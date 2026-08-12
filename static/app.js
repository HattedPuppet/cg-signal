import {
  FEED_SCHEMA_VERSION,
  ARTICLE_LANE_VALUES,
  LANE_VALUES,
  SOFTWARE_GROUP_ORDER,
  SOFTWARE_GROUP_COLORS,
  TOPIC_ORDER,
  TOPIC_COLORS,
  normalizeSoftwareCategory,
  softwareGroup,
  articleCategories as articleSoftwareCategories,
  articleTopics,
  matchesSearch as matchesSearchQuery,
  articleWithinPublicationWindow,
  feedPayloadIsStructurallyCompatible,
  thumbnailReferenceIsValid,
} from "./domain.mjs";

const apiToken = document.querySelector('meta[name="cg-signal-api-token"]')?.content || "";

async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-CG-Signal-Token", apiToken);
  return fetch(url, {
    cache: "no-store",
    ...options,
    headers,
  });
}

const storageKeys = {
  theme: "cg-signal:theme",
  layout: "cg-signal:layout",
  lane: "cg-signal:lane",
  software: "cg-signal:software",
  topics: "cg-signal:topics",
  lastVisit: "cg-signal:last-visit",
  timeWindow: "cg-signal:time-window",
  sidebar: "cg-signal:sidebar",
};

const presentationStorageKeys = new Set(Object.values(storageKeys));
for (let index = localStorage.length - 1; index >= 0; index -= 1) {
  const key = localStorage.key(index);
  if (key?.startsWith("cg-signal:") && !presentationStorageKeys.has(key)) {
    localStorage.removeItem(key);
  }
}

const TIME_WINDOW_LABELS = {
  month: "This month",
  quarter: "Last 3 months",
  all: "All current",
};

const storedLane = localStorage.getItem(storageKeys.lane);

const state = {
  payload: null,
  articles: [],
  historyArticles: [],
  historyTotal: 0,
  historyHasMore: false,
  historyLoading: false,
  historyRequestId: 0,
  managedSources: [],
  activeSources: new Set(),
  lane: LANE_VALUES.has(storedLane) ? storedLane : "All",
  software: readFilterSet(storageKeys.software),
  topics: readFilterSet(storageKeys.topics),
  view: "all",
  search: "",
  saved: new Set(),
  mutedSources: new Set(),
  layout: localStorage.getItem(storageKeys.layout) || "grid",
  timeWindow: Object.prototype.hasOwnProperty.call(TIME_WINDOW_LABELS, localStorage.getItem(storageKeys.timeWindow))
    ? localStorage.getItem(storageKeys.timeWindow)
    : "month",
  sidebarOpen: localStorage.getItem(storageKeys.sidebar) !== "0",
  visibleArticleIds: [],
  keyboardArticleId: null,
  knownArticleIds: new Set(),
  knownSourceIds: new Set(),
  firstFeedLoaded: false,
  sessionCutoff: parseStoredDate(localStorage.getItem(storageKeys.lastVisit)),
  sessionStartedAt: new Date().toISOString(),
};

let stateSaveTimer = null;
let backgroundRefreshTimer = null;
let feedRefreshWaitPending = false;
let thumbnailRefreshWaitPending = false;
let thumbnailRefreshRetryTimer = null;
let thumbnailRefreshRetryDelay = 750;
let historySearchTimer = null;
const THUMBNAIL_REFRESH_RETRY_MAX_MS = 10_000;

const elements = {
  appShell: document.querySelector(".app-shell"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  grid: document.querySelector("#story-grid"),
  stories: document.querySelector("#stories"),
  empty: document.querySelector("#empty-state"),
  sourceFilters: document.querySelector("#source-filters"),
  softwareFilterGroup: document.querySelector("#software-filter-group"),
  softwareFilters: document.querySelector("#software-filters"),
  topicFilterGroup: document.querySelector("#topic-filter-group"),
  topicFilters: document.querySelector("#topic-filters"),
  visibleCount: document.querySelector("#visible-count"),
  newSince: document.querySelector("#new-since"),
  allCount: document.querySelector("#all-count"),
  savedCount: document.querySelector("#saved-count"),
  historyCount: document.querySelector("#history-count"),
  lastUpdated: document.querySelector("#last-updated"),
  home: document.querySelector("#home-button"),
  search: document.querySelector("#search-input"),
  searchHelp: document.querySelector("#search-help"),
  scrollTop: document.querySelector("#scroll-top-button"),
  refresh: document.querySelector("#refresh-button"),
  layout: document.querySelector("#layout-toggle"),
  notice: document.querySelector("#notice"),
  sortLabel: document.querySelector("#sort-label"),
  manageSources: document.querySelector("#manage-sources"),
  sourceManagerPanel: document.querySelector("#source-manager-panel"),
  sourceManagerClose: document.querySelector("#source-manager-close"),
  sourceForm: document.querySelector("#source-form"),
  sourceFeedUrl: document.querySelector("#source-feed-url"),
  sourceName: document.querySelector("#source-name"),
  sourceSiteUrl: document.querySelector("#source-site-url"),
  testFeedUrl: document.querySelector("#test-feed-url"),
  sourceFormStatus: document.querySelector("#source-form-status"),
  managedSourceList: document.querySelector("#managed-source-list"),
  configuredSourceCount: document.querySelector("#configured-source-count"),
};

const sourceShortNames = {
  "80-level": "80",
  cgworld: "CG",
  gamemakers: "GM",
  "3dnchu": "3D",
  cginterest: "CI",
  "befores-afters": "B&A",
  "game-developer": "GD",
  "cartoon-brew": "CB",
  siggraph: "SG",
  gamebusiness: "GB",
  "automaton-interviews": "AU",
  "unreal-engine": "UE",
  "blender-developers": "BL",
};

function parseStoredDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function readFilterSet(key) {
  const stored = localStorage.getItem(key);
  if (!stored || stored === "All") return new Set();
  try {
    const values = JSON.parse(stored);
    const first = Array.isArray(values) ? values.find((value) => value && value !== "All") : null;
    const normalized = normalizeSoftwareCategory(first);
    return new Set(normalized ? [normalized] : []);
  } catch {
    const normalized = normalizeSoftwareCategory(stored);
    return new Set(normalized ? [normalized] : []);
  }
}

function persistFilterSet(key, values) {
  localStorage.setItem(key, JSON.stringify([...values]));
}

function chooseSingleFilter(selected, value) {
  if (value === "All" || selected.has(value)) {
    selected.clear();
    return;
  }
  selected.clear();
  selected.add(value);
}

async function persistUserState() {
  try {
    const response = await apiFetch("/api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        saved: [...state.saved],
        muted_sources: [...state.mutedSources],
      }),
    });
    if (!response.ok) throw new Error(`State save failed (${response.status})`);
  } catch (error) {
    console.warn("CG Signal could not save local state.", error);
  }
}

