import { test, expect, devices } from "@playwright/test";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const HELPER_PATH = join(dirname(fileURLToPath(import.meta.url)), "serve_smoke.py");
const PYTHON = process.env.PYTHON || "python";
const STARTUP_TIMEOUT_MS = 15_000;
const SHUTDOWN_TIMEOUT_MS = 10_000;
const { defaultBrowserType: _defaultBrowserType, ...PIXEL_7 } = devices["Pixel 7"];

let helper;
let fixtureUrls;
let helperStderr = "";

function startFixtureServers() {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(PYTHON, [HELPER_PATH], {
      cwd: PROJECT_ROOT,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    helper = child;
    let stdout = "";
    let settled = false;
    const startupTimer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      rejectPromise(new Error(`Timed out starting smoke fixture servers.\n${helperStderr}`));
    }, STARTUP_TIMEOUT_MS);

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      helperStderr += chunk;
      if (helperStderr.length > 20_000) helperStderr = helperStderr.slice(-20_000);
    });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      const newline = stdout.indexOf("\n");
      if (newline < 0 || settled) return;
      const line = stdout.slice(0, newline).trim();
      try {
        const parsed = JSON.parse(line);
        if (!parsed.desktop_url || !parsed.mobile_url) throw new Error("fixture URLs are missing");
        settled = true;
        clearTimeout(startupTimer);
        resolvePromise(parsed);
      } catch (error) {
        settled = true;
        clearTimeout(startupTimer);
        child.kill();
        rejectPromise(new Error(`Invalid smoke fixture startup line: ${line}\n${error}\n${helperStderr}`));
      }
    });
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(startupTimer);
      rejectPromise(new Error(`Could not start smoke fixture helper: ${error}\n${helperStderr}`));
    });
    child.once("exit", (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(startupTimer);
      rejectPromise(new Error(`Smoke fixture helper exited before startup (${code ?? signal}).\n${helperStderr}`));
    });
  });
}

async function stopFixtureServers() {
  if (!helper) return;
  const child = helper;
  helper = null;
  child.stdin.end("\n");
  await new Promise((resolvePromise) => {
    let finished = false;
    const timer = setTimeout(() => {
      if (finished) return;
      finished = true;
      child.kill();
      resolvePromise();
    }, SHUTDOWN_TIMEOUT_MS);
    child.once("exit", () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolvePromise();
    });
  });
}

async function installBrowserGuards(page) {
  const pageErrors = [];
  const unexpectedHttp = [];
  page.on("pageerror", (error) => pageErrors.push(error));
  await page.context().route("**/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    if (["http:", "https:"].includes(requestUrl.protocol) && requestUrl.hostname !== "127.0.0.1") {
      unexpectedHttp.push(requestUrl.href);
      await route.abort();
      return;
    }
    await route.continue();
  });
  return { pageErrors, unexpectedHttp };
}

async function expectCleanBrowser(guards) {
  expect(guards.unexpectedHttp, `unexpected HTTP(S) requests: ${guards.unexpectedHttp.join(", ")}`).toEqual([]);
  expect(guards.pageErrors.map((error) => error.stack || error.message), "page errors").toEqual([]);
}

test.beforeAll(async () => {
  fixtureUrls = await startFixtureServers();
});

test.afterAll(async () => {
  await stopFixtureServers();
});

test("desktop dashboard serves and persists the fixture workflow", async ({ page }) => {
  const guards = await installBrowserGuards(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem("cg-signal:legacy-state", "must be removed");
    localStorage.setItem("cg-signal:theme", "paper");
  });
  await page.goto(fixtureUrls.desktop_url, { waitUntil: "domcontentloaded" });

  const token = await page.locator('meta[name="cg-signal-api-token"]').getAttribute("content");
  expect(token).toBeTruthy();
  expect(token).not.toBe("__CG_SIGNAL_API_TOKEN__");
  await expect(page.locator("#stories")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator("#story-grid .story-card")).toHaveCount(2);
  await expect(page.locator("#story-grid .skeleton-card")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("cg-signal:legacy-state"))).toBeNull();

  const cards = page.locator("#story-grid .story-card");
  await page.locator("#search-input").fill("Unreal");
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Unreal Engine Rendering Techniques");
  await page.locator("#search-input").fill("");
  await expect(cards).toHaveCount(2);

  const stateResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/state") && response.request().method() === "POST",
  );
  await page.locator('[data-id="smoke-blender-article"] [data-save-id="smoke-blender-article"]').click();
  expect((await stateResponse).status()).toBe(200);
  if (await page.locator("#sidebar-toggle").getAttribute("aria-expanded") === "false") {
    await page.locator("#sidebar-toggle").click();
  }
  await expect(page.locator("#sidebar-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#desktop-sidebar")).toBeVisible();
  await page.locator('[data-view="saved"]').click();
  await expect(page.locator("#stories")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator('[data-id="smoke-blender-article"]')).toContainText("Blender Lighting Workflow");
  await expect(page.locator('textarea[data-note-id="smoke-blender-article"]')).toHaveCount(0);
  await expectCleanBrowser(guards);
});

test("mobile feed survives an offline reload through its service worker", async ({ browser }) => {
  const context = await browser.newContext({ ...PIXEL_7, serviceWorkers: "allow" });
  const page = await context.newPage();
  try {
    const guards = await installBrowserGuards(page);
    await page.goto(fixtureUrls.mobile_url, { waitUntil: "domcontentloaded" });
    await expect.poll(() => page.evaluate(() => ({
      touch: navigator.maxTouchPoints > 0,
      coarse: matchMedia("(pointer: coarse)").matches,
      mobile: matchMedia("(max-width: 700px)").matches,
    }))).toMatchObject({ touch: true, coarse: true, mobile: true });
    await expect(page.locator("#story-list")).toHaveAttribute("aria-busy", "false");
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);

    await page.locator('[data-pin-id="smoke-blender-article"]').click();
    await expect(page.locator("#pinned-total")).toHaveText("1");
    await page.locator('[data-view="pinned"]').click();
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(1);
    await expect(page.locator("#story-list")).toContainText("Blender Lighting Workflow");

    await page.evaluate(async () => {
      await navigator.serviceWorker.ready;
      if (!navigator.serviceWorker.controller) {
        await new Promise((resolvePromise) => {
          navigator.serviceWorker.addEventListener("controllerchange", resolvePromise, { once: true });
        });
      }
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect.poll(() => page.evaluate(async () => {
      for (const key of await caches.keys()) {
        if (await (await caches.open(key)).match(new URL("./feed.json", location.href))) return true;
      }
      return false;
    })).toBe(true);

    await page.evaluate(() => localStorage.removeItem("cg-signal-mobile:last-feed"));
    const offlineFeedResponses = [];
    const recordOfflineFeed = (response) => {
      if (response.url().endsWith("/feed.json")) {
        offlineFeedResponses.push({ status: response.status(), fromServiceWorker: response.fromServiceWorker() });
      }
    };
    page.on("response", recordOfflineFeed);
    await page.context().setOffline(true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect.poll(() => offlineFeedResponses.some(
      (response) => response.status === 200 && response.fromServiceWorker,
    )).toBe(true);
    page.off("response", recordOfflineFeed);
    await expect(page.locator("#story-list")).toHaveAttribute("aria-busy", "false");
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);
    await expect(page.locator("#pinned-total")).toHaveText("1");
    await expect.poll(() => page.evaluate(() => ({
      controlled: Boolean(navigator.serviceWorker.controller),
      feedCached: Boolean(document.querySelector("#story-list .story-card:not(.skeleton)")),
    }))).toEqual({ controlled: true, feedCached: true });
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
});
