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

async function readDesktopState(page) {
  return page.evaluate(async () => {
    const token = document.querySelector('meta[name="cg-signal-api-token"]').content;
    const response = await fetch("/api/state", {
      headers: { "X-CG-Signal-Token": token },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`State read failed (${response.status})`);
    return response.json();
  });
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

test("desktop state controls wait for authoritative recovery", async ({ page }) => {
  const guards = await installBrowserGuards(page);
  const statePosts = [];
  let abortedInitialStateGet = false;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/state") && request.method() === "POST") {
      statePosts.push(request.postDataJSON());
    }
  });
  await page.route("**/api/state", async (route) => {
    if (!abortedInitialStateGet && route.request().method() === "GET") {
      abortedInitialStateGet = true;
      await route.abort();
      return;
    }
    await route.continue();
  });
  await page.goto(`${fixtureUrls.desktop_url}?state_fault=1`, { waitUntil: "domcontentloaded" });

  await expect(page.locator("#user-state-status")).toContainText("Retry");
  await expect(page.locator('[data-id="smoke-unreal-article"] [data-save-id]')).toBeDisabled();
  await expect(page.locator("#reset-sources")).toBeDisabled();
  await page.locator('[data-id="smoke-unreal-article"] .source-menu summary').click();
  await expect(page.locator('[data-id="smoke-unreal-article"] [data-source-action]')).toBeDisabled();
  await page.keyboard.press("j");
  await page.keyboard.press("s");
  await page.locator('[data-id="smoke-unreal-article"] [data-save-id]').dispatchEvent("click");
  await page.locator('[data-id="smoke-unreal-article"] [data-source-action]').dispatchEvent("click");
  await page.locator("#reset-sources").dispatchEvent("click");
  await expect.poll(() => statePosts.length).toBe(0);

  await page.locator("[data-retry-user-state]").click();
  await expect(page.locator("#user-state-status")).toBeHidden();
  await expect(page.locator('[data-id="smoke-blender-article"] [data-save-id]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-source-id="smoke-unreal-source"]')).toHaveClass(/is-source-muted/);

  // The muted source is intentionally absent from Latest Signal. Restore it
  // through the recovered state UI, add a saved story, then mute it again so
  // the final merged write proves both seeded values survived the recovery.
  const restorePost = page.waitForRequest(
    (request) => request.url().endsWith("/api/state") && request.method() === "POST",
  );
  await page.locator('[data-source-id="smoke-unreal-source"]').dispatchEvent("click");
  await restorePost;
  await expect(page.locator('[data-id="smoke-unreal-article"] [data-save-id]')).toBeVisible();
  const statePost = page.waitForRequest((request) => {
    if (!request.url().endsWith("/api/state") || request.method() !== "POST") return false;
    const postedState = request.postDataJSON();
    return postedState.saved?.includes("smoke-unreal-article")
      && postedState.muted_sources?.includes("smoke-unreal-source");
  });
  await page.locator('[data-id="smoke-unreal-article"] [data-save-id]').click();
  await page.locator('[data-id="smoke-unreal-article"] .source-menu summary').click();
  await page.locator('[data-id="smoke-unreal-article"] [data-source-action="mute"]').click();
  expect((await statePost).postDataJSON()).toMatchObject({
    saved: expect.arrayContaining(["smoke-blender-article", "smoke-unreal-article"]),
    muted_sources: ["smoke-unreal-source"],
  });
  await expectCleanBrowser(guards);
});

test("desktop state writes serialize and coalesce the latest generation", async ({ page }) => {
  const guards = await installBrowserGuards(page);
  const postBodies = [];
  let heldFirstPost = null;
  await page.route("**/api/state", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    postBodies.push(route.request().postDataJSON());
    if (!heldFirstPost) {
      heldFirstPost = route;
      return;
    }
    await route.continue();
  });
  await page.goto(`${fixtureUrls.desktop_url}?state_control=1`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#user-state-status")).toBeHidden();
  await expect(page.locator("#story-grid .story-card")).toHaveCount(2);

  await page.locator('[data-id="smoke-unreal-article"] [data-save-id]').click();
  await expect.poll(() => postBodies.length).toBe(1);
  await page.locator('[data-id="smoke-blender-article"] [data-save-id]').click();
  await page.locator('[data-id="smoke-unreal-article"] .source-menu summary').click();
  await page.locator('[data-id="smoke-unreal-article"] [data-source-action="mute"]').click();
  await page.waitForTimeout(350);
  expect(postBodies).toHaveLength(1);

  await heldFirstPost.continue();
  await expect.poll(() => postBodies.length).toBe(2);
  expect(postBodies[1]).toEqual({
    saved: ["smoke-unreal-article"],
    muted_sources: ["smoke-unreal-source"],
  });
  await expect.poll(async () => (await readDesktopState(page)).saved).toEqual(["smoke-unreal-article"]);
  expect(await readDesktopState(page)).toMatchObject({ muted_sources: ["smoke-unreal-source"] });
  await expectCleanBrowser(guards);
});

test("desktop state save failure keeps latest state for accessible retry", async ({ page }) => {
  const guards = await installBrowserGuards(page);
  let abortedPost = false;
  await page.route("**/api/state", async (route) => {
    if (!abortedPost && route.request().method() === "POST") {
      abortedPost = true;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });
  await page.goto(`${fixtureUrls.desktop_url}?state_failure=1`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#user-state-status")).toBeHidden();
  await page.locator('[data-id="smoke-unreal-article"] [data-save-id]').click();
  await expect(page.locator("#user-state-status")).toContainText("Changes not saved");
  await expect(page.locator("[data-retry-user-state-save]")).toBeVisible();
  expect(await readDesktopState(page)).toMatchObject({
    saved: ["smoke-blender-article"],
    muted_sources: [],
  });

  await page.locator("[data-retry-user-state-save]").click();
  await expect(page.locator("#user-state-status")).toBeHidden();
  expect(await readDesktopState(page)).toMatchObject({
    saved: ["smoke-blender-article", "smoke-unreal-article"],
    muted_sources: [],
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator("#user-state-status")).toBeHidden();
  await expect(page.locator('[data-id="smoke-unreal-article"] [data-save-id]')).toHaveAttribute("aria-pressed", "true");
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
    const thumbnail = page.locator("#story-list .story-card").first().locator(".story-image img");
    await expect(thumbnail).toBeVisible();
    await expect.poll(() => thumbnail.evaluate((image) => ({
      complete: image.complete,
      naturalWidth: image.naturalWidth,
      sameOrigin: new URL(image.currentSrc).origin === window.location.origin,
      bundledPath: new URL(image.currentSrc).pathname.includes("/thumbnails/"),
    }))).toEqual({ complete: true, naturalWidth: expect.any(Number), sameOrigin: true, bundledPath: true });
    expect((await thumbnail.evaluate((image) => image.naturalWidth))).toBeGreaterThan(0);

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