function queueUserStateSave() {
  window.clearTimeout(stateSaveTimer);
  stateSaveTimer = window.setTimeout(persistUserState, 140);
}

async function loadUserState() {
  try {
    const response = await apiFetch("/api/state");
    if (!response.ok) throw new Error(`State request failed (${response.status})`);
    const stored = await response.json();
    state.saved = new Set(Array.isArray(stored.saved) ? stored.saved : []);
    state.mutedSources = new Set(Array.isArray(stored.muted_sources) ? stored.muted_sources : []);
  } catch (error) {
    console.warn("CG Signal could not load local state.", error);
  }
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value = "") {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
  } catch {
    return "#";
  }
}

function relativeTime(value) {
  const date = new Date(value);
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const intervals = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of intervals) {
    if (Math.abs(seconds) >= size || unit === "minute") {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return "just now";
}

function trimSummary(value = "") {
  const clean = value.replace(/\s+/g, " ").trim();
  if (!clean) return "Open the original article for the full story and production details.";
  return clean.length > 230 ? `${clean.slice(0, 227).trim()}…` : clean;
}

function chronologicalSort(left, right) {
  return new Date(right.published_at).getTime() - new Date(left.published_at).getTime()
    || String(left.id || "").localeCompare(String(right.id || ""));
}

function safeImageUrl(value = "") {
  if (typeof value !== "string" || value === "" || !thumbnailReferenceIsValid(value)) return "#";
  try {
    const url = new URL(value, document.baseURI);
    if (url.origin !== window.location.origin) return "#";
    return url.href;
  } catch {
    return "#";
  }
}

function articleWithinTimeWindow(article) {
  return articleWithinPublicationWindow(article, state.timeWindow);
}

function articleMonthKey(article) {
  const published = new Date(article.published_at);
  if (Number.isNaN(published.getTime())) return "";
  return `${published.getFullYear()}-${published.getMonth()}`;
}

function articleMonthLabel(article) {
  const published = new Date(article.published_at);
  if (Number.isNaN(published.getTime())) return "Publication date unavailable";
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(published);
}

function matchesSource(article) {
  return article.sources.some(
    (source) => state.activeSources.has(source.id) && !state.mutedSources.has(source.id),
  );
}

function articleIsNew(article) {
  return Boolean(state.sessionCutoff)
    && new Date(article.published_at).getTime() > state.sessionCutoff.getTime();
}

function matchesSearch(article, query) {
  return matchesSearchQuery(article, query, {
    isStatus: (item, value) => ({
      saved: state.saved.has(item.id),
      library: state.saved.has(item.id),
      new: articleIsNew(item),
    })[value] || false,
  });
}

function latestPool() {
  const query = state.search;
  return state.articles.filter((article) => {
    if (!articleWithinTimeWindow(article)) return false;
    if (!matchesSource(article)) return false;
    if (state.lane !== "All" && (article.lane || "Tech & Development") !== state.lane) return false;
    return matchesSearch(article, query);
  }).sort(chronologicalSort);
}

function productionTopicsActive() {
  return state.software.size === 1 && state.software.has("Production techniques");
}

function matchesSelection(values, selected) {
  return selected.size === 0 || values.some((value) => selected.has(value));
}

function matchesSoftware(article) {
  return matchesSelection(articleSoftwareCategories(article), state.software);
}

function matchesTopics(article) {
  return matchesSelection(articleTopics(article), state.topics);
}

function applyFacetFilters(articles) {
  return articles.filter((article) => matchesSoftware(article) && matchesTopics(article));
}

function usesHistoryView() {
  return state.view === "history" || state.view === "saved";
}

function historyViewQuery() {
  const status = state.view === "saved" ? "#is:saved" : "";
  return [state.search.trim(), status].filter(Boolean).join(" ");
}

function historySourceFilter() {
  const enabledIds = (state.payload?.sources || []).map((source) => source.id);
  const selectedIds = enabledIds.filter(
    (sourceId) => state.activeSources.has(sourceId) && !state.mutedSources.has(sourceId),
  );
  if (selectedIds.length === enabledIds.length) return [];
  return selectedIds.length ? selectedIds : ["__none__"];
}

async function loadHistory({ append = false } = {}) {
  if (!state.payload || !usesHistoryView()) return;
  const requestId = ++state.historyRequestId;
  const offset = append ? state.historyArticles.length : 0;
  if (!append) {
    state.historyArticles = [];
    state.historyTotal = 0;
    state.historyHasMore = false;
  }
  state.historyLoading = true;
  render();
  const parameters = new URLSearchParams({
    q: historyViewQuery(),
    lane: state.lane,
    limit: "60",
    offset: String(offset),
  });
  const sourceIds = historySourceFilter();
  if (sourceIds.length) parameters.set("sources", sourceIds.join(","));
  if (state.sessionCutoff) parameters.set("new_after", state.sessionCutoff.toISOString());
  try {
    const response = await apiFetch(`/api/history?${parameters}`);
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.detail || payload.error || `History request failed (${response.status})`);
    if (requestId !== state.historyRequestId) return;
    state.historyArticles = append
      ? [...state.historyArticles, ...(payload.articles || [])]
      : (payload.articles || []);
    state.historyTotal = payload.total || 0;
    state.historyHasMore = Boolean(payload.has_more);
    if (elements.historyCount) elements.historyCount.textContent = payload.history_count ?? state.historyTotal;
  } catch (error) {
    if (requestId !== state.historyRequestId) return;
    elements.notice.textContent = `Article history could not be searched. ${error.message}`;
    elements.notice.hidden = false;
  } finally {
    if (requestId === state.historyRequestId) {
      state.historyLoading = false;
      render();
    }
  }
}

