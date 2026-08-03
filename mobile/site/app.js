const storageKeys = {
  pinned: "cg-signal-mobile:pinned",
  feed: "cg-signal-mobile:last-feed",
  disabledSources: "cg-signal-mobile:disabled-sources",
  density: "cg-signal-mobile:density",
  timeWindow: "cg-signal-mobile:time-window",
};

const CLASSIFICATION_VERSION = 4;

const CATEGORY_ORDER = [
  "Unreal Engine",
  "Unity",
  "Blender",
  "Substance 3D",
  "Houdini",
  "AI",
  "Production techniques",
  "Industry context",
  "Business context",
];

const CATEGORY_COLORS = {
  "Unreal Engine": "#4b75ff",
  Unity: "#202a34",
  Blender: "#f18a21",
  "Substance 3D": "#9fa9ff",
  Houdini: "#ff7b38",
  AI: "#a77bff",
  "Production techniques": "#d7ff57",
  "Industry context": "#f4a261",
  "Business context": "#c78cff",
};

const TIME_WINDOW_LABELS = {
  month: "This month",
  quarter: "Last 3 months",
  all: "All available",
};

const SEARCH_ALIASES = new Map([
  ["unreal", ["software", "unreal engine"]],
  ["unreal-engine", ["software", "unreal engine"]],
  ["ue", ["software", "unreal engine"]],
  ["ue5", ["software", "unreal engine"]],
  ["unity", ["software", "unity"]],
  ["blender", ["software", "blender"]],
  ["houdini", ["software", "houdini"]],
  ["substance", ["software", "substance 3d"]],
  ["painter", ["software", "substance 3d"]],
  ["designer", ["software", "substance 3d"]],
  ["ai", ["software", "ai"]],
  ["genai", ["software", "ai"]],
  ["production", ["software", "production techniques"]],
  ["industry", ["software", "industry context"]],
  ["business", ["software", "business context"]],
]);

const state = {
  payload: null,
  articles: [],
  pinned: readIdSet(storageKeys.pinned),
  disabledSources: disabledSourceSet(),
  lane: "All",
  category: "All",
  source: "All",
  search: "",
  view: "latest",
  timeWindow: Object.prototype.hasOwnProperty.call(TIME_WINDOW_LABELS, localStorage.getItem(storageKeys.timeWindow))
    ? localStorage.getItem(storageKeys.timeWindow)
    : "month",
  density: localStorage.getItem(storageKeys.density) === "compact" ? "compact" : "comfortable",
  installPrompt: null,
  lastFetchAt: 0,
};

const elements = {
  storyList: document.querySelector("#story-list"),
  empty: document.querySelector("#empty-state"),
  clearFilters: document.querySelector("#clear-filters"),
  storyTotal: document.querySelector("#story-total"),
  repeatTotal: document.querySelector("#repeat-total"),
  recentTotal: document.querySelector("#recent-total"),
  updateStatus: document.querySelector("#update-status"),
  connectionDot: document.querySelector("#connection-dot"),
  categoryLists: [...document.querySelectorAll("[data-category-list]")],
  sourceButtonList: document.querySelector("#source-list"),
  sourceManagerPanel: document.querySelector("#source-manager-panel"),
  sourceManagerList: document.querySelector("#source-manager-list"),
  sourceEnabledTotal: document.querySelector("#source-enabled-total"),
  searchInputs: [...document.querySelectorAll("[data-search-input]")],
  clearSearchButtons: [...document.querySelectorAll("[data-clear-search]")],
  notice: document.querySelector("#notice"),
  feedKicker: document.querySelector("#feed-kicker"),
  feedTitle: document.querySelector("#feed-title"),
  install: document.querySelector("#install-button"),
  scrollTop: document.querySelector("#scroll-top-button"),
  filterDrawer: document.querySelector(".filter-drawer"),
  filterDrawerHandle: document.querySelector("#filter-drawer-handle"),
  filterDrawerSummary: document.querySelector("#filter-drawer-summary"),
  densityToggle: document.querySelector("#density-toggle"),
  pinnedTotal: document.querySelector("#pinned-total"),
};

