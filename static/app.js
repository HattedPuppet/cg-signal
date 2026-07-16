const storageKeys = {
  read: "cg-signal:read",
  saved: "cg-signal:saved",
  archived: "cg-signal:archived",
  theme: "cg-signal:theme",
  layout: "cg-signal:layout",
  lane: "cg-signal:lane",
  software: "cg-signal:software",
  stateDirty: "cg-signal:state-dirty",
  stateMigrated: "cg-signal:state-migrated",
};

const state = {
  payload: null,
  articles: [],
  activeSources: new Set(),
  lane: localStorage.getItem(storageKeys.lane) || "All",
  software: localStorage.getItem(storageKeys.software) || "All",
  view: "focus",
  search: "",
  read: readSet(storageKeys.read),
  saved: readSet(storageKeys.saved),
  archived: readSet(storageKeys.archived),
  layout: localStorage.getItem(storageKeys.layout) || "grid",
  briefOpen: false,
  briefArticleIds: [],
};

const FOCUS_MIN_SCORE = 36;
const FOCUS_LIMIT = 30;
const SOFTWARE_GROUP_ORDER = [
  "Unreal Engine",
  "Blender",
  "Substance Painter",
  "Substance Designer",
  "Substance 3D",
  "Houdini",
  "Spine",
  "Production techniques",
  "Industry context",
];
const SOFTWARE_GROUP_DETAILS = {
  "Unreal Engine": "Current tool · engine workflows and production",
  Blender: "Current tool · modeling, animation and rendering",
  "Substance Painter": "Current tool · texturing and material workflows",
  "Substance Designer": "Exploring · procedural material creation",
  "Substance 3D": "Shared Substance ecosystem updates",
  Houdini: "Exploring · procedural workflows, simulation and FX",
  Spine: "Exploring · real-time 2D animation",
  "Production techniques": "Cross-tool craft, pipelines and research",
  "Industry context": "Business developments worth keeping in view",
};
const SOFTWARE_GROUP_COLORS = {
  "Unreal Engine": "#4b75ff",
  Blender: "#f18a21",
  "Substance Painter": "#61d0c8",
  "Substance Designer": "#7fb9ff",
  "Substance 3D": "#9fa9ff",
  Houdini: "#ff7b38",
  Spine: "#e56d9f",
  "Production techniques": "#d7ff57",
  "Industry context": "#f4a261",
};
let stateSaveTimer = null;

const elements = {
  grid: document.querySelector("#story-grid"),
  stories: document.querySelector("#stories"),
  empty: document.querySelector("#empty-state"),
  sourceFilters: document.querySelector("#source-filters"),
  sourceOrbit: document.querySelector("#source-orbit"),
  softwareFilterGroup: document.querySelector("#software-filter-group"),
  softwareFilters: document.querySelector("#software-filters"),
  visibleCount: document.querySelector("#visible-count"),
  allCount: document.querySelector("#all-count"),
  focusCount: document.querySelector("#focus-count"),
  savedCount: document.querySelector("#saved-count"),
  unreadCount: document.querySelector("#unread-count"),
  archivedCount: document.querySelector("#archived-count"),
  heroUnique: document.querySelector("#hero-unique"),
  heroCollapsed: document.querySelector("#hero-collapsed"),
  lastUpdated: document.querySelector("#last-updated"),
  search: document.querySelector("#search-input"),
  refresh: document.querySelector("#refresh-button"),
  layout: document.querySelector("#layout-toggle"),
  notice: document.querySelector("#notice"),
  briefButton: document.querySelector("#brief-button"),
  briefCount: document.querySelector("#brief-count"),
  briefingPanel: document.querySelector("#briefing-panel"),
  briefingContent: document.querySelector("#briefing-content"),
  briefingClose: document.querySelector("#briefing-close"),
  briefingMarkRead: document.querySelector("#briefing-mark-read"),
  briefingDate: document.querySelector("#briefing-date"),
  sortLabel: document.querySelector("#sort-label"),
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

function readSet(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(value) ? value : []);
  } catch {
    return new Set();
  }
}