function filteredArticles() {
  if (state.view === "all") {
    return applyFacetFilters(latestPool());
  }

  if (usesHistoryView()) {
    return state.historyArticles.filter((article) => {
      if (state.view === "saved" && !state.saved.has(article.id)) return false;
      return true;
    });
  }

  const query = state.search;
  return state.articles.filter((article) => {
    if (!matchesSource(article)) return false;
    if (state.lane !== "All" && (article.lane || "Tech & Development") !== state.lane) return false;
    if (state.view === "saved") {
      if (!state.saved.has(article.id)) return false;
    }
    return matchesSearch(article, query);
  });
}

function sourceStack(article) {
  return article.sources
    .slice(0, 5)
    .map(
      (source) =>
        `<i title="${escapeHtml(source.name)}" style="--source-accent:${escapeHtml(source.accent)}">${escapeHtml(sourceShortNames[source.id] || source.name.slice(0, 2))}</i>`,
    )
    .join("");
}

function relatedCoverage(article) {
  if (!article.related.length) return "";
  const rows = article.related
    .map(
      (item) => `
        <a class="related-row" href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">
          <strong>${escapeHtml(item.source)}</strong>
          <span>${escapeHtml(item.title)}</span>
          <time datetime="${escapeHtml(item.published_at)}">${escapeHtml(relativeTime(item.published_at))}</time>
        </a>`,
    )
    .join("");
  const coverageLabel = article.related.length === 1 ? "1 related report" : `${article.related.length} related reports`;
  return `
    <details class="coverage">
      <summary>${coverageLabel}</summary>
      <div class="related-list">${rows}</div>
    </details>`;
}

function sourcePreferenceMenu(article) {
  const sourceId = article.source_id || article.sources?.[0]?.id || "";
  if (!sourceId) return "";
  const muted = state.mutedSources.has(sourceId);
  return `
    <details class="source-menu">
      <summary aria-label="Source actions for ${escapeHtml(article.source)}" title="Source actions">•••</summary>
      <div class="source-menu-panel">
        <strong>${escapeHtml(article.source)}</strong>
        <button type="button" data-source-action="${muted ? "restore" : "mute"}" data-preference-source="${escapeHtml(sourceId)}">${muted ? "Restore this source" : "Mute this source"}</button>
      </div>
    </details>`;
}

function storyCard(article) {
  const saved = state.saved.has(article.id);
  const imageUrl = safeImageUrl(article.image);
  const image = imageUrl === "#" ? "" : `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`;
  const coverage = article.source_count > 1 ? `${article.source_count} sources` : "Single source";
  const lane = article.lane || "Tech & Development";
  const laneLabel = lane === "Business" ? "Business" : lane === "Industry" ? "Industry" : "Tech";
  const laneClass = lane === "Business" ? "is-business" : lane === "Industry" ? "is-industry" : "";
  const category = softwareGroup(article);
  const reasons = [...new Set([
    ...articleSoftwareCategories(article),
    ...(category === "Production techniques" ? articleTopics(article) : []),
    ...(article.priority_reasons || []),
  ])]
    .filter((reason) => reason !== category && !reason.startsWith("Other "))
    .slice(0, 3);
  const reasonMarkup = reasons.length
    ? `<div class="story-reasons">${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>`
    : "";
  return `
    <article class="story-card${state.keyboardArticleId === article.id ? " is-keyboard-active" : ""}" data-id="${escapeHtml(article.id)}" tabindex="-1" style="--story-accent:${escapeHtml(article.accent)}">
      <div class="story-visual${image ? "" : " image-failed"}" data-category="${escapeHtml(category)}">
        ${image}
        <div class="visual-overlay"></div>
        <span class="visual-category">${escapeHtml(category)}</span>
      </div>
      <div class="story-body">
        <div class="story-meta">
          <div class="story-classification">
            <span class="source-label">${escapeHtml(article.source)}</span>
            <span class="lane-badge${laneClass ? ` ${laneClass}` : ""}">${laneLabel}</span>
          </div>
          <time class="story-time" datetime="${escapeHtml(article.published_at)}" title="${escapeHtml(new Date(article.published_at).toLocaleString())}">${escapeHtml(relativeTime(article.published_at))}</time>
        </div>
        <h2 class="story-title">
          <a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.title)}</a>
        </h2>
        <p class="story-summary">${escapeHtml(trimSummary(article.summary))}</p>
        ${reasonMarkup}
        <div class="story-footer">
          <div class="source-stack">${sourceStack(article)}<span class="coverage-label">${coverage}</span></div>
          <div class="card-actions">
            <button class="save-button${saved ? " is-saved" : ""}" type="button" data-save-id="${escapeHtml(article.id)}" aria-label="${saved ? "Remove from saved" : "Save story"}" aria-pressed="${saved}">${saved ? "★" : "☆"}</button>
            ${sourcePreferenceMenu(article)}
          </div>
        </div>
        ${relatedCoverage(article)}
      </div>
    </article>`;
}

function newSinceDivider(count) {
  return `<div class="new-since-divider" role="separator"><span>${count} new since your last visit</span></div>`;
}

function latestStoryMarkup(visible) {
  const newCount = visible.filter(articleIsNew).length;
  let previousMonth = "";
  const groupByMonth = state.timeWindow === "quarter" && state.view === "all";
  return visible.map((article, index) => {
    const monthKey = groupByMonth ? articleMonthKey(article) : "";
    const monthDivider = monthKey && monthKey !== previousMonth
      ? `<div class="month-divider" role="separator"><span>${escapeHtml(articleMonthLabel(article))}</span></div>`
      : "";
    if (monthKey) previousMonth = monthKey;
    const newDivider = state.sessionCutoff && state.view === "all" && newCount
      && (newCount === visible.length ? index === 0 : index === newCount)
      ? newSinceDivider(newCount)
      : "";
    return `${monthDivider}${newDivider}${storyCard(article)}`;
  }).join("");
}