let sourceManagerReturnFocus = null;
let filterPointerStartY = null;
let ignoreNextFilterClick = false;

function readIdSet(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(value) ? value : []);
  } catch {
    return new Set();
  }
}

function disabledSourceSet() {
  try {
    const value = JSON.parse(localStorage.getItem(storageKeys.disabledSources) || "[]");
    return new Set(Array.isArray(value) ? value.filter((id) => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function persistPinned() {
  const bounded = [...state.pinned].filter((id) => typeof id === "string").slice(-1500);
  state.pinned = new Set(bounded);
  try {
    localStorage.setItem(storageKeys.pinned, JSON.stringify(bounded));
  } catch (error) {
    console.warn("CG Signal could not persist pins on this device.", error);
  }
}

function persistDisabledSources() {
  const disabled = [...state.disabledSources]
    .filter((id) => typeof id === "string")
    .slice(-500);
  state.disabledSources = new Set(disabled);
  try {
    if (disabled.length) localStorage.setItem(storageKeys.disabledSources, JSON.stringify(disabled));
    else localStorage.removeItem(storageKeys.disabledSources);
  } catch (error) {
    console.warn("CG Signal could not persist source settings on this device.", error);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

function normalize(value) {
  return String(value || "").toLocaleLowerCase();
}

function relativeTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const ranges = [
    [31536000, "year"],
    [2592000, "month"],
    [604800, "week"],
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ];
  for (const [size, unit] of ranges) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

function articleCategories(article) {
  const categories = [...new Set([...(article.software_tags || []), article.software_group].filter(Boolean))];
  return categories.length
    ? categories
    : [article.lane === "Business" ? "Business context" : article.lane === "Industry" ? "Industry context" : "Production techniques"];
}

function timeWindowStart() {
  if (state.timeWindow === "all") return null;
  const monthOffset = state.timeWindow === "quarter" ? 2 : 0;
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth() - monthOffset, 1);
}

function articleWithinTimeWindow(article) {
  const start = timeWindowStart();
  if (!start) return true;
  const published = new Date(article.published_at);
  return Number.isNaN(published.getTime()) || published >= start;
}

function primaryCategory(article) {
  return article.software_group || articleCategories(article)[0];
}

function searchableText(article) {
  return normalize([
    article.title,
    article.summary,
    article.source,
    article.lane,
    article.software_group,
    ...(article.software_tags || []),
    ...(article.topic_tags || []),
    ...(article.priority_reasons || []),
    ...(article.related || []).flatMap((item) => [item.source, item.title]),
  ].join(" "));
}

function parseSearch(query) {
  const normalizedQuery = query
    .replace(/#unreal\s+engine\b/gi, '#software:"Unreal Engine"')
    .replace(/#substance\s+(?:painter|designer|3d)\b/gi, '#software:"Substance 3D"')
    .replace(/#industry\s+context\b/gi, '#software:"Industry context"')
    .replace(/#business\s+context\b/gi, '#software:"Business context"');
  const rawTokens = normalizedQuery.match(/-?#(?:software|topic|source|is):(?:"[^"]+"|'[^']+'|\S+)|-?#[\p{L}\p{N}_-]+|-?"[^"]+"|-?\S+/giu) || [];
  return rawTokens.map((raw) => {
    const negative = raw.startsWith("-");
    let token = negative ? raw.slice(1) : raw;
    let field = "text";
    let value = token;
    if (token.startsWith("#")) {
      token = token.slice(1);
      const separator = token.indexOf(":");
      if (separator > -1) {
        const possibleField = token.slice(0, separator).toLocaleLowerCase();
        if (["software", "topic", "source", "is"].includes(possibleField)) {
          field = possibleField;
          value = token.slice(separator + 1);
        }
      } else {
        const alias = SEARCH_ALIASES.get(token.toLocaleLowerCase().replaceAll("_", "-"));
        if (alias) [field, value] = alias;
        else value = token;
      }
    }
    value = normalize(value.replace(/^['"]|['"]$/g, ""));
    return { negative, field, value };
  }).filter((token) => token.value);
}

function tokenMatches(article, token) {
  if (token.field === "software") {
    return articleCategories(article).some((category) => normalize(category).includes(token.value));
  }
  if (token.field === "topic") {
    return (article.topic_tags || []).some((topic) => normalize(topic).includes(token.value));
  }
  if (token.field === "source") {
    return (article.sources || []).some((source) => normalize(`${source.id} ${source.name}`).includes(token.value))
      || normalize(`${article.source_id} ${article.source}`).includes(token.value);
  }
  if (token.field === "is") {
    if (token.value === "new") return Date.now() - new Date(article.published_at).getTime() < 24 * 60 * 60 * 1000;
    return false;
  }
  return searchableText(article).includes(token.value);
}

function matchesSearch(article) {
  return parseSearch(state.search).every((token) => tokenMatches(article, token) !== token.negative);
}

function articleSourceIds(article) {
  const ids = new Set((article.sources || []).map((source) => source.id).filter(Boolean));
  if (article.source_id) ids.add(article.source_id);
  return ids;
}

function articleHasEnabledSource(article) {
  const sourceIds = articleSourceIds(article);
  return sourceIds.size === 0 || [...sourceIds].some((id) => !state.disabledSources.has(id));
}

function matchesBaseFilters(article) {
  if (state.view !== "pinned" && !articleWithinTimeWindow(article)) return false;
  if (!articleHasEnabledSource(article)) return false;
  if (state.lane !== "All" && article.lane !== state.lane) return false;
  if (state.source !== "All" && !articleSourceIds(article).has(state.source)) return false;
  if (state.view === "pinned" && !state.pinned.has(article.id)) return false;
  return matchesSearch(article);
}

function visibleArticles() {
  return state.articles.filter((article) => {
    if (!matchesBaseFilters(article)) return false;
    return state.category === "All" || articleCategories(article).includes(state.category);
  });
}

function categoryCounts() {
  const counts = new Map();
  state.articles.filter(matchesBaseFilters).forEach((article) => {
    articleCategories(article).forEach((category) => counts.set(category, (counts.get(category) || 0) + 1));
  });
  return counts;
}

function syncControlValues() {
  elements.searchInputs.forEach((input) => {
    if (input.value !== state.search) input.value = state.search;
  });
}

function renderCategories() {
  const counts = categoryCounts();
  const categories = CATEGORY_ORDER.filter((category) => counts.has(category) || category === state.category);
  const allCount = state.articles.filter(matchesBaseFilters).length;
  const markup = ["All", ...categories].map((category) => {
    const active = category === state.category;
    const label = category === "All" ? "All categories" : category;
    const count = category === "All" ? allCount : (counts.get(category) || 0);
    const color = category === "All" ? "#d7ff57" : (CATEGORY_COLORS[category] || "#7fa9ff");
    return `<button class="category-button${active ? " is-active" : ""}" type="button" data-category="${escapeHtml(category)}" aria-pressed="${active}" style="--category-accent:${escapeHtml(color)}"><span>${escapeHtml(label)}</span><strong>${count}</strong></button>`;
  }).join("");
  elements.categoryLists.forEach((list) => { list.innerHTML = markup; });
}

function sourceContext() {
  const articles = state.articles.filter((article) => {
    if (state.view !== "pinned" && !articleWithinTimeWindow(article)) return false;
    if (!articleHasEnabledSource(article)) return false;
    if (state.lane !== "All" && article.lane !== state.lane) return false;
    if (!matchesSearch(article)) return false;
    return state.category === "All" || articleCategories(article).includes(state.category);
  });
  const counts = new Map();
  articles.forEach((article) => {
    articleSourceIds(article).forEach((id) => counts.set(id, (counts.get(id) || 0) + 1));
  });
  return { articles, counts };
}

function renderSourceButtons() {
  const { articles, counts } = sourceContext();
  const sources = [
    { id: "All", name: "All sources", accent: "#d7ff57", count: articles.length },
    ...(state.payload?.sources || [])
      .filter((source) => !state.disabledSources.has(source.id))
      .map((source) => ({ ...source, count: counts.get(source.id) || 0 })),
  ];
  elements.sourceButtonList.innerHTML = sources.map((source) => {
    const active = source.id === state.source;
    const countLabel = `${source.count} ${source.count === 1 ? "story" : "stories"}`;
    return `<button class="source-button${source.id === "All" ? " is-all" : ""}${active ? " is-active" : ""}" type="button" data-source-option="${escapeHtml(source.id)}" aria-pressed="${active}" aria-label="${escapeHtml(`${source.name}, ${countLabel}`)}" style="--source-accent:${escapeHtml(source.accent || "#7fa9ff")}"><span>${escapeHtml(source.name)}</span><strong>${source.count}</strong></button>`;
  }).join("");
}

function renderSourceManager() {
  const sources = state.payload?.sources || [];
  const enabledCount = sources.filter((source) => !state.disabledSources.has(source.id)).length;
  elements.sourceEnabledTotal.textContent = `${enabledCount}/${sources.length}`;
  elements.sourceManagerList.innerHTML = sources.map((source) => {
    const enabled = !state.disabledSources.has(source.id);
    const count = Number(source.count || 0);
    const countLabel = `${count} ${count === 1 ? "story" : "stories"}`;
    const action = enabled ? "Disable" : "Enable";
    return `<button class="source-manager-item${enabled ? " is-enabled" : ""}" type="button" data-toggle-source="${escapeHtml(source.id)}" aria-pressed="${enabled}" aria-label="${escapeHtml(`${action} ${source.name}, ${countLabel}`)}" style="--source-accent:${escapeHtml(source.accent || "#7fa9ff")}"><span class="source-manager-label"><i aria-hidden="true"></i><span><strong>${escapeHtml(source.name)}</strong><small>${escapeHtml(countLabel)}</small></span></span><span class="source-switch" aria-hidden="true"><i></i></span></button>`;
  }).join("");
}

function trimSummary(value) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  return clean.length > 245 ? `${clean.slice(0, 242).trimEnd()}…` : clean;
}

function storyMarkup(article) {
  const pinned = state.pinned.has(article.id);
  const category = primaryCategory(article);
  const imageUrl = safeUrl(article.image);
  const image = imageUrl === "#" ? "" : `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`;
  const reasons = [...new Set([...(article.software_tags || []), ...(article.topic_tags || [])])]
    .filter((reason) => reason !== category)
    .slice(0, 2);
  const coverage = article.source_count > 1 ? `${article.source_count} sources` : "Single source";
  return `
    <article class="story-card" style="--story-accent:${escapeHtml(article.accent || CATEGORY_COLORS[category] || "#7fa9ff")}">
      <div class="story-image${image ? "" : " no-image"}">
        ${image}
        <span>${escapeHtml(category)}</span>
      </div>
      <div class="story-body">
        <div class="story-meta">
          <span class="source-name">${escapeHtml(article.source)}</span>
          <span class="lane-label${article.lane === "Business" ? " is-business" : article.lane === "Industry" ? " is-industry" : ""}">${article.lane === "Business" ? "Business" : article.lane === "Industry" ? "Industry" : "Tech"}</span>
          <time datetime="${escapeHtml(article.published_at)}">${escapeHtml(relativeTime(article.published_at))}</time>
        </div>
        <h3><a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.title)}</a></h3>
        <p>${escapeHtml(trimSummary(article.summary))}</p>
        ${reasons.length ? `<div class="reason-list">${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>` : ""}
        <footer><span>${escapeHtml(coverage)}</span><div class="story-footer-actions"><button class="pin-button${pinned ? " is-pinned" : ""}" type="button" data-pin-id="${escapeHtml(article.id)}" aria-label="${pinned ? "Unpin" : "Pin"} ${escapeHtml(article.title)}" aria-pressed="${pinned}">${pinned ? "★" : "☆"}</button><a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer">Read original <i aria-hidden="true">↗</i></a></div></footer>
      </div>
    </article>`;
}

const RECENCY_SECTIONS = [
  { key: "day", label: "Last 24 hours" },
  { key: "three-days", label: "Last 3 days" },
  { key: "older", label: "Earlier" },
];

function articleRecencyBucket(article) {
  const published = new Date(article.published_at).getTime();
  if (Number.isNaN(published)) return "older";
  const age = Math.max(0, Date.now() - published);
  if (age < 24 * 60 * 60 * 1000) return "day";
  if (age < 3 * 24 * 60 * 60 * 1000) return "three-days";
  return "older";
}

function storyListMarkup(articles) {
  return RECENCY_SECTIONS.map(({ key, label }) => {
    const sectionArticles = articles.filter((article) => articleRecencyBucket(article) === key);
    if (!sectionArticles.length) return "";
    const headingId = "recency-" + key;
    return [
      '<section class="recency-section" aria-labelledby="' + headingId + '">',
      '<div class="recency-section-heading"><h2 id="' + headingId + '">' + escapeHtml(label) + '</h2><span>' + sectionArticles.length + '</span></div>',
      '<div class="recency-stories">' + sectionArticles.map(storyMarkup).join("") + '</div>',
      '</section>',
    ].join("");
  }).join("");
}

function updateLaneCounts() {
  const enabledArticles = state.articles.filter((article) => articleHasEnabledSource(article) && (state.view === "pinned" ? state.pinned.has(article.id) : articleWithinTimeWindow(article)));
  document.querySelectorAll("[data-lane]").forEach((button) => {
    const lane = button.dataset.lane;
    const count = lane === "All" ? enabledArticles.length : enabledArticles.filter((article) => article.lane === lane).length;
    const active = lane === state.lane;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    button.querySelector("strong").textContent = count;
  });
}

function renderFilterDrawerSummary() {
  const source = state.source === "All"
    ? "All sources"
    : (state.payload?.sources || []).find((item) => item.id === state.source)?.name || state.source;
  const lane = state.lane === "All" ? "All types" : (state.lane === "Tech & Development" ? "Tech" : state.lane);
  const category = state.category === "All" ? "All categories" : state.category;
  elements.filterDrawerSummary.textContent = `${TIME_WINDOW_LABELS[state.timeWindow]} · ${lane} · ${category} · ${source}`;
}

function setFilterDrawerExpanded(expanded) {
  elements.filterDrawer.classList.toggle("is-collapsed", !expanded);
  elements.filterDrawerHandle.setAttribute("aria-expanded", String(expanded));
}

function render() {
  if (!state.payload) return;
  const articles = visibleArticles();
  const enabledArticles = state.articles.filter((article) => articleHasEnabledSource(article) && articleWithinTimeWindow(article));
  const pinned = state.articles.filter((article) => articleHasEnabledSource(article) && state.pinned.has(article.id)).length;
  syncControlValues();
  renderCategories();
  renderSourceButtons();
  renderSourceManager();
  renderFilterDrawerSummary();
  updateLaneCounts();
  elements.storyTotal.textContent = state.view === "pinned" ? pinned : enabledArticles.length;
  elements.repeatTotal.textContent = state.payload.duplicates_collapsed || 0;
  elements.recentTotal.textContent = enabledArticles.filter((article) => articleRecencyBucket(article) === "day").length;
  elements.pinnedTotal.textContent = pinned;
  elements.feedKicker.textContent = state.view === "pinned" ? "Pinned signal" : "Latest signal";
  elements.feedTitle.textContent = state.view === "pinned" ? "Keep these close" : "What is worth a look";
  elements.storyList.innerHTML = storyListMarkup(articles);
  elements.storyList.classList.toggle("is-compact", state.density === "compact");
  elements.storyList.hidden = articles.length === 0;
  elements.empty.hidden = articles.length > 0;
  const allSourcesDisabled = Boolean(state.payload.sources?.length)
    && state.payload.sources.every((source) => state.disabledSources.has(source.id));
  elements.empty.querySelector("h2").textContent = allSourcesDisabled ? "All mobile sources are disabled" : "No matching signal";
  elements.empty.querySelector("p").textContent = allSourcesDisabled ? "Enable at least one source to rebuild this phone’s feed." : "Try a broader search or clear your filters.";
  elements.clearFilters.textContent = allSourcesDisabled ? "Enable all sources" : "Clear filters";
  elements.clearFilters.dataset.action = allSourcesDisabled ? "enable-sources" : "reset-filters";
  elements.storyList.setAttribute("aria-busy", "false");
  elements.clearSearchButtons.forEach((button) => { button.hidden = !state.search.trim(); });
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view || (button.dataset.view === "latest" && state.view === "latest");
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  const compact = state.density === "compact";
  elements.densityToggle.classList.toggle("is-active", compact);
  elements.densityToggle.setAttribute("aria-pressed", String(compact));
  elements.densityToggle.setAttribute("aria-label", compact ? "Use comfortable cards" : "Use compact cards");
  elements.densityToggle.title = compact ? "Use comfortable cards" : "Use compact cards";
  elements.densityToggle.querySelector("span:last-child").textContent = compact ? "Comfort" : "Compact";
  document.querySelectorAll(".time-window-button[data-time-window]").forEach((button) => {
    const active = button.dataset.timeWindow === state.timeWindow;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function updateConnection(online, cached = false, checking = false) {
  elements.connectionDot.classList.toggle("is-offline", !online);
  if (!state.payload) return;
  const generated = state.payload.generated_at ? relativeTime(state.payload.generated_at) : "recently";
  const retained = Number(state.payload.carried_forward_count || 0);
  const retainedLabel = retained ? ` · ${retained} retained` : "";
  elements.updateStatus.textContent = checking
    ? `Stored copy · checking for updates${retainedLabel}`
    : cached
      ? `Offline copy · updated ${generated}${retainedLabel}`
      : `Updated ${generated}${retainedLabel} · refreshes every 30 minutes`;
}

function readCachedFeed() {
  try {
    const payload = JSON.parse(localStorage.getItem(storageKeys.feed) || "null");
    return payload?.classification_version === CLASSIFICATION_VERSION
      && Array.isArray(payload.articles)
      && payload.articles.length
      ? payload
      : null;
  } catch {
    return null;
  }
}

function applyFeed(payload, { store = false } = {}) {
  state.payload = payload;
  state.articles = payload.articles;
  if (store) {
    try {
      localStorage.setItem(storageKeys.feed, JSON.stringify(payload));
    } catch (error) {
      console.warn("CG Signal could not update its offline feed copy.", error);
    }
  }
  persistPinned();
  persistDisabledSources();
  if (state.disabledSources.has(state.source)) state.source = "All";
  render();
}

async function loadFeed() {
  state.lastFetchAt = Date.now();
  const cached = readCachedFeed();
  if (!state.payload && cached) {
    applyFeed(cached);
    updateConnection(navigator.onLine, true, navigator.onLine);
  }
  try {
    const response = await fetch("./feed.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Feed request failed (${response.status})`);
    const payload = await response.json();
    if (payload.classification_version !== CLASSIFICATION_VERSION) throw new Error("The hosted feed uses an incompatible classifier.");
    if (!Array.isArray(payload.articles) || !payload.articles.length) throw new Error("The hosted feed is empty.");
    applyFeed(payload, { store: true });
    updateConnection(true);
    if (payload.unavailable_sources?.length) {
      const retentionMessage = payload.carried_forward_count
        ? " Recent articles from earlier successful updates are retained."
        : "";
      elements.notice.textContent = `Some sources missed the latest update: ${payload.unavailable_sources.join(", ")}.${retentionMessage}`;
      elements.notice.hidden = false;
    } else {
      elements.notice.hidden = true;
    }
  } catch (error) {
    const fallback = state.payload || cached;
    if (fallback?.articles?.length) {
      if (!state.payload) applyFeed(fallback);
      updateConnection(false, true);
      elements.notice.textContent = "The network is unavailable, so the most recent copy stored on this phone is shown.";
      elements.notice.hidden = false;
    } else {
      elements.storyList.hidden = true;
      elements.empty.hidden = false;
      elements.empty.querySelector("h2").textContent = "The mobile signal is unavailable";
      elements.empty.querySelector("p").textContent = "Reconnect to the internet and try again.";
      elements.notice.textContent = error.message;
      elements.notice.hidden = false;
    }
  }
}

function resetFilters() {
  state.lane = "All";
  state.category = "All";
  state.source = "All";
  state.search = "";
  state.view = "latest";
  state.timeWindow = "month";
  localStorage.setItem(storageKeys.timeWindow, state.timeWindow);
  syncControlValues();
  render();
}

function openSourceManager() {
  sourceManagerReturnFocus = document.activeElement;
  renderSourceManager();
  elements.sourceManagerPanel.hidden = false;
  document.body.classList.add("source-manager-open");
  window.requestAnimationFrame(() => elements.sourceManagerPanel.querySelector(".source-manager-drawer > header button").focus());
}

function closeSourceManager() {
  elements.sourceManagerPanel.hidden = true;
  document.body.classList.remove("source-manager-open");
  if (sourceManagerReturnFocus instanceof HTMLElement) sourceManagerReturnFocus.focus();
  sourceManagerReturnFocus = null;
}


document.addEventListener("click", (event) => {
  const timeWindowButton = event.target.closest(".time-window-button[data-time-window]");
  if (timeWindowButton) {
    state.timeWindow = Object.prototype.hasOwnProperty.call(TIME_WINDOW_LABELS, timeWindowButton.dataset.timeWindow)
      ? timeWindowButton.dataset.timeWindow
      : "month";
    localStorage.setItem(storageKeys.timeWindow, state.timeWindow);
    render();
    return;
  }

  if (event.target.closest("[data-open-source-manager]")) {
    openSourceManager();
    return;
  }

  if (event.target.closest("[data-close-source-manager]")) {
    closeSourceManager();
    return;
  }

  const sourceToggle = event.target.closest("[data-toggle-source]");
  if (sourceToggle) {
    const sourceId = sourceToggle.dataset.toggleSource;
    if (state.disabledSources.has(sourceId)) state.disabledSources.delete(sourceId);
    else state.disabledSources.add(sourceId);
    if (state.disabledSources.has(state.source)) state.source = "All";
    persistDisabledSources();
    render();
    return;
  }

  const sourceButton = event.target.closest("[data-source-option]");
  if (sourceButton) {
    const source = sourceButton.dataset.sourceOption;
    state.source = source === state.source && source !== "All" ? "All" : source;
    syncControlValues();
    render();
    return;
  }

  const categoryButton = event.target.closest("[data-category]");
  if (categoryButton) {
    const category = categoryButton.dataset.category;
    state.category = category === state.category && category !== "All" ? "All" : category;
    render();
    return;
  }

  const laneButton = event.target.closest("[data-lane]");
  if (laneButton) {
    const lane = laneButton.dataset.lane;
    state.lane = lane === state.lane && lane !== "All" ? "All" : lane;
    render();
    return;
  }

  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    state.view = viewButton.dataset.view;
    render();
    document.querySelector(".feed-section").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  const pinButton = event.target.closest("[data-pin-id]");
  if (pinButton) {
    const id = pinButton.dataset.pinId;
    state.pinned.has(id) ? state.pinned.delete(id) : state.pinned.add(id);
    persistPinned();
    render();
    return;
  }

});

elements.searchInputs.forEach((input) => input.addEventListener("input", () => {
  state.search = input.value;
  syncControlValues();
  render();
}));

elements.clearSearchButtons.forEach((button) => button.addEventListener("click", () => {
  state.search = "";
  syncControlValues();
  button.closest(".search-box").querySelector("input").focus();
  render();
}));

document.querySelector("#enable-all-sources").addEventListener("click", () => {
  state.disabledSources.clear();
  persistDisabledSources();
  render();
});

elements.clearFilters.addEventListener("click", () => {
  if (elements.clearFilters.dataset.action === "enable-sources") {
    state.disabledSources.clear();
    persistDisabledSources();
  }
  resetFilters();
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.installPrompt = event;
  elements.install.hidden = false;
});

elements.install.addEventListener("click", async () => {
  if (!state.installPrompt) return;
  await state.installPrompt.prompt();
  state.installPrompt = null;
  elements.install.hidden = true;
});

window.addEventListener("appinstalled", () => {
  state.installPrompt = null;
  elements.install.hidden = true;
});

window.addEventListener("online", () => loadFeed());
window.addEventListener("offline", () => updateConnection(false, true));

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - state.lastFetchAt > 5 * 60 * 1000) loadFeed();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!elements.sourceManagerPanel.hidden) closeSourceManager();
});

elements.scrollTop.addEventListener("click", () => {
  const firstArticle = elements.storyList.querySelector(".story-card:not(.skeleton)");
  if (!firstArticle) {
    window.scrollTo({ top: 0 });
    return;
  }
  elements.filterDrawer.classList.add("is-jumping");
  setFilterDrawerExpanded(false);
  firstArticle.scrollIntoView({ behavior: "auto", block: "center" });
  window.requestAnimationFrame(() => elements.filterDrawer.classList.remove("is-jumping"));
});

elements.densityToggle.addEventListener("click", () => {
  state.density = state.density === "compact" ? "comfortable" : "compact";
  localStorage.setItem(storageKeys.density, state.density);
  render();
});

document.addEventListener("pointerdown", (event) => {
  if (elements.filterDrawer.classList.contains("is-collapsed") || elements.filterDrawer.contains(event.target)) return;
  setFilterDrawerExpanded(false);
}, { passive: true });

elements.filterDrawerHandle.addEventListener("click", () => {
  if (ignoreNextFilterClick) {
    ignoreNextFilterClick = false;
    return;
  }
  const expanded = elements.filterDrawer.classList.contains("is-collapsed");
  setFilterDrawerExpanded(expanded);
});

elements.filterDrawerHandle.addEventListener("pointerdown", (event) => {
  filterPointerStartY = event.clientY;
  elements.filterDrawerHandle.setPointerCapture?.(event.pointerId);
});

elements.filterDrawerHandle.addEventListener("pointerup", (event) => {
  if (filterPointerStartY === null) return;
  const delta = event.clientY - filterPointerStartY;
  filterPointerStartY = null;
  if (Math.abs(delta) < 18) return;
  ignoreNextFilterClick = true;
  window.setTimeout(() => { ignoreNextFilterClick = false; }, 0);
  setFilterDrawerExpanded(delta > 0);
});

elements.filterDrawerHandle.addEventListener("pointercancel", () => {
  filterPointerStartY = null;
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js?v=20260730").catch(() => {}));
}

loadFeed();