function saveSet(key, value) {
  localStorage.setItem(key, JSON.stringify([...value].slice(-1500)));
}

function cacheUserState() {
  saveSet(storageKeys.read, state.read);
  saveSet(storageKeys.saved, state.saved);
  saveSet(storageKeys.archived, state.archived);
}

async function persistUserState() {
  cacheUserState();
  try {
    const response = await fetch("/api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        read: [...state.read],
        saved: [...state.saved],
        archived: [...state.archived],
      }),
    });
    if (!response.ok) throw new Error(`State save failed (${response.status})`);
    localStorage.setItem(storageKeys.stateDirty, "0");
    localStorage.setItem(storageKeys.stateMigrated, "1");
  } catch (error) {
    console.warn("CG Signal kept the latest state in this browser.", error);
  }
}

function queueUserStateSave() {
  cacheUserState();
  localStorage.setItem(storageKeys.stateDirty, "1");
  window.clearTimeout(stateSaveTimer);
  stateSaveTimer = window.setTimeout(persistUserState, 140);
}

async function loadUserState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`State request failed (${response.status})`);
    const stored = await response.json();
    const mergeLocal = localStorage.getItem(storageKeys.stateMigrated) !== "1"
      || localStorage.getItem(storageKeys.stateDirty) === "1";
    state.read = new Set(mergeLocal ? [...state.read, ...(stored.read || [])] : (stored.read || []));
    state.saved = new Set(mergeLocal ? [...state.saved, ...(stored.saved || [])] : (stored.saved || []));
    state.archived = new Set(mergeLocal ? [...state.archived, ...(stored.archived || [])] : (stored.archived || []));
    if (mergeLocal) await persistUserState();
    localStorage.setItem(storageKeys.stateMigrated, "1");
    cacheUserState();
  } catch (error) {
    console.warn("CG Signal is using browser state until the local store is available.", error);
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

function briefExcerpt(value = "") {
  const clean = trimSummary(value);
  const sentence = clean.split(/(?<=[.!?。！？])\s*/u)[0] || clean;
  return sentence.length > 180 ? `${sentence.slice(0, 177).trim()}…` : sentence;
}

function priorityScore(article) {
  return Number(article.priority_score || 0);
}

function prioritySort(left, right) {
  return priorityScore(right) - priorityScore(left)
    || new Date(right.published_at).getTime() - new Date(left.published_at).getTime();
}

function balancedPriorityArticles(articles, limit = FOCUS_LIMIT, perSource = 3) {
  const ranked = [...articles].sort(prioritySort);
  const selected = [];
  const selectedIds = new Set();
  const sourceCounts = new Map();
  ranked.forEach((article) => {
    const sourceId = article.source_id || article.source;
    const count = sourceCounts.get(sourceId) || 0;
    if (selected.length >= limit || count >= perSource) return;
    selected.push(article);
    selectedIds.add(article.id);
    sourceCounts.set(sourceId, count + 1);
  });
  if (selected.length < limit) {
    ranked.forEach((article) => {
      if (selected.length < limit && !selectedIds.has(article.id)) selected.push(article);
    });
  }
  return selected;
}

function unreadArticles() {
  return state.articles.filter((article) => !state.read.has(article.id) && !state.archived.has(article.id));
}

function focusArticles() {
  return balancedPriorityArticles(
    unreadArticles().filter((article) => priorityScore(article) >= FOCUS_MIN_SCORE),
  );
}

function dailyBriefArticles() {
  const unread = unreadArticles();
  const technical = balancedPriorityArticles(
    unread.filter((article) => (article.lane || "Tech & Development") === "Tech & Development"),
    6,
    2,
  );
  const industry = balancedPriorityArticles(
    unread.filter((article) => article.lane === "Industry & Business"),
    3,
    2,
  );
  return [...technical, ...industry].sort(prioritySort);
}

function briefingItem(article) {
  const reasons = (article.priority_reasons || []).slice(0, 2).join(" · ") || softwareGroup(article);
  return `
    <a class="briefing-item${article.lane === "Industry & Business" ? " is-industry" : ""}" href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}" style="--story-accent:${escapeHtml(article.accent)}">
      <span class="briefing-item-dot"></span>
      <span class="briefing-item-copy">
        <span class="briefing-item-meta">${escapeHtml(article.source)} · ${escapeHtml(relativeTime(article.published_at))} · Priority ${priorityScore(article)}</span>
        <h3>${escapeHtml(article.title)}</h3>
        <p>${escapeHtml(briefExcerpt(article.summary))}</p>
        <span class="briefing-reason">${escapeHtml(reasons)}</span>
      </span>
      <span class="briefing-item-arrow" aria-hidden="true">↗</span>
    </a>`;
}

function briefingSection(label, articles) {
  if (!articles.length) return "";
  return `
    <p class="briefing-section-label">${escapeHtml(label)}</p>
    <div class="briefing-highlights">${articles.map(briefingItem).join("")}</div>`;
}

function buildBriefing() {
  const unread = unreadArticles();
  const brief = dailyBriefArticles();
  state.briefArticleIds = brief.map((article) => article.id);
  elements.briefCount.textContent = brief.length;
  elements.briefingMarkRead.disabled = brief.length === 0;
  elements.briefingDate.textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date());

  if (!unread.length) {
    elements.briefingContent.innerHTML = `
      <div class="briefing-empty">
        <div>
          <span aria-hidden="true">✓</span>
          <h3>You’re all caught up</h3>
          <p>New stories will appear here the next time the feeds refresh.</p>
        </div>
      </div>`;
    return;
  }

  const categories = new Map();
  unread.forEach((article) => {
    const category = softwareGroup(article);
    categories.set(category, (categories.get(category) || 0) + 1);
  });
  const rankedCategories = [...categories.entries()].sort(([left], [right]) => {
    const leftIndex = SOFTWARE_GROUP_ORDER.indexOf(left);
    const rightIndex = SOFTWARE_GROUP_ORDER.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
  const categoryPills = rankedCategories
    .slice(0, 5)
    .map(([category, count]) => `<span class="briefing-category">${escapeHtml(category)} <strong>${count}</strong></span>`)
    .join("");
  const technical = brief.filter((article) => article.lane !== "Industry & Business");
  const industry = brief.filter((article) => article.lane === "Industry & Business");

  elements.briefingContent.innerHTML = `
    <p class="briefing-lead"><strong>${unread.length} unread ${unread.length === 1 ? "story is" : "stories are"} waiting.</strong> These ${brief.length} stories are the strongest match for your tools and production interests today.</p>
    <div class="briefing-categories">${categoryPills}</div>
    ${briefingSection("Technical priorities", technical)}
    ${briefingSection("Industry pulse", industry)}`;
}

function openBriefing() {
  buildBriefing();
  state.briefOpen = true;
  elements.briefingPanel.hidden = false;
  elements.briefButton.setAttribute("aria-expanded", "true");
  document.body.classList.add("briefing-open");
  elements.briefingClose.focus();
}

function closeBriefing() {
  state.briefOpen = false;
  elements.briefingPanel.hidden = true;
  elements.briefButton.setAttribute("aria-expanded", "false");
  document.body.classList.remove("briefing-open");
  elements.briefButton.focus();
}

function matchesSource(article) {
  return article.sources.some((source) => state.activeSources.has(source.id));
}

function matchesSearch(article, query) {
  if (!query) return true;
  const haystack = [
    article.title,
    article.summary,
    article.source,
    article.lane,
    article.software_group,
    ...(article.software_tags || []),
    ...(article.priority_reasons || []),
    ...article.related.map((item) => `${item.source} ${item.title}`),
  ]
    .join(" ")
    .toLocaleLowerCase();
  return haystack.includes(query);
}

function focusPool() {
  const query = state.search.trim().toLocaleLowerCase();
  const candidates = state.articles.filter((article) => {
    if (!matchesSource(article)) return false;
    if (state.lane !== "All" && (article.lane || "Tech & Development") !== state.lane) return false;
    if (state.archived.has(article.id) || state.read.has(article.id)) return false;
    if (priorityScore(article) < FOCUS_MIN_SCORE) return false;
    return matchesSearch(article, query);
  });
  return balancedPriorityArticles(candidates);
}

function filteredArticles() {
  if (state.view === "focus") {
    const pool = focusPool();
    return state.software === "All"
      ? pool
      : pool.filter((article) => softwareGroup(article) === state.software);
  }

  const query = state.search.trim().toLocaleLowerCase();
  return state.articles.filter((article) => {
    if (!matchesSource(article)) return false;
    if (state.lane !== "All" && (article.lane || "Tech & Development") !== state.lane) return false;
    if (state.view === "archived") {
      if (!state.archived.has(article.id)) return false;
    } else if (state.view === "saved") {
      if (!state.saved.has(article.id)) return false;
    } else {
      if (state.archived.has(article.id)) return false;
      if (state.view === "unread" && state.read.has(article.id)) return false;
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
        <a class="related-row" href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}">
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

function storyCard(article) {
  const read = state.read.has(article.id);
  const saved = state.saved.has(article.id);
  const archived = state.archived.has(article.id);
  const imageUrl = safeUrl(article.image);
  const image = imageUrl === "#" ? "" : `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`;
  const coverage = article.source_count > 1 ? `${article.source_count} sources` : "Single source";
  const lane = article.lane || "Tech & Development";
  const laneLabel = lane === "Industry & Business" ? "Business" : "Tech";
  const category = softwareGroup(article);
  const score = priorityScore(article);
  const priorityBadge = score >= FOCUS_MIN_SCORE
    ? `<span class="priority-badge">Focus ${score}</span>`
    : "";
  const reasons = (article.priority_reasons || [])
    .filter((reason) => state.view !== "focus" || reason !== category)
    .slice(0, 2);
  const reasonMarkup = reasons.length
    ? `<div class="story-reasons">${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>`
    : "";
  return `
    <article class="story-card${read ? " is-read" : ""}${archived ? " is-archived" : ""}" data-id="${escapeHtml(article.id)}" style="--story-accent:${escapeHtml(article.accent)}">
      <div class="story-visual${image ? "" : " image-failed"}" data-category="${escapeHtml(category)}">
        ${image}
        <div class="visual-overlay"></div>
        <span class="visual-category">${escapeHtml(category)}</span>
        ${priorityBadge}
      </div>
      <div class="story-body">
        <div class="story-meta">
          <div class="story-classification">
            <span class="source-label">${escapeHtml(article.source)}</span>
            <span class="lane-badge${lane === "Industry & Business" ? " is-industry" : ""}">${laneLabel}</span>
          </div>
          <time class="story-time" datetime="${escapeHtml(article.published_at)}" title="${escapeHtml(new Date(article.published_at).toLocaleString())}">${escapeHtml(relativeTime(article.published_at))}</time>
        </div>
        <h2 class="story-title">
          <a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}">${escapeHtml(article.title)}</a>
        </h2>
        <p class="story-summary">${escapeHtml(trimSummary(article.summary))}</p>
        ${reasonMarkup}
        <div class="story-footer">
          <div class="source-stack">${sourceStack(article)}<span class="coverage-label">${coverage}</span></div>
          <div class="card-actions">
            <button class="archive-button${archived ? " is-archived" : ""}" type="button" data-archive-id="${escapeHtml(article.id)}" aria-label="${archived ? "Restore from archive" : "Archive story"}" aria-pressed="${archived}">${archived ? "↥" : "⌄"}</button>
            <button class="save-button${saved ? " is-saved" : ""}" type="button" data-save-id="${escapeHtml(article.id)}" aria-label="${saved ? "Remove from saved" : "Save story"}" aria-pressed="${saved}">${saved ? "★" : "☆"}</button>
          </div>
        </div>
        ${relatedCoverage(article)}
      </div>
    </article>`;
}

function softwareGroup(article) {
  if (article.software_group) return article.software_group;
  const matchedReason = SOFTWARE_GROUP_ORDER.find((group) => (article.priority_reasons || []).includes(group));
  if (matchedReason) return matchedReason;
  return article.lane === "Industry & Business" ? "Industry context" : "Production techniques";
}

function renderSoftwareFilters(pool) {
  const isFocusView = state.view === "focus";
  elements.softwareFilterGroup.hidden = !isFocusView;
  if (!isFocusView) return;

  const counts = new Map();
  pool.forEach((article) => {
    const group = softwareGroup(article);
    counts.set(group, (counts.get(group) || 0) + 1);
  });
  const categories = [...counts.keys()].sort((left, right) => {
    const leftIndex = SOFTWARE_GROUP_ORDER.indexOf(left);
    const rightIndex = SOFTWARE_GROUP_ORDER.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
  if (state.software !== "All" && !counts.has(state.software)) {
    state.software = "All";
    localStorage.setItem(storageKeys.software, state.software);
  }

  const buttons = [
    { label: "All focus", value: "All", count: pool.length, color: "#d7ff57" },
    ...categories.map((category) => ({
      label: category,
      value: category,
      count: counts.get(category),
      color: SOFTWARE_GROUP_COLORS[category] || "#d7ff57",
    })),
  ];
  elements.softwareFilters.innerHTML = buttons
    .map((button) => {
      const active = state.software === button.value;
      return `
        <button class="software-button${active ? " is-active" : ""}" type="button" data-software="${escapeHtml(button.value)}" aria-pressed="${active}" style="--category-accent:${escapeHtml(button.color)}">
          <span>${escapeHtml(button.label)}</span>
          <strong>${button.count}</strong>
        </button>`;
    })
    .join("");
}

function focusGroupMarkup(articles) {
  const groups = new Map();
  articles.forEach((article) => {
    const group = softwareGroup(article);
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
  return orderedGroups
    .map(([group, groupedArticles], index) => `
      <section class="focus-group" aria-labelledby="focus-group-${index}">
        <header class="focus-group-header">
          <div>
            <span class="focus-group-kicker">Software signal ${String(index + 1).padStart(2, "0")}</span>
            <h2 id="focus-group-${index}">${escapeHtml(group)}</h2>
            <p>${escapeHtml(SOFTWARE_GROUP_DETAILS[group] || "Related production coverage")}</p>
          </div>
          <strong>${groupedArticles.length} ${groupedArticles.length === 1 ? "story" : "stories"}</strong>
        </header>
        <div class="focus-group-grid">${groupedArticles.map(storyCard).join("")}</div>
      </section>`)
    .join("");
}

function render() {
  if (!state.payload) return;
  const pool = state.view === "focus" ? focusPool() : [];
  renderSoftwareFilters(pool);
  const visible = state.view === "focus"
    ? (state.software === "All" ? pool : pool.filter((article) => softwareGroup(article) === state.software))
    : filteredArticles();
  elements.grid.classList.toggle("is-list", state.layout === "list");
  elements.grid.classList.toggle("is-grouped", state.view === "focus");
  elements.grid.classList.remove("loading-grid");
  elements.grid.innerHTML = state.view === "focus"
    ? focusGroupMarkup(visible)
    : visible.map(storyCard).join("");
  elements.empty.hidden = visible.length > 0;
  elements.grid.hidden = visible.length === 0;
  const emptyCopy = {
    focus: ["Focus inbox cleared", "You have reviewed today’s strongest matches. Check Latest signal for everything else."],
    saved: ["Nothing saved yet", "Use the star on a story to keep it in your research collection."],
    unread: ["You’re all caught up", "New unread stories will appear after the next feed refresh."],
    archived: ["The archive is empty", "Archived stories stay out of your active feed and can be restored here."],
  }[state.view] || ["No signal here yet", "Try another category, clear your source filters, or refresh the feeds."];
  elements.empty.querySelector("h2").textContent = emptyCopy[0];
  elements.empty.querySelector("p").textContent = emptyCopy[1];
  elements.visibleCount.textContent = `${visible.length} ${visible.length === 1 ? "story" : "stories"}`;
  elements.allCount.textContent = state.articles.filter((article) => !state.archived.has(article.id)).length;
  elements.focusCount.textContent = focusArticles().length;
  elements.savedCount.textContent = state.articles.filter((article) => state.saved.has(article.id)).length;
  elements.unreadCount.textContent = unreadArticles().length;
  elements.archivedCount.textContent = state.articles.filter((article) => state.archived.has(article.id)).length;
  elements.briefCount.textContent = dailyBriefArticles().length;
  elements.sortLabel.textContent = {
    focus: "Grouped by software",
    saved: "Saved collection",
    unread: "Newest unread",
    archived: "Recently archived",
    all: "Newest first",
  }[state.view] || "Newest first";
  if (state.briefOpen) buildBriefing();

  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll(".lane-button[data-lane]").forEach((button) => {
    const lane = button.dataset.lane;
    const active = lane === state.lane;
    const count = lane === "All"
      ? state.articles.length
      : state.articles.filter((article) => (article.lane || "Tech & Development") === lane).length;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    button.querySelector("strong").textContent = count;
  });
  elements.layout.querySelector("span:first-child").textContent = state.layout === "grid" ? "▦" : "☷";
}

function sourceCount(sourceId) {
  return state.articles.filter((article) => article.sources.some((source) => source.id === sourceId)).length;
}

function renderSources(sources) {
  elements.sourceFilters.innerHTML = sources
    .map(
      (source) => `
        <button class="source-button${state.activeSources.has(source.id) ? "" : " is-muted"}" type="button" data-source-id="${escapeHtml(source.id)}" style="--source-accent:${escapeHtml(source.accent)}" aria-pressed="${state.activeSources.has(source.id)}">
          <span class="source-dot"></span>
          <span>${escapeHtml(source.name)}</span>
          <strong>${sourceCount(source.id)}</strong>
        </button>`,
    )
    .join("");
  elements.sourceOrbit.innerHTML = sources
    .map(
      (source) =>
        `<i class="${source.ok ? "" : "is-offline"}" title="${escapeHtml(`${source.name}: ${source.ok ? "connected" : "unavailable"}`)}" style="--source-accent:${escapeHtml(source.accent)}"></i>`,
    )
    .join("");
}

function updateDashboard(payload) {
  state.payload = payload;
  state.articles = payload.articles || [];
  if (!state.activeSources.size) {
    (payload.sources || []).forEach((source) => state.activeSources.add(source.id));
  }
  elements.heroUnique.textContent = payload.unique_count ?? state.articles.length;
  elements.heroCollapsed.textContent = payload.duplicates_collapsed ?? 0;
  elements.lastUpdated.textContent = payload.generated_at
    ? `Updated ${relativeTime(payload.generated_at)}${payload.cached ? " · local cache" : ""}`
    : "Update time unavailable";
  renderSources(payload.sources || []);
  showWarnings(payload);
  render();
}

function showWarnings(payload) {
  const warnings = payload.warnings || [];
  if (!warnings.length && !payload.stale) {
    elements.notice.hidden = true;
    return;
  }
  const lead = payload.stale
    ? "Live refresh was unavailable, so the most recent local copy is shown."
    : "Some sources could not be refreshed; the rest of the board is current.";
  elements.notice.textContent = `${lead} ${warnings.map((item) => item.split(":")[0]).join(", ")}`;
  elements.notice.hidden = false;
}

async function loadFeed(force = false) {
  elements.refresh.classList.add("is-loading");
  elements.refresh.disabled = true;
  elements.stories.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/feed${force ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Feed request failed (${response.status})`);
    const payload = await response.json();
    if (payload.error) throw new Error(payload.detail || payload.error);
    updateDashboard(payload);
  } catch (error) {
    elements.notice.textContent = `The feeds could not be gathered. ${error.message}`;
    elements.notice.hidden = false;
    elements.grid.hidden = true;
    elements.empty.hidden = false;
    elements.empty.querySelector("h2").textContent = "The signal is temporarily quiet";
    elements.empty.querySelector("p").textContent = "Check your connection, then refresh the dashboard.";
  } finally {
    elements.refresh.classList.remove("is-loading");
    elements.refresh.disabled = false;
    elements.stories.setAttribute("aria-busy", "false");
  }
}

document.addEventListener("click", (event) => {
  const saveButton = event.target.closest("[data-save-id]");
  if (saveButton) {
    const id = saveButton.dataset.saveId;
    state.saved.has(id) ? state.saved.delete(id) : state.saved.add(id);
    queueUserStateSave();
    render();
    return;
  }

  const archiveButton = event.target.closest("[data-archive-id]");
  if (archiveButton) {
    const id = archiveButton.dataset.archiveId;
    state.archived.has(id) ? state.archived.delete(id) : state.archived.add(id);
    queueUserStateSave();
    render();
    return;
  }

  const storyLink = event.target.closest("[data-read-id]");
  if (storyLink) {
    state.read.add(storyLink.dataset.readId);
    queueUserStateSave();
    window.setTimeout(render, 120);
    return;
  }

  const sourceButton = event.target.closest("[data-source-id]");
  if (sourceButton) {
    const id = sourceButton.dataset.sourceId;
    state.activeSources.has(id) ? state.activeSources.delete(id) : state.activeSources.add(id);
    renderSources(state.payload.sources || []);
    render();
    return;
  }

  const softwareButton = event.target.closest(".software-button");
  if (softwareButton) {
    state.software = softwareButton.dataset.software;
    localStorage.setItem(storageKeys.software, state.software);
    render();
    return;
  }

  const laneButton = event.target.closest(".lane-button");
  if (laneButton) {
    state.lane = laneButton.dataset.lane;
    localStorage.setItem(storageKeys.lane, state.lane);
    render();
    return;
  }

  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    state.view = viewButton.dataset.view;
    render();
  }
});

elements.search.addEventListener("input", () => {
  state.search = elements.search.value;
  render();
});

elements.refresh.addEventListener("click", () => loadFeed(true));

elements.briefButton.addEventListener("click", openBriefing);

document.querySelectorAll("[data-close-briefing]").forEach((button) => {
  button.addEventListener("click", closeBriefing);
});

elements.briefingMarkRead.addEventListener("click", () => {
  state.briefArticleIds.forEach((id) => state.read.add(id));
  queueUserStateSave();
  render();
});

elements.layout.addEventListener("click", () => {
  state.layout = state.layout === "grid" ? "list" : "grid";
  localStorage.setItem(storageKeys.layout, state.layout);
  render();
});

document.querySelector("#reset-sources").addEventListener("click", () => {
  state.activeSources = new Set((state.payload.sources || []).map((source) => source.id));
  renderSources(state.payload.sources || []);
  render();
});

document.querySelector("#clear-filters").addEventListener("click", () => {
  state.activeSources = new Set((state.payload.sources || []).map((source) => source.id));
  state.lane = "All";
  state.software = "All";
  localStorage.setItem(storageKeys.lane, state.lane);
  localStorage.setItem(storageKeys.software, state.software);
  state.view = "focus";
  state.search = "";
  elements.search.value = "";
  renderSources(state.payload.sources || []);
  render();
});

document.querySelector("#mark-all-read").addEventListener("click", () => {
  filteredArticles().forEach((article) => state.read.add(article.id));
  queueUserStateSave();
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
  } else if (event.key === "Escape" && state.briefOpen) {
    closeBriefing();
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

async function initialize() {
  await loadUserState();
  await loadFeed();
}

initialize();