function libraryStoryMarkup(visible) {
  const groups = new Map();
  visible.forEach((article) => {
    const group = articleSoftwareCategories(article)[0] || softwareGroup(article);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(article);
  });
  const orderedGroups = [...groups.entries()].sort(([left], [right]) => {
    const leftIndex = SOFTWARE_GROUP_ORDER.indexOf(left);
    const rightIndex = SOFTWARE_GROUP_ORDER.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
  return orderedGroups.map(([group, articles]) => `
    <section class="library-group" aria-labelledby="library-${escapeHtml(group).replaceAll(" ", "-")}">
      <header><h2 id="library-${escapeHtml(group).replaceAll(" ", "-")}">${escapeHtml(group)}</h2><span>${articles.length}</span></header>
      <div class="library-grid">${articles.map(storyCard).join("")}</div>
    </section>`).join("");
}

function historyControlsMarkup() {
  if (!usesHistoryView()) return "";
  if (state.historyLoading && !state.historyArticles.length) {
    return `<div class="history-loading" role="status"><span></span> Searching your local history…</div>`;
  }
  if (!state.historyHasMore && !state.historyLoading) return "";
  return `
    <div class="history-load-more">
      <button type="button" data-load-more-history ${state.historyLoading ? "disabled" : ""}>
        ${state.historyLoading ? "Loading…" : `Load more · ${state.historyArticles.length} of ${state.historyTotal}`}
      </button>
    </div>`;
}

function facetCounts(articles, valuesForArticle) {
  const counts = new Map();
  articles.forEach((article) => {
    valuesForArticle(article).forEach((value) => {
      counts.set(value, (counts.get(value) || 0) + 1);
    });
  });
  return counts;
}

function orderedFacetCategories(counts, selected, order) {
  return [...new Set([...counts.keys(), ...selected])].sort((left, right) => {
    const leftIndex = order.indexOf(left);
    const rightIndex = order.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
}

function renderFacetButtons(element, kind, allLabel, allCount, categories, counts, selected, colors) {
  const buttons = [
    { label: allLabel, value: "All", count: allCount, color: "#d7ff57" },
    ...categories.map((category) => ({
      label: category,
      value: category,
      count: counts.get(category) || 0,
      color: colors[category] || "#d7ff57",
    })),
  ];
  element.innerHTML = buttons
    .map((button) => {
      const active = button.value === "All" ? selected.size === 0 : selected.has(button.value);
      return `
        <button class="facet-button ${kind}-button${active ? " is-active" : ""}" type="button" data-${kind}="${escapeHtml(button.value)}" aria-pressed="${active}" style="--category-accent:${escapeHtml(button.color)}">
          <span>${escapeHtml(button.label)}</span>
          <strong>${button.count}</strong>
        </button>`;
    })
    .join("");
}

function renderFacetFilters(pool) {
  const showTopics = state.view === "all" && productionTopicsActive();
  elements.softwareFilterGroup.hidden = state.view !== "all";
  elements.topicFilterGroup.hidden = !showTopics;
  if (state.view !== "all") return;

  if (!showTopics && state.topics.size) {
    state.topics.clear();
    persistFilterSet(storageKeys.topics, state.topics);
  }

  const softwarePool = pool;
  const softwareCounts = facetCounts(softwarePool, articleSoftwareCategories);
  SOFTWARE_GROUP_ORDER.forEach((category) => {
    if (!softwareCounts.has(category)) softwareCounts.set(category, 0);
  });
  renderFacetButtons(
    elements.softwareFilters,
    "software",
    "All categories",
    softwarePool.length,
    orderedFacetCategories(softwareCounts, state.software, SOFTWARE_GROUP_ORDER),
    softwareCounts,
    state.software,
    SOFTWARE_GROUP_COLORS,
  );
  if (!showTopics) return;

  const topicPool = pool.filter(matchesSoftware);
  const topicCounts = facetCounts(topicPool, articleTopics);
  renderFacetButtons(
    elements.topicFilters,
    "topic",
    "All techniques",
    topicPool.length,
    orderedFacetCategories(topicCounts, state.topics, TOPIC_ORDER),
    topicCounts,
    state.topics,
    TOPIC_COLORS,
  );
}

function render() {
  if (!state.payload) return;
  const pool = state.view === "all" ? latestPool() : [];
  renderFacetFilters(pool);
  const visible = filteredArticles();
  elements.grid.classList.toggle("is-list", state.layout === "list");
  elements.grid.classList.toggle("is-library", state.view === "saved");
  elements.grid.classList.remove("loading-grid");
  state.visibleArticleIds = visible.map((article) => article.id);
  if (state.keyboardArticleId && !state.visibleArticleIds.includes(state.keyboardArticleId)) {
    state.keyboardArticleId = null;
  }
  const storyMarkup = state.view === "saved"
    ? libraryStoryMarkup(visible)
    : latestStoryMarkup(visible);
  elements.grid.innerHTML = `${storyMarkup}${historyControlsMarkup()}`;
  const initialHistoryLoad = usesHistoryView() && state.historyLoading && !visible.length;
  elements.empty.hidden = visible.length > 0 || initialHistoryLoad;
  elements.grid.hidden = visible.length === 0 && !initialHistoryLoad;
  const emptyCopy = {
    saved: ["Your learning library is empty", "Save a story to keep it available here."],
    history: ["No articles match", "Try a broader search or restore your source filters."],
  }[state.view] || ["No signal here yet", "Try another category, clear your source filters, or refresh the feeds."];
  elements.empty.querySelector("h2").textContent = emptyCopy[0];
  elements.empty.querySelector("p").textContent = emptyCopy[1];
  const resultCount = usesHistoryView() ? state.historyTotal : visible.length;
  elements.visibleCount.textContent = `${resultCount} ${resultCount === 1 ? "story" : "stories"}`;
  const newCount = usesHistoryView() ? 0 : visible.filter(articleIsNew).length;
  elements.newSince.textContent = state.sessionCutoff && newCount ? `${newCount} new` : "";
  elements.newSince.hidden = !(state.sessionCutoff && newCount);
  const latestCount = state.articles.filter(articleWithinTimeWindow).length;
  elements.allCount.textContent = latestCount;
  elements.savedCount.textContent = state.saved.size;
  elements.historyCount.textContent = state.payload.history_count ?? state.historyTotal ?? "—";
  elements.sortLabel.textContent = {
    saved: "Learning library",
    history: "Full history",
    all: `${TIME_WINDOW_LABELS[state.timeWindow]} · newest first`,
  }[state.view] || "Newest first";

  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll(".lane-button[data-lane]").forEach((button) => {
    const lane = button.dataset.lane;
    const active = lane === state.lane;
    const count = lane === "All"
      ? state.articles.filter(articleWithinTimeWindow).length
      : state.articles.filter((article) => articleWithinTimeWindow(article) && (article.lane || "Tech & Development") === lane).length;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    button.querySelector("strong").textContent = count;
  });
  elements.layout.querySelector("span:first-child").textContent = state.layout === "grid" ? "▦" : "☷";
  document.querySelectorAll(".time-window-button[data-time-window]").forEach((button) => {
    const active = button.dataset.timeWindow === state.timeWindow;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function sourceCount(sourceId) {
  return state.articles.filter((article) => articleWithinTimeWindow(article) && article.sources.some((source) => source.id === sourceId)).length;
}

function renderSources(sources) {
  elements.sourceFilters.innerHTML = sources
    .map((source) => {
      const muted = state.mutedSources.has(source.id);
      const active = state.activeSources.has(source.id) && !muted;
      const status = muted ? "Muted — click to restore" : active ? "Included" : "Filtered out";
      return `
        <button class="source-button${active ? "" : " is-muted"}${muted ? " is-source-muted" : ""}" type="button" data-source-id="${escapeHtml(source.id)}" style="--source-accent:${escapeHtml(source.accent)}" aria-pressed="${active}" title="${escapeHtml(status)}">
          <span class="source-dot"></span>
          <span>${escapeHtml(source.name)}</span>
          ${muted ? '<em aria-hidden="true">muted</em>' : ""}
          <strong>${sourceCount(source.id)}</strong>
        </button>`;
    })
    .join("");
}

function updateDashboard(payload, { background = false } = {}) {
  const previousIds = new Set(state.knownArticleIds);
  state.payload = payload;
  state.articles = payload.articles || [];
  if (!state.firstFeedLoaded) {
    (payload.sources || []).forEach((source) => state.activeSources.add(source.id));
  } else {
    (payload.sources || []).forEach((source) => {
      if (!state.knownSourceIds.has(source.id)) state.activeSources.add(source.id);
    });
  }
  state.knownSourceIds = new Set((payload.sources || []).map((source) => source.id));
  state.knownArticleIds = new Set(state.articles.map((article) => article.id));
  elements.lastUpdated.textContent = payload.generated_at
    ? `Updated ${relativeTime(payload.generated_at)}${payload.cached ? " · local cache" : ""}${payload.refreshing ? " · refreshing" : ""}${payload.thumbnails_refreshing ? " · loading images" : ""}`
    : "Update time unavailable";
  renderSources(payload.sources || []);
  showWarnings(payload);
  render();
  if (background && previousIds.size) {
    const arrived = state.articles.filter((article) => !previousIds.has(article.id)).length;
    if (arrived) {
      elements.notice.textContent = `${arrived} new ${arrived === 1 ? "story has" : "stories have"} arrived. The board is up to date.`;
      elements.notice.hidden = false;
    }
  }
  state.firstFeedLoaded = true;
}

function syncAfterBackgroundRefresh(payload) {
  if (!payload.refreshing) {
    feedRefreshWaitPending = false;
    return;
  }
  if (feedRefreshWaitPending) return;
  feedRefreshWaitPending = true;
  loadFeed(false, { background: true, waitForRefresh: true })
    .finally(() => { feedRefreshWaitPending = false; });
}

function syncAfterThumbnailRefresh(payload) {
  if (!payload.thumbnails_refreshing) {
    thumbnailRefreshWaitPending = false;
    window.clearTimeout(thumbnailRefreshRetryTimer);
    thumbnailRefreshRetryTimer = null;
    thumbnailRefreshRetryDelay = 750;
    return;
  }
  if (thumbnailRefreshWaitPending) return;
  window.clearTimeout(thumbnailRefreshRetryTimer);
  thumbnailRefreshRetryTimer = null;
  thumbnailRefreshWaitPending = true;
  loadFeed(false, { background: true, waitForThumbnails: true })
    .finally(() => {
      thumbnailRefreshWaitPending = false;
      if (!state.payload?.thumbnails_refreshing || thumbnailRefreshRetryTimer) return;
      const retryDelay = thumbnailRefreshRetryDelay;
      thumbnailRefreshRetryDelay = Math.min(
        thumbnailRefreshRetryDelay * 2,
        THUMBNAIL_REFRESH_RETRY_MAX_MS,
      );
      thumbnailRefreshRetryTimer = window.setTimeout(() => {
        thumbnailRefreshRetryTimer = null;
        if (state.payload?.thumbnails_refreshing) {
          syncAfterThumbnailRefresh(state.payload);
        }
      }, retryDelay);
    });
}

function showWarnings(payload) {
  const warnings = payload.warnings || [];
  if (!warnings.length && !payload.stale) {
    elements.notice.hidden = true;
    return;
  }
  const names = [...new Set(warnings
    .map((item) => String(item).split(":", 1)[0].trim())
    .filter(Boolean))];
  const summary = payload.stale
    ? "Live refresh unavailable · showing cached stories"
    : `${names.length || "Some"} sources unavailable · showing cached stories`;
  const detail = names.length
    ? `<details><summary>Show unavailable sources</summary><span>${names.map(escapeHtml).join(", ")}</span></details>`
    : "";
  elements.notice.innerHTML = `<strong>${escapeHtml(summary)}</strong>${detail}`;
  elements.notice.hidden = false;
}

function validateFeedPayload(payload) {
  if (payload?.feed_schema_version !== FEED_SCHEMA_VERSION) {
    throw new Error("The dashboard server returned an incompatible feed schema. Restart CG Signal, then refresh the dashboard.");
  }
  if (!Array.isArray(payload.articles)) {
    throw new Error("The dashboard server returned an invalid article feed.");
  }
  if (!feedPayloadIsStructurallyCompatible(payload)) {
    throw new Error("The dashboard server returned incompatible article labels. Restart CG Signal, then refresh the dashboard.");
  }
  return payload;
}

async function loadFeed(
  force = false,
  { background = false, waitForRefresh = false, waitForThumbnails = false } = {},
) {
  if (!background) {
    elements.refresh.classList.add("is-loading");
    elements.refresh.disabled = true;
    elements.stories.setAttribute("aria-busy", "true");
  }
  try {
    const query = waitForRefresh
      ? "?wait=1"
      : waitForThumbnails
        ? "?wait_thumbnails=1"
        : "";
    const response = await apiFetch(
      force ? "/api/feed/refresh" : `/api/feed${query}`,
      force
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
        : {},
    );
    if (!response.ok) throw new Error(`Feed request failed (${response.status})`);
    const payload = await response.json();
    if (payload.error) throw new Error(payload.detail || payload.error);
    updateDashboard(validateFeedPayload(payload), { background });
    syncAfterBackgroundRefresh(payload);
    syncAfterThumbnailRefresh(payload);
  } catch (error) {
    if (background && state.payload) {
      console.warn("Background feed check failed; keeping the current board.", error);
      return;
    }
    elements.notice.textContent = `The feeds could not be gathered. ${error.message}`;
    elements.notice.hidden = false;
    elements.grid.hidden = true;
    elements.empty.hidden = false;
    elements.empty.querySelector("h2").textContent = "The signal is temporarily quiet";
    elements.empty.querySelector("p").textContent = "Check your connection, then refresh the dashboard.";
  } finally {
    if (!background) {
      elements.refresh.classList.remove("is-loading");
      elements.refresh.disabled = false;
      elements.stories.setAttribute("aria-busy", "false");
    }
  }
}

async function requestJson(url, options = {}) {
  const response = await apiFetch(url, {
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The local server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok || payload.error) {
    throw new Error(payload.detail || payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

function renderManagedSources() {
  elements.configuredSourceCount.textContent = `${state.managedSources.length} configured`;
  elements.managedSourceList.innerHTML = state.managedSources.map((source) => `
    <article class="managed-source${source.enabled ? "" : " is-disabled"}" style="--source-accent:${escapeHtml(source.accent)}">
      <span class="managed-source-accent" aria-hidden="true"></span>
      <div class="managed-source-copy">
        <div class="managed-source-title">
          <strong>${escapeHtml(source.name)}</strong>
          <span>${source.is_builtin ? "Built in" : "Custom"}</span>
          ${source.enabled ? "" : "<em>Disabled</em>"}
        </div>
        <a href="${escapeHtml(safeUrl(source.feed))}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.feed)}</a>
        <p data-source-test-status="${escapeHtml(source.id)}"></p>
      </div>
      <div class="managed-source-actions">
        <button class="secondary-action" type="button" data-test-source="${escapeHtml(source.id)}">Test</button>
        <button class="source-toggle${source.enabled ? "" : " is-enable"}" type="button" data-toggle-source="${escapeHtml(source.id)}" data-source-enabled="${source.enabled}">${source.enabled ? "Disable" : "Enable"}</button>
      </div>
    </article>`).join("");
}

async function loadManagedSources() {
  elements.managedSourceList.innerHTML = `<div class="managed-source-loading"><span></span> Loading configured sources…</div>`;
  try {
    const payload = await requestJson("/api/sources");
    state.managedSources = payload.sources || [];
    renderManagedSources();
  } catch (error) {
    elements.managedSourceList.innerHTML = `<p class="source-manager-error">${escapeHtml(error.message)}</p>`;
  }
}

function setSourceFormStatus(message, type = "") {
  elements.sourceFormStatus.textContent = message;
  elements.sourceFormStatus.dataset.status = type;
}

async function openSourceManager() {
  elements.sourceManagerPanel.hidden = false;
  elements.manageSources.setAttribute("aria-expanded", "true");
  document.body.classList.add("source-manager-open");
  setSourceFormStatus("");
  await loadManagedSources();
  elements.sourceFeedUrl.focus();
}

function closeSourceManager() {
  elements.sourceManagerPanel.hidden = true;
  elements.manageSources.setAttribute("aria-expanded", "false");
  document.body.classList.remove("source-manager-open");
  elements.manageSources.focus();
}

function setSidebarOpen(open, { focus = false } = {}) {
  state.sidebarOpen = Boolean(open);
  elements.appShell.classList.toggle("sidebar-closed", !state.sidebarOpen);
  localStorage.setItem(storageKeys.sidebar, state.sidebarOpen ? "1" : "0");
  elements.sidebarToggle.setAttribute("aria-expanded", String(state.sidebarOpen));
  const label = state.sidebarOpen ? "Hide navigation panel" : "Show navigation panel";
  elements.sidebarToggle.setAttribute("aria-label", label);
  elements.sidebarToggle.title = label;
  const icon = elements.sidebarToggle.querySelector("[data-sidebar-toggle-icon]");
  if (icon) icon.textContent = state.sidebarOpen ? "‹" : "›";
  if (focus) elements.sidebarToggle.focus();
}

function sidebarInteractionIsInternal(target) {
  return target instanceof Element
    && (Boolean(target.closest(".sidebar")) || Boolean(target.closest("#sidebar-toggle")));
}

document.addEventListener("pointerdown", (event) => {
  if (!state.sidebarOpen) return;
  if (!sidebarInteractionIsInternal(event.target)) setSidebarOpen(false);
});

document.addEventListener("focusin", (event) => {
  if (!state.sidebarOpen) return;
  if (!sidebarInteractionIsInternal(event.target)) setSidebarOpen(false);
});

async function testConfiguredSource(sourceId, button) {
  const status = elements.managedSourceList.querySelector(`[data-source-test-status="${CSS.escape(sourceId)}"]`);
  button.disabled = true;
  button.textContent = "Testing…";
  if (status) status.textContent = "Contacting the feed…";
  try {
    const result = await requestJson("/api/sources/test", {
      method: "POST",
      body: JSON.stringify({ id: sourceId }),
    });
    if (status) {
      status.textContent = result.ok
        ? `Working · ${result.count} recent ${result.count === 1 ? "item" : "items"} · ${result.duration_ms} ms`
        : `Could not read feed · ${result.message || "No valid articles were returned."}`;
      status.dataset.status = result.ok ? "success" : "error";
    }
  } catch (error) {
    if (status) {
      status.textContent = error.message;
      status.dataset.status = "error";
    }
  } finally {
    button.disabled = false;
    button.textContent = "Test";
  }
}

async function toggleConfiguredSource(sourceId, enabled, button) {
  button.disabled = true;
  button.textContent = enabled ? "Enabling…" : "Disabling…";
  try {
    await requestJson("/api/sources/toggle", {
      method: "POST",
      body: JSON.stringify({ id: sourceId, enabled }),
    });
    await loadManagedSources();
    await loadFeed(true);
  } catch (error) {
    setSourceFormStatus(error.message, "error");
    button.disabled = false;
    button.textContent = enabled ? "Enable" : "Disable";
  }
}

function setSourcePreference(sourceId, action) {
  if (action === "mute") {
    state.mutedSources.add(sourceId);
  } else {
    state.mutedSources.delete(sourceId);
    state.activeSources.add(sourceId);
  }
  queueUserStateSave();
  renderSources(state.payload.sources || []);
  if (usesHistoryView()) loadHistory();
  else render();
}

function moveKeyboardSelection(direction) {
  if (!state.visibleArticleIds.length) return;
  const current = state.visibleArticleIds.indexOf(state.keyboardArticleId);
  const next = current < 0
    ? (direction > 0 ? 0 : state.visibleArticleIds.length - 1)
    : Math.max(0, Math.min(state.visibleArticleIds.length - 1, current + direction));
  state.keyboardArticleId = state.visibleArticleIds[next];
  document.querySelectorAll(".story-card.is-keyboard-active").forEach((card) => card.classList.remove("is-keyboard-active"));
  const card = elements.grid.querySelector(`[data-id="${CSS.escape(state.keyboardArticleId)}"]`);
  card?.classList.add("is-keyboard-active");
  card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function keyboardTargetIsEditable(target) {
  return target instanceof HTMLElement
    && (target.matches("input, textarea, select, button") || target.isContentEditable);
}

function scheduleBackgroundChecks() {
  window.clearInterval(backgroundRefreshTimer);
  backgroundRefreshTimer = window.setInterval(() => {
    if (document.visibilityState !== "prerender") loadFeed(false, { background: true });
  }, 15 * 60 * 1000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.firstFeedLoaded) {
    loadFeed(false, { background: true });
  }
});

document.addEventListener("click", (event) => {
  const timeWindowButton = event.target.closest(".time-window-button[data-time-window]");
  if (timeWindowButton) {
    state.timeWindow = Object.prototype.hasOwnProperty.call(TIME_WINDOW_LABELS, timeWindowButton.dataset.timeWindow)
      ? timeWindowButton.dataset.timeWindow
      : "month";
    localStorage.setItem(storageKeys.timeWindow, state.timeWindow);
    renderSources(state.payload?.sources || []);
    if (usesHistoryView()) loadHistory();
    else render();
    return;
  }

  const searchToken = event.target.closest("[data-search-token]");
  if (searchToken) {
    const token = searchToken.dataset.searchToken;
    const existing = elements.search.value.trim();
    elements.search.value = existing ? `${existing} ${token}` : token;
    state.search = elements.search.value;
    elements.search.focus();
    if (usesHistoryView()) loadHistory();
    else render();
    return;
  }

  const loadMoreHistory = event.target.closest("[data-load-more-history]");
  if (loadMoreHistory) {
    loadHistory({ append: true });
    return;
  }

  const testSourceButton = event.target.closest("[data-test-source]");
  if (testSourceButton) {
    testConfiguredSource(testSourceButton.dataset.testSource, testSourceButton);
    return;
  }

  const toggleSourceButton = event.target.closest("[data-toggle-source]");
  if (toggleSourceButton) {
    const enable = toggleSourceButton.dataset.sourceEnabled !== "true";
    toggleConfiguredSource(toggleSourceButton.dataset.toggleSource, enable, toggleSourceButton);
    return;
  }

  const preferenceButton = event.target.closest("[data-source-action]");
  if (preferenceButton) {
    setSourcePreference(preferenceButton.dataset.preferenceSource, preferenceButton.dataset.sourceAction);
    return;
  }

  const saveButton = event.target.closest("[data-save-id]");
  if (saveButton) {
    const id = saveButton.dataset.saveId;
    const wasSaved = state.saved.has(id);
    wasSaved ? state.saved.delete(id) : state.saved.add(id);
    queueUserStateSave();
    render();
    return;
  }

  const sourceButton = event.target.closest("[data-source-id]");
  if (sourceButton) {
    const id = sourceButton.dataset.sourceId;
    if (state.mutedSources.has(id)) {
      state.mutedSources.delete(id);
      queueUserStateSave();
    }
    if (event.ctrlKey || event.metaKey) {
      state.activeSources.has(id) ? state.activeSources.delete(id) : state.activeSources.add(id);
    } else {
      const effectiveActive = [...state.activeSources].filter((sourceId) => !state.mutedSources.has(sourceId));
      const alreadyIsolated = effectiveActive.length === 1 && effectiveActive[0] === id;
      state.activeSources = alreadyIsolated
        ? new Set((state.payload.sources || []).map((source) => source.id))
        : new Set([id]);
    }
    renderSources(state.payload.sources || []);
    if (usesHistoryView()) loadHistory();
    else render();
    return;
  }

  const softwareButton = event.target.closest(".software-button");
  if (softwareButton) {
    const software = softwareButton.dataset.software;
    chooseSingleFilter(state.software, software);
    persistFilterSet(storageKeys.software, state.software);
    if (!productionTopicsActive() && state.topics.size) {
      state.topics.clear();
      persistFilterSet(storageKeys.topics, state.topics);
    }
    render();
    return;
  }

  const topicButton = event.target.closest(".topic-button");
  if (topicButton) {
    const topic = topicButton.dataset.topic;
    chooseSingleFilter(state.topics, topic);
    persistFilterSet(storageKeys.topics, state.topics);
    render();
    return;
  }

  const laneButton = event.target.closest(".lane-button");
  if (laneButton) {
    state.lane = laneButton.dataset.lane;
    localStorage.setItem(storageKeys.lane, state.lane);
    if (usesHistoryView()) loadHistory();
    else render();
    return;
  }

  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    state.view = viewButton.dataset.view;
    if (usesHistoryView()) loadHistory();
    else render();
  }
});

elements.search.addEventListener("input", () => {
  state.search = elements.search.value;
  window.clearTimeout(historySearchTimer);
  if (usesHistoryView()) {
    historySearchTimer = window.setTimeout(() => loadHistory(), 250);
  } else {
    render();
  }
});

elements.refresh.addEventListener("click", () => loadFeed(true));

elements.sidebarToggle.addEventListener("click", () => {
  setSidebarOpen(!state.sidebarOpen, { focus: true });
});

elements.manageSources.addEventListener("click", openSourceManager);

document.querySelectorAll("[data-close-source-manager]").forEach((button) => {
  button.addEventListener("click", closeSourceManager);
});

elements.testFeedUrl.addEventListener("click", async () => {
  if (!elements.sourceFeedUrl.reportValidity()) return;
  const payload = {
    feed: elements.sourceFeedUrl.value.trim(),
    name: elements.sourceName.value.trim(),
    site: elements.sourceSiteUrl.value.trim(),
  };
  elements.testFeedUrl.disabled = true;
  elements.testFeedUrl.textContent = "Testing…";
  setSourceFormStatus("Contacting the feed…");
  try {
    const result = await requestJson("/api/sources/test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const sample = result.sample_titles?.[0] ? ` First item: ${result.sample_titles[0]}` : "";
    setSourceFormStatus(
      result.ok
        ? `Feed works · ${result.count} recent ${result.count === 1 ? "item" : "items"} · ${result.duration_ms} ms.${sample}`
        : `Could not read this feed. ${result.message || "No valid RSS or Atom items were found."}`,
      result.ok ? "success" : "error",
    );
  } catch (error) {
    setSourceFormStatus(error.message, "error");
  } finally {
    elements.testFeedUrl.disabled = false;
    elements.testFeedUrl.textContent = "Test URL";
  }
});

elements.sourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!elements.sourceForm.reportValidity()) return;
  const submit = elements.sourceForm.querySelector('[type="submit"]');
  const payload = {
    feed: elements.sourceFeedUrl.value.trim(),
    name: elements.sourceName.value.trim(),
    site: elements.sourceSiteUrl.value.trim(),
  };
  submit.disabled = true;
  submit.textContent = "Adding…";
  setSourceFormStatus("Saving this source locally…");
  try {
    const result = await requestJson("/api/sources", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    elements.sourceForm.reset();
    setSourceFormStatus(`${result.source.name} was added and enabled.`, "success");
    await loadManagedSources();
    await loadFeed(true);
  } catch (error) {
    setSourceFormStatus(error.message, "error");
  } finally {
    submit.disabled = false;
    submit.textContent = "Add source";
  }
});

elements.layout.addEventListener("click", () => {
  state.layout = state.layout === "grid" ? "list" : "grid";
  localStorage.setItem(storageKeys.layout, state.layout);
  render();
});

elements.scrollTop.addEventListener("click", () => {
  const firstArticle = elements.grid.querySelector(".story-card:not(.skeleton-card)");
  if (!firstArticle) {
    window.scrollTo({ top: 0 });
    return;
  }
  firstArticle.scrollIntoView({ behavior: "auto", block: "center" });
});

elements.home.addEventListener("click", () => {
  state.view = "all";
  render();
  window.scrollTo({ top: 0, behavior: "auto" });
});

document.querySelector("#reset-sources").addEventListener("click", () => {
  state.activeSources = new Set((state.payload.sources || []).map((source) => source.id));
  state.mutedSources.clear();
  queueUserStateSave();
  renderSources(state.payload.sources || []);
  if (usesHistoryView()) loadHistory();
  else render();
});

document.querySelector("#clear-filters").addEventListener("click", () => {
  state.activeSources = new Set((state.payload.sources || []).map((source) => source.id));
  state.lane = "All";
  state.software.clear();
  state.topics.clear();
  localStorage.setItem(storageKeys.lane, state.lane);
  persistFilterSet(storageKeys.software, state.software);
  persistFilterSet(storageKeys.topics, state.topics);
  state.view = "all";
  state.timeWindow = "month";
  localStorage.setItem(storageKeys.timeWindow, state.timeWindow);
  state.search = "";
  elements.search.value = "";
  renderSources(state.payload.sources || []);
  render();
});

document.querySelector("#theme-toggle").addEventListener("click", () => {
  document.body.classList.toggle("night");
  localStorage.setItem(storageKeys.theme, document.body.classList.contains("night") ? "night" : "paper");
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    elements.search.focus();
  }
  if (event.key === "Escape" && document.activeElement === elements.search) {
    elements.search.value = "";
    state.search = "";
    elements.search.blur();
    render();
  } else if (event.key === "Escape" && !elements.sourceManagerPanel.hidden) {
    closeSourceManager();
  } else if (event.key === "Escape" && state.sidebarOpen) {
    setSidebarOpen(false, { focus: true });
  }

  if (event.ctrlKey || event.metaKey || event.altKey || !elements.sourceManagerPanel.hidden || keyboardTargetIsEditable(event.target)) return;
  const key = event.key.toLocaleLowerCase();
  if (key === "j" || key === "k") {
    event.preventDefault();
    moveKeyboardSelection(key === "j" ? 1 : -1);
    return;
  }
  if (!state.keyboardArticleId) return;
  if (key === "enter") {
    event.preventDefault();
    elements.grid.querySelector(`[data-id="${CSS.escape(state.keyboardArticleId)}"] .story-title a`)?.click();
  } else if (key === "s") {
    event.preventDefault();
    const wasSaved = state.saved.has(state.keyboardArticleId);
    wasSaved ? state.saved.delete(state.keyboardArticleId) : state.saved.add(state.keyboardArticleId);
    queueUserStateSave();
    render();
  }
});

elements.grid.addEventListener(
  "error",
  (event) => {
    if (event.target.tagName === "IMG") {
      event.target.closest(".story-visual")?.classList.add("image-failed");
    }
  },
  true,
);

if (localStorage.getItem(storageKeys.theme) === "night") {
  document.body.classList.add("night");
}

setSidebarOpen(state.sidebarOpen);

async function initialize() {
  await Promise.all([loadUserState(), loadFeed()]);
  render();
  localStorage.setItem(storageKeys.lastVisit, state.sessionStartedAt);
  scheduleBackgroundChecks();
}

initialize();
