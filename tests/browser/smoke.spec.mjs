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

function expandedMobilePayload(payload, count = 120) {
  const now = Date.now();
  const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1).getTime();
  return {
    ...payload,
    articles: Array.from({ length: count }, (_, index) => ({
      ...payload.articles[index % payload.articles.length],
      id: `mobile-story-${String(index).padStart(3, "0")}`,
      title: `${index % 2 ? "Blender" : "Unreal"} story ${index}`,
      url: `https://example.test/story-${index}`,
      published_at: new Date(Math.max(monthStart, now - (index + 5) * 60_000)).toISOString(),
      image: index === 0 ? payload.articles[0].image : "",
    })),
  };
}

async function cardTop(page, id) {
  return page.locator(`[data-article-id="${id}"]`).evaluate((card) => card.getBoundingClientRect().top);
}

test("mobile incrementally renders compact cards and stages updates without moving the reader", async ({ browser }) => {
  const context = await browser.newContext({ ...PIXEL_7, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    const guards = await installBrowserGuards(page);
    const fixture = await (await page.request.get(`${fixtureUrls.mobile_url}feed.json`)).json();
    let payload = expandedMobilePayload(fixture);
    await page.route("**/feed.json", (route) => route.fulfill({ json: payload }));
    await page.goto(fixtureUrls.mobile_url, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#story-list")).toHaveClass(/is-compact/);
    await expect(page.locator("#story-list .story-card")).toHaveCount(40);
    await page.evaluate(() => { window.originalFirstCard = document.querySelector(".story-card"); });
    await page.locator("#more-stories").scrollIntoViewIfNeeded();
    await expect(page.locator("#story-list .story-card")).toHaveCount(80);
    expect(await page.evaluate(() => window.originalFirstCard === document.querySelector(".story-card"))).toBe(true);

    await page.locator('[data-article-id="mobile-story-020"]').evaluate((card) => card.scrollIntoView({ block: "start", behavior: "instant" }));
    const beforePin = await cardTop(page, "mobile-story-020");
    await page.locator('[data-pin-id="mobile-story-020"]').click();
    await expect(page.locator('[data-pin-id="mobile-story-020"]')).toHaveAttribute("aria-pressed", "true");
    expect(Math.abs(await cardTop(page, "mobile-story-020") - beforePin)).toBeLessThan(3);
    expect(await page.evaluate(() => window.originalFirstCard === document.querySelector(".story-card"))).toBe(true);

    payload.articles[20] = { ...payload.articles[20], title: "Updated existing Unreal story" };
    await page.locator("#refresh-feed").dispatchEvent("click");
    await expect(page.locator('[data-article-id="mobile-story-020"]')).toContainText("Updated existing Unreal story");
    expect(Math.abs(await cardTop(page, "mobile-story-020") - beforePin)).toBeLessThan(3);
    expect(await page.evaluate(() => window.originalFirstCard === document.querySelector(".story-card"))).toBe(true);
    await expect(page.locator("#new-stories")).toBeHidden();
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    await page.evaluate(() => window.dispatchEvent(new Event("offline")));
    await expect(page.locator("#update-status")).toContainText("Offline copy");
    expect(Math.abs(await cardTop(page, "mobile-story-020") - beforePin)).toBeLessThan(3);

    payload = {
      ...payload,
      articles: [...Array.from({ length: 3 }, (_, index) => ({
        ...payload.articles[1], id: `new-story-${index}`, title: `New Blender story ${index}`,
        url: `https://example.test/new-${index}`, published_at: new Date(Date.now() - index * 1000).toISOString(),
      })), ...payload.articles],
    };
    // The header refresh control is above the reading position; dispatching its
    // action avoids test-driver auto-scrolling before the update arrives.
    const beforeRefresh = await cardTop(page, "mobile-story-020");
    await page.locator("#refresh-feed").dispatchEvent("click");
    await expect(page.locator("#new-stories")).toContainText("3 new stories");
    await expect(page.locator("#story-list .story-card")).toHaveCount(80);
    await expect(page.locator("#story-list")).not.toContainText("New Blender story");
    expect(Math.abs(await cardTop(page, "mobile-story-020") - beforeRefresh)).toBeLessThan(3);
    expect(await page.evaluate(() => window.originalFirstCard === document.querySelector(".story-card"))).toBe(true);
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    await page.locator("#refresh-feed").dispatchEvent("click");
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    await expect(page.locator("#new-stories")).toContainText("3 new stories");

    await page.locator("#filter-drawer-handle").click();
    await page.locator('[data-category="Unreal Engine"]').click();
    await expect(page.locator("#new-stories")).toBeHidden();
    await page.locator('[data-category="All"]').click();
    await expect(page.locator("#new-stories")).toContainText("3 new stories");
    await page.locator("#filter-drawer-handle").click();
    await page.locator("#new-stories").click();
    await expect(page.locator("#new-stories")).toBeHidden();
    await expect(page.locator("#story-list .story-card")).toHaveCount(40);
    await expect(page.locator("#story-list .story-card").first()).toContainText("New Blender story 0");
    await expect(page.locator('[data-pin-id="mobile-story-020"]')).toHaveAttribute("aria-pressed", "true");
    expect(await cardTop(page, "new-story-0")).toBeGreaterThan(0);
    expect(await cardTop(page, "new-story-0")).toBeLessThan(200);
    payload = {
      ...payload, generated_at: new Date(Date.parse(payload.generated_at) - 60_000).toISOString(),
      articles: payload.articles.filter((article) => !article.id.startsWith("new-story-")),
    };
    await page.locator("#refresh-feed").dispatchEvent("click");
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    await expect(page.locator("#story-list .story-card").first()).toContainText("New Blender story 0");
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
});

test("mobile preserves filters and reading anchors through view changes and reload", async ({ browser }) => {
  const context = await browser.newContext({ ...PIXEL_7, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    const guards = await installBrowserGuards(page);
    const fixture = await (await page.request.get(`${fixtureUrls.mobile_url}feed.json`)).json();
    const payload = expandedMobilePayload(fixture);
    await page.addInitScript(() => { delete window.IntersectionObserver; });
    await page.route("**/feed.json", (route) => route.fulfill({ json: payload }));
    await page.goto(fixtureUrls.mobile_url, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#story-list .story-card")).toHaveCount(40);
    await page.locator("#more-stories").click();
    await expect(page.locator("#story-list .story-card")).toHaveCount(80);
    await expect(page.locator('[data-article-id="mobile-story-040"] h3 a')).toBeFocused();
    await page.locator('[data-article-id="mobile-story-055"]').evaluate((card) => card.scrollIntoView({ block: "start", behavior: "instant" }));
    await page.locator('[data-pin-id="mobile-story-055"]').click();
    const beforeFilter = await cardTop(page, "mobile-story-055");
    // Search uses the same filtering path as typing, without moving focus away
    // from the article before the anchor-preservation assertion.
    await page.locator("#search-input").evaluate((input) => { input.value = "Blender story"; input.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(Math.abs(await cardTop(page, "mobile-story-055") - beforeFilter)).toBeLessThan(3);
    await expect(page.locator("#story-list .story-card")).toHaveCount(40);
    const latestTop = await cardTop(page, "mobile-story-055");
    await page.locator('[data-view="pinned"]').click();
    await expect(page.locator("#story-list .story-card")).toHaveCount(1);
    await page.locator('[data-view="latest"]').click();
    expect(Math.abs(await cardTop(page, "mobile-story-055") - latestTop)).toBeLessThan(3);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator("#search-input")).toHaveValue("Blender story");
    await expect(page.locator('[data-pin-id="mobile-story-055"]')).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => Math.abs(await cardTop(page, "mobile-story-055") - latestTop)).toBeLessThan(3);
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
});

test("mobile cleans legacy cached events and checks for fresh articles without losing preferences", async ({ browser }) => {
  const context = await browser.newContext({ ...PIXEL_7, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    const guards = await installBrowserGuards(page);
    const response = await page.request.get(`${fixtureUrls.mobile_url}feed.json`);
    const payload = await response.json();
    const event = {
      ...payload.articles[0], id: "legacy-calendar", title: "gamescom", source_id: "game-developer",
      published_at: "2099-08-23T07:00:00Z", url: "https://www.gamedeveloper.com/events/gamescom",
    };
    const cached = { ...payload, generated_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(), articles: [event, ...payload.articles] };
    let allowNetwork = false;
    let feedRequests = 0;
    const freshArticle = { ...payload.articles[0], id: "fresh-article", title: "Fresh production news", url: "https://example.test/fresh" };
    await page.addInitScript((offline) => {
      localStorage.setItem("cg-signal-mobile:last-feed", JSON.stringify(offline));
      localStorage.setItem("cg-signal-mobile:pinned", JSON.stringify(["smoke-blender-article"]));
      localStorage.setItem("cg-signal-mobile:density", "comfortable");
      localStorage.setItem("cg-signal-mobile:disabled-sources", JSON.stringify(["hidden-source"]));
    }, cached);
    await page.route("**/feed.json", async (route) => {
      feedRequests += 1;
      if (!allowNetwork) return route.abort();
      return route.fulfill({ json: { ...payload, articles: [event, freshArticle, ...payload.articles] } });
    });
    await page.goto(fixtureUrls.mobile_url, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);
    await expect(page.locator("#story-list")).not.toContainText("gamescom");
    await expect(page.locator("#notice")).toContainText("Feed refresh is delayed");
    await expect(page.locator("#story-list")).not.toHaveClass(/is-compact/);
    await expect(page.locator("#update-status")).toContainText("Offline copy");
    await expect(page.locator("#pinned-total")).toHaveText("1");

    allowNetwork = true;
    await page.locator("#refresh-feed").click();
    await expect(page.locator("#new-stories")).toContainText("1 new story");
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);
    await page.locator("#new-stories").click();
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(3);
    await expect(page.locator("#story-list")).toContainText("Fresh production news");
    await expect(page.locator("#story-list")).not.toContainText("gamescom");
    await expect(page.locator("#notice")).toBeHidden();
    await expect(page.locator("#update-status")).toContainText("Feed published");
    await expect(page.locator("#update-status")).toContainText("newest story");
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    expect(feedRequests).toBe(2);
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem("cg-signal-mobile:disabled-sources")))).toEqual(["hidden-source"]);
    await expect(page.locator("#pinned-total")).toHaveText("1");
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
});

test("mobile bounds a stalled refresh and coalesces overlapping checks", async ({ browser }) => {
  const context = await browser.newContext({ ...PIXEL_7, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    const guards = await installBrowserGuards(page);
    const payload = await (await page.request.get(`${fixtureUrls.mobile_url}feed.json`)).json();
    await page.addInitScript((cached) => localStorage.setItem("cg-signal-mobile:last-feed", JSON.stringify(cached)), payload);
    let requests = 0;
    let held = null;
    await page.route("**/feed.json", async (route) => {
      requests += 1;
      if (requests === 1) { held = route; return; }
      await route.fulfill({ json: payload });
    });
    await page.clock.install({ time: Date.now() });
    await page.goto(fixtureUrls.mobile_url, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);
    await expect(page.locator("#refresh-feed")).toBeDisabled();
    await expect.poll(() => requests).toBe(1);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    expect(requests).toBe(1);
    await page.clock.fastForward("00:21");
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    await expect(page.locator("#notice")).toContainText("Live refresh unavailable");
    await held.abort().catch(() => {});
    await page.locator("#refresh-feed").click();
    await expect(page.locator("#notice")).toBeHidden();
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    expect(requests).toBe(2);
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
});

test("mobile periodically checks the feed while it remains visible", async ({ browser }) => {
  const context = await browser.newContext({ ...PIXEL_7, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    const guards = await installBrowserGuards(page);
    const payload = await (await page.request.get(`${fixtureUrls.mobile_url}feed.json`)).json();
    let requests = 0;
    await page.route("**/feed.json", async (route) => {
      requests += 1;
      const articles = requests === 1 ? payload.articles : [
        { ...payload.articles[0], id: "automatic-refresh", title: "Automatically refreshed story", url: "https://example.test/automatic" },
        ...payload.articles,
      ];
      await route.fulfill({ json: { ...payload, articles } });
    });
    await page.clock.install({ time: Date.now() });
    await page.goto(fixtureUrls.mobile_url, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);
    await expect(page.locator("#refresh-feed")).toBeEnabled();
    await page.clock.fastForward("06:00");
    await expect(page.locator("#new-stories")).toContainText("1 new story");
    await expect(page.locator("#story-list")).not.toContainText("Automatically refreshed story");
    await page.locator("#new-stories").click();
    await expect(page.locator("#story-list")).toContainText("Automatically refreshed story");
    expect(requests).toBe(2);
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
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
    await expect(page.locator('[data-view="pinned"]')).toHaveAttribute("aria-current", "page");
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(1);
    await page.locator('[data-view="latest"]').click();
    await expect(page.locator("#story-list")).toHaveAttribute("aria-busy", "false");
    await expect(page.locator("#story-list .story-card:not(.skeleton)")).toHaveCount(2);
    await expect(page.locator("#pinned-total")).toHaveText("1");
    await expect(page.locator("#update-status")).toContainText("Offline copy");
    await expect(page.locator("#notice")).toContainText("Live refresh unavailable");
    await expect.poll(() => page.evaluate(() => ({
      controlled: Boolean(navigator.serviceWorker.controller),
      feedCached: Boolean(document.querySelector("#story-list .story-card:not(.skeleton)")),
    }))).toEqual({ controlled: true, feedCached: true });
    await expectCleanBrowser(guards);
  } finally {
    await context.close();
  }
});
