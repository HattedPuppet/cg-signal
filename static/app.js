const storageKeys = {
  read: "cg-signal:read",
  saved: "cg-signal:saved",
  archived: "cg-signal:archived",
  theme: "cg-signal:theme",
  layout: "cg-signal:layout",
  lane: "cg-signal:lane",
  software: "cg-signal:software",
  topics: "cg-signal:topics",
  notes: "cg-signal:notes",
  feedback: "cg-signal:feedback",
  mutedSources: "cg-signal:muted-sources",
  reducedSources: "cg-signal:reduced-sources",
  lastVisit: "cg-signal:last-visit",
  stateDirty: "cg-signal:state-dirty",
  stateMigrated: "cg-signal:state-migrated",
};

const state = {
  payload: null,
  articles: [],
  archiveArticles: [],
  archiveTotal: 0,
  archiveHasMore: false,
  archiveLoading: false,
  archiveRequestId: 0,
  managedSources: [],
  activeSources: new Set(),
  lane: localStorage.getItem(storageKeys.lane) || "All",
  software: readFilterSet(storageKeys.software),
  topics: readFilterSet(storageKeys.topics),
  view: "all",
  search: "",
  read: readSet(storageKeys.read),
  saved: readSet(storageKeys.saved),
  archived: readSet(storageKeys.archived),
  notes: readObject(storageKeys.notes),
  feedback: readFeedback(storageKeys.feedback),
  mutedSources: readSet(storageKeys.mutedSources),
  reducedSources: readSet(storageKeys.reducedSources),
  layout: localStorage.getItem(storageKeys.layout) || "grid",
  briefOpen: false,
  briefArticleIds: [],
  visibleArticleIds: [],
  keyboardArticleId: null,
  knownArticleIds: new Set(),
  knownSourceIds: new Set(),
  firstFeedLoaded: false,
  sessionCutoff: parseStoredDate(localStorage.getItem(storageKeys.lastVisit)),
  sessionStartedAt: new Date().toISOString(),
};

const SOFTWARE_GROUP_ORDER = [
  "Unreal Engine",
  "Unity",
  "Blender",
  "Substance 3D",
  "Houdini",
  "AI",
  "Production techniques",
  "Industry context",
];
const SOFTWARE_GROUP_COLORS = {
  "Unreal Engine": "#4b75ff",
  Unity: "#222c37",
  Blender: "#f18a21",
  "Substance 3D": "#9fa9ff",
  Houdini: "#ff7b38",
  AI: "#a77bff",
  "Production techniques": "#d7ff57",
  "Industry context": "#f4a261",
};
const TOPIC_ORDER = [
  "Modeling & sculpting",
  "Materials & texturing",
  "Animation, rigging & mocap",
  "Lighting & rendering",
  "VFX, simulation & procedural",
  "Technical art & optimization",
  "Pipeline, tools & automation",
  "Game design & development",
  "Breakdowns & production stories",
  "Research & emerging tech",
  "Releases & product updates",
  "Assets & inspiration",
  "Other production",
];
const TOPIC_COLORS = {
  "Modeling & sculpting": "#cb7cff",
  "Materials & texturing": "#61d0c8",
  "Animation, rigging & mocap": "#ff7597",
  "Lighting & rendering": "#ffd166",
  "VFX, simulation & procedural": "#ff7b38",
  "Technical art & optimization": "#4b75ff",
  "Pipeline, tools & automation": "#66b8ff",
  "Game design & development": "#9ddc65",
  "Breakdowns & production stories": "#ea8db3",
  "Research & emerging tech": "#8e9dff",
  "Releases & product updates": "#62c7db",
  "Assets & inspiration": "#d6a6ff",
  "Other production": "#b6bfad",
};
let stateSaveTimer = null;
let noteSaveTimer = null;
let backgroundRefreshTimer = null;
let archiveSearchTimer = null;

const elements = {
  grid: document.querySelector("#story-grid"),
  stories: document.querySelector("#stories"),
  empty: document.querySelector("#empty-state"),
  sourceFilters: document.querySelector("#source-filters"),
  sourceOrbit: document.querySelector("#source-orbit"),
  softwareFilterGroup: document.querySelector("#software-filter-group"),
  softwareFilters: document.querySelector("#software-filters"),
  topicFilterGroup: document.querySelector("#topic-filter-group"),
  topicFilters: document.querySelector("#topic-filters"),
  visibleCount: document.querySelector("#visible-count"),
  newSince: document.querySelector("#new-since"),
  allCount: document.querySelector("#all-count"),
  savedCount: document.querySelector("#saved-count"),
  unreadCount: document.querySelector("#unread-count"),
  archivedCount: document.querySelector("#archived-count"),
  historyCount: document.querySelector("#history-count"),
  heroUnique: document.querySelector("#hero-unique"),
  heroCollapsed: document.querySelector("#hero-collapsed"),
  lastUpdated: document.querySelector("#last-updated"),
  search: document.querySelector("#search-input"),
  searchHelp: document.querySelector("#search-help"),
  scrollTop: document.querySelector("#scroll-top-button"),
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

const SEARCH_ALIASES = new Map([
  ["unreal", { field: "software", value: "Unreal Engine" }],
  ["unreal-engine", { field: "software", value: "Unreal Engine" }],
  ["ue", { field: "software", value: "Unreal Engine" }],
  ["ue5", { field: "software", value: "Unreal Engine" }],
  ["unity", { field: "software", value: "Unity" }],
  ["blender", { field: "software", value: "Blender" }],
  ["houdini", { field: "software", value: "Houdini" }],
  ["painter", { field: "software", value: "Substance 3D" }],
  ["substance-painter", { field: "software", value: "Substance 3D" }],
  ["designer", { field: "software", value: "Substance 3D" }],
  ["substance-designer", { field: "software", value: "Substance 3D" }],
  ["substance", { field: "software", value: "Substance 3D" }],
  ["production", { field: "software", value: "Production techniques" }],
  ["production-techniques", { field: "software", value: "Production techniques" }],
  ["industry", { field: "software", value: "Industry context" }],
  ["industry-context", { field: "software", value: "Industry context" }],
  ["ai", { field: "software", value: "AI" }],
  ["genai", { field: "software", value: "AI" }],
]);

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

function readObject(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function normalizeSoftwareCategory(value) {
  if (["Substance Painter", "Substance Designer", "Substance 3D"].includes(value)) {
    return "Substance 3D";
  }
  if (value === "Spine") return "";
  return value;
}

function normalizeFeedbackItem(item) {
  return {
    ...item,
    software_tags: [...new Set((item.software_tags || []).map(normalizeSoftwareCategory).filter(Boolean))],
  };
}

function readFeedback(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return new Map((Array.isArray(value) ? value : [])
      .filter((item) => item?.id)
      .map(normalizeFeedbackItem)
      .map((item) => [item.id, item]));
  } catch {
    return new Map();
  }
}

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

function saveSet(key, value) {
  localStorage.setItem(key, JSON.stringify([...value].slice(-1500)));
}

function cacheUserState() {
  saveSet(storageKeys.read, state.read);
  saveSet(storageKeys.saved, state.saved);
  saveSet(storageKeys.archived, state.archived);
  localStorage.setItem(storageKeys.notes, JSON.stringify(state.notes));
  localStorage.setItem(storageKeys.feedback, JSON.stringify([...state.feedback.values()].slice(-500)));
  saveSet(storageKeys.mutedSources, state.mutedSources);
  saveSet(storageKeys.reducedSources, state.reducedSources);
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
        notes: state.notes,
        feedback: [...state.feedback.values()],
        muted_sources: [...state.mutedSources],
        reduced_sources: [...state.reducedSources],
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
    state.notes = mergeLocal ? { ...(stored.notes || {}), ...state.notes } : (stored.notes || {});
    state.feedback = new Map((mergeLocal
      ? [...(stored.feedback || []), ...state.feedback.values()]
      : (stored.feedback || [])).map(normalizeFeedbackItem).map((item) => [item.id, item]));
    state.mutedSources = new Set(mergeLocal
      ? [...state.mutedSources, ...(stored.muted_sources || [])]
      : (stored.muted_sources || []));
    state.reducedSources = new Set(mergeLocal
      ? [...state.reducedSources, ...(stored.reduced_sources || [])]
      : (stored.reduced_sources || []));
    state.mutedSources.forEach((id) => state.reducedSources.delete(id));
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

function sharesAny(left = [], right = []) {
  const rightValues = new Set(right);
  return left.some((value) => rightValues.has(value));
}

function personalizedScore(article) {
  let score = priorityScore(article);
  const sourceIds = (article.sources || []).map((source) => source.id);
  if (sourceIds.some((id) => state.reducedSources.has(id))) score -= 20;
  state.feedback.forEach((feedback) => {
    let weight = 0;
    if (feedback.source_id && sourceIds.includes(feedback.source_id)) weight += 2;
    if (sharesAny(feedback.software_tags, articleSoftwareCategories(article))) weight += 8;
    if (sharesAny(feedback.topic_tags, articleTopics(article))) weight += 5;
    score += feedback.value * weight;
  });
  return score;
}

function signedScore(value) {
  if (!value) return "0";
  return value > 0 ? `+${value}` : `−${Math.abs(value)}`;
}

function personalizationAdjustment(article) {
  return personalizedScore(article) - priorityScore(article);
}

function preferenceSignals() {
  const signals = new Map();
  const sourceNames = new Map((state.payload?.sources || []).map((source) => [source.id, source.name]));
  const add = (kind, label, value) => {
    if (!label) return;
    const key = `${kind}:${label}`;
    const current = signals.get(key) || { kind, label, value: 0 };
    current.value += value;
    signals.set(key, current);
  };
  state.feedback.forEach((feedback) => {
    (feedback.software_tags || []).forEach((label) => add("Category", label, feedback.value * 8));
    (feedback.topic_tags || []).forEach((label) => add("Technique", label, feedback.value * 5));
    add("Source", sourceNames.get(feedback.source_id) || feedback.source_id, feedback.value * 2);
  });
  return [...signals.values()]
    .filter((signal) => signal.value)
    .sort((left, right) => Math.abs(right.value) - Math.abs(left.value) || left.label.localeCompare(right.label));
}

function preferenceTuningMarkup() {
  const feedback = [...state.feedback.values()];
  const moreCount = feedback.filter((item) => item.value === 1).length;
  const lessCount = feedback.filter((item) => item.value === -1).length;
  const signals = preferenceSignals().slice(0, 8);
  const status = feedback.length ? `${moreCount} more · ${lessCount} less` : "No signals yet";
  const signalMarkup = signals.length
    ? `<div class="tuning-signals">${signals.map((signal) => `
        <span class="tuning-signal${signal.value < 0 ? " is-negative" : ""}" title="${escapeHtml(signal.kind)} preference">
          <b>${signal.value > 0 ? "↑" : "↓"}</b>${escapeHtml(signal.label)} <em>${signedScore(signal.value)}</em>
        </span>`).join("")}</div>`
    : "";
  const copy = feedback.length
    ? "Matching categories change a story by ±8 per signal, techniques by ±5, and sources by ±2. A reduced source applies −20. These adjustments affect only Daily Brief ranking."
    : "Use the up or down arrows on story cards. CG Signal will learn from the story’s categories, techniques, and source; Latest Signal always stays chronological.";
  return `
    <details class="preference-tuning"${feedback.length ? " open" : ""}>
      <summary>Preference tuning <span>${escapeHtml(status)}</span></summary>
      <div class="preference-tuning-body">
        ${signalMarkup}
        <p>${escapeHtml(copy)}</p>
        ${feedback.length ? '<button class="reset-tuning-button" type="button" data-reset-feedback>Reset More/Less tuning</button>' : ""}
      </div>
    </details>`;
}

function personalizedSort(left, right) {
  return personalizedScore(right) - personalizedScore(left)
    || prioritySort(left, right);
}

function balancedPriorityArticles(articles, limit = 30, perSource = 3) {
  const ranked = [...articles].sort(personalizedSort);
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
  return state.articles.filter((article) => (
    !state.read.has(article.id)
    && !state.archived.has(article.id)
    && (article.sources || []).some((source) => !state.mutedSources.has(source.id))
  ));
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
  return [...technical, ...industry].sort(personalizedSort);
}

function briefingItem(article) {
  const reasons = (article.priority_reasons || []).slice(0, 2).join(" · ") || softwareGroup(article);
  const adjustment = personalizationAdjustment(article);
  const tuning = adjustment ? ` · tuning ${signedScore(adjustment)}` : "";
  return `
    <a class="briefing-item${article.lane === "Industry & Business" ? " is-industry" : ""}" href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}" style="--story-accent:${escapeHtml(article.accent)}">
      <span class="briefing-item-dot"></span>
      <span class="briefing-item-copy">
        <span class="briefing-item-meta">${escapeHtml(article.source)} · ${escapeHtml(relativeTime(article.published_at))} · Brief score ${personalizedScore(article)}${escapeHtml(tuning)}</span>
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
    ${preferenceTuningMarkup()}
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
  return article.sources.some(
    (source) => state.activeSources.has(source.id) && !state.mutedSources.has(source.id),
  );
}

function searchDocument(article) {
  return [
    article.title,
    article.summary,
    article.source,
    article.lane,
    article.software_group,
    ...(article.software_tags || []),
    ...(article.topic_tags || []),
    ...(article.priority_reasons || []),
    ...article.related.map((item) => `${item.source} ${item.title}`),
    state.notes[article.id] || "",
  ]
    .join(" ")
    .toLocaleLowerCase();
}

function normalizeSearchQuery(query) {
  return query
    .replace(/#unreal\s+engine\b/giu, '#software:"Unreal Engine"')
    .replace(/#substance\s+(?:painter|designer|3d)\b/giu, '#software:"Substance 3D"')
    .replace(/#production\s+techniques\b/giu, '#software:"Production techniques"')
    .replace(/#industry\s+context\b/giu, '#software:"Industry context"');
}

function searchTokens(query) {
  const normalized = normalizeSearchQuery(query.trim());
  if (!normalized) return [];
  const rawTokens = normalized.match(/-?#(?:software|topic|source|is):(?:"[^"]+"|'[^']+'|\S+)|-?#[\p{L}\p{N}_-]+|-?"[^"]+"|-?\S+/giu) || [];
  return rawTokens.map((raw) => {
    const negative = raw.startsWith("-");
    let token = negative ? raw.slice(1) : raw;
    if (token.startsWith("#")) {
      token = token.slice(1);
      const separator = token.indexOf(":");
      if (separator > 0) {
        const field = token.slice(0, separator).toLocaleLowerCase();
        let value = token.slice(separator + 1).replace(/^['"]|['"]$/g, "").toLocaleLowerCase();
        if (field === "software" && ["substance painter", "substance designer"].includes(value)) {
          value = "substance 3d";
        }
        return { negative, field, value };
      }
      const aliasKey = token.toLocaleLowerCase().replaceAll("_", "-");
      const alias = SEARCH_ALIASES.get(aliasKey);
      if (alias) return { negative, field: alias.field, value: alias.value.toLocaleLowerCase() };
      return { negative, field: "text", value: token.toLocaleLowerCase() };
    }
    return { negative, field: "text", value: token.replace(/^['"]|['"]$/g, "").toLocaleLowerCase() };
  }).filter((token) => token.value);
}

function articleIsNew(article) {
  return Boolean(state.sessionCutoff)
    && new Date(article.published_at).getTime() > state.sessionCutoff.getTime();
}

function matchesSearchToken(article, token) {
  const value = token.value;
  if (token.field === "software") {
    return articleSoftwareCategories(article).some((item) => item.toLocaleLowerCase().includes(value));
  }
  if (token.field === "topic") {
    return articleTopics(article).some((item) => item.toLocaleLowerCase().includes(value));
  }
  if (token.field === "source") {
    return (article.sources || []).some((source) => `${source.id} ${source.name}`.toLocaleLowerCase().includes(value));
  }
  if (token.field === "is") {
    const statusMatches = {
      unread: !state.read.has(article.id),
      read: state.read.has(article.id),
      saved: state.saved.has(article.id),
      library: state.saved.has(article.id),
      archived: state.archived.has(article.id),
      new: articleIsNew(article),
      liked: state.feedback.get(article.id)?.value === 1,
      reduced: state.feedback.get(article.id)?.value === -1,
    };
    return Boolean(statusMatches[value]);
  }
  return searchDocument(article).includes(value);
}

function matchesSearch(article, query) {
  return searchTokens(query).every((token) => {
    const matches = matchesSearchToken(article, token);
    return token.negative ? !matches : matches;
  });
}

function latestPool() {
  const query = state.search;
  return state.articles.filter((article) => {
    if (!matchesSource(article)) return false;
    if (state.lane !== "All" && (article.lane || "Tech & Development") !== state.lane) return false;
    if (state.archived.has(article.id)) return false;
    return matchesSearch(article, query);
  });
}

function articleSoftwareCategories(article) {
  const tags = [...new Set((article.software_tags || []).map(normalizeSoftwareCategory))].filter(Boolean);
  return tags.length ? tags : [softwareGroup(article)];
}

function articleTopics(article) {
  if (softwareGroup(article) !== "Production techniques") return [];
  const tags = [...new Set(article.topic_tags || [])].filter(Boolean);
  if (tags.length) return tags;
  return ["Other production"];
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

function usesArchiveView() {
  return state.view === "history" || state.view === "saved" || state.view === "archived";
}

function archiveViewQuery() {
  const status = state.view === "saved"
    ? "#is:saved"
    : state.view === "archived" ? "#is:archived" : "";
  return [state.search.trim(), status].filter(Boolean).join(" ");
}

function archiveSourceFilter() {
  const enabledIds = (state.payload?.sources || []).map((source) => source.id);
  const selectedIds = enabledIds.filter(
    (sourceId) => state.activeSources.has(sourceId) && !state.mutedSources.has(sourceId),
  );
  if (selectedIds.length === enabledIds.length) return [];
  return selectedIds.length ? selectedIds : ["__none__"];
}

async function loadArchive({ append = false } = {}) {
  if (!state.payload || !usesArchiveView()) return;
  const requestId = ++state.archiveRequestId;
  const offset = append ? state.archiveArticles.length : 0;
  if (!append) {
    state.archiveArticles = [];
    state.archiveTotal = 0;
    state.archiveHasMore = false;
  }
  state.archiveLoading = true;
  render();
  const parameters = new URLSearchParams({
    q: archiveViewQuery(),
    lane: state.lane,
    limit: "60",
    offset: String(offset),
  });
  const sourceIds = archiveSourceFilter();
  if (sourceIds.length) parameters.set("sources", sourceIds.join(","));
  if (state.sessionCutoff) parameters.set("new_after", state.sessionCutoff.toISOString());
  try {
    const response = await fetch(`/api/archive?${parameters}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.detail || payload.error || `Archive request failed (${response.status})`);
    if (requestId !== state.archiveRequestId) return;
    state.archiveArticles = append
      ? [...state.archiveArticles, ...(payload.articles || [])]
      : (payload.articles || []);
    state.archiveTotal = payload.total || 0;
    state.archiveHasMore = Boolean(payload.has_more);
    if (elements.historyCount) elements.historyCount.textContent = payload.archive_count ?? state.archiveTotal;
  } catch (error) {
    if (requestId !== state.archiveRequestId) return;
    elements.notice.textContent = `Article history could not be searched. ${error.message}`;
    elements.notice.hidden = false;
  } finally {
    if (requestId === state.archiveRequestId) {
      state.archiveLoading = false;
      render();
    }
  }
}

function filteredArticles() {
  if (state.view === "all") {
    return applyFacetFilters(latestPool());
  }

  if (usesArchiveView()) {
    return state.archiveArticles.filter((article) => {
      if (state.view === "saved" && !state.saved.has(article.id)) return false;
      if (state.view === "archived" && !state.archived.has(article.id)) return false;
      return true;
    });
  }

  const query = state.search;
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

function feedbackControls(article) {
  const current = state.feedback.get(article.id)?.value || 0;
  return `
    <div class="feedback-controls" role="group" aria-label="Tune recommendations from this story">
      <button class="feedback-button${current === 1 ? " is-active" : ""}" type="button" data-feedback-id="${escapeHtml(article.id)}" data-feedback-value="1" aria-label="More like this" aria-pressed="${current === 1}" title="More like this">↑</button>
      <button class="feedback-button${current === -1 ? " is-active is-negative" : ""}" type="button" data-feedback-id="${escapeHtml(article.id)}" data-feedback-value="-1" aria-label="Less like this" aria-pressed="${current === -1}" title="Less like this">↓</button>
    </div>`;
}

function sourcePreferenceMenu(article) {
  const sourceId = article.source_id || article.sources?.[0]?.id || "";
  if (!sourceId) return "";
  const muted = state.mutedSources.has(sourceId);
  const reduced = state.reducedSources.has(sourceId);
  return `
    <details class="source-menu">
      <summary aria-label="Source preferences for ${escapeHtml(article.source)}" title="Source preferences">•••</summary>
      <div class="source-menu-panel">
        <strong>${escapeHtml(article.source)}</strong>
        <button type="button" data-source-action="${muted || reduced ? "restore" : "reduce"}" data-preference-source="${escapeHtml(sourceId)}">${muted ? "Restore this source" : reduced ? "Restore normal priority" : "Show less from this source"}</button>
        ${muted ? "" : `<button type="button" data-source-action="mute" data-preference-source="${escapeHtml(sourceId)}">Mute this source</button>`}
      </div>
    </details>`;
}

function libraryNote(article) {
  if (state.view !== "saved") return "";
  return `
    <div class="library-note">
      <label for="note-${escapeHtml(article.id)}">Research note</label>
      <textarea id="note-${escapeHtml(article.id)}" data-note-id="${escapeHtml(article.id)}" rows="2" maxlength="4000" placeholder="Why is this useful? Add a technique, takeaway, or next step…">${escapeHtml(state.notes[article.id] || "")}</textarea>
    </div>`;
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
    <article class="story-card${read ? " is-read" : ""}${archived ? " is-archived" : ""}${state.keyboardArticleId === article.id ? " is-keyboard-active" : ""}" data-id="${escapeHtml(article.id)}" tabindex="-1" style="--story-accent:${escapeHtml(article.accent)}">
      <div class="story-visual${image ? "" : " image-failed"}" data-category="${escapeHtml(category)}">
        ${image}
        <div class="visual-overlay"></div>
        <span class="visual-category">${escapeHtml(category)}</span>
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
        ${libraryNote(article)}
        <div class="story-footer">
          <div class="source-stack">${sourceStack(article)}<span class="coverage-label">${coverage}</span></div>
          <div class="card-actions">
            ${feedbackControls(article)}
            <button class="archive-button${archived ? " is-archived" : ""}" type="button" data-archive-id="${escapeHtml(article.id)}" aria-label="${archived ? "Restore from archive" : "Archive story"}" aria-pressed="${archived}">${archived ? "↥" : "⌄"}</button>
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
  if (!state.sessionCutoff || state.view !== "all") return visible.map(storyCard).join("");
  const newCount = visible.filter(articleIsNew).length;
  if (!newCount || newCount === visible.length) {
    return `${newCount ? newSinceDivider(newCount) : ""}${visible.map(storyCard).join("")}`;
  }
  return visible.map((article, index) => {
    const divider = index === newCount ? newSinceDivider(newCount) : "";
    return `${divider}${storyCard(article)}`;
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

function archiveControlsMarkup() {
  if (!usesArchiveView()) return "";
  if (state.archiveLoading && !state.archiveArticles.length) {
    return `<div class="archive-loading" role="status"><span></span> Searching your local history…</div>`;
  }
  if (!state.archiveHasMore && !state.archiveLoading) return "";
  return `
    <div class="archive-load-more">
      <button type="button" data-load-more-archive ${state.archiveLoading ? "disabled" : ""}>
        ${state.archiveLoading ? "Loading…" : `Load more · ${state.archiveArticles.length} of ${state.archiveTotal}`}
      </button>
    </div>`;
}

function softwareGroup(article) {
  const explicitGroup = normalizeSoftwareCategory(article.software_group);
  if (explicitGroup) return explicitGroup;
  const matchedReason = SOFTWARE_GROUP_ORDER.find((group) => (article.priority_reasons || []).includes(group));
  if (matchedReason) return matchedReason;
  return article.lane === "Industry & Business" ? "Industry context" : "Production techniques";
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
  const isLatestView = state.view === "all";
  const showTopics = isLatestView && productionTopicsActive();
  elements.softwareFilterGroup.hidden = !isLatestView;
  elements.topicFilterGroup.hidden = !showTopics;
  if (!isLatestView) return;

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
  const visible = state.view === "all"
    ? applyFacetFilters(pool)
    : filteredArticles();
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
  elements.grid.innerHTML = `${storyMarkup}${archiveControlsMarkup()}`;
  const initialArchiveLoad = usesArchiveView() && state.archiveLoading && !visible.length;
  elements.empty.hidden = visible.length > 0 || initialArchiveLoad;
  elements.grid.hidden = visible.length === 0 && !initialArchiveLoad;
  const emptyCopy = {
    saved: ["Your learning library is empty", "Save a story, then add a note so useful techniques remain easy to find."],
    unread: ["You’re all caught up", "New unread stories will appear after the next feed refresh."],
    archived: ["The archive is empty", "Archived stories stay out of your active feed and can be restored here."],
    history: ["No articles match", "Try a broader search or restore your source filters."],
  }[state.view] || ["No signal here yet", "Try another category, clear your source filters, or refresh the feeds."];
  elements.empty.querySelector("h2").textContent = emptyCopy[0];
  elements.empty.querySelector("p").textContent = emptyCopy[1];
  const resultCount = usesArchiveView() ? state.archiveTotal : visible.length;
  elements.visibleCount.textContent = `${resultCount} ${resultCount === 1 ? "story" : "stories"}`;
  const newCount = usesArchiveView() ? 0 : visible.filter(articleIsNew).length;
  elements.newSince.textContent = state.sessionCutoff && newCount ? `${newCount} new` : "";
  elements.newSince.hidden = !(state.sessionCutoff && newCount);
  elements.allCount.textContent = state.articles.filter((article) => !state.archived.has(article.id)).length;
  elements.savedCount.textContent = state.saved.size;
  elements.unreadCount.textContent = unreadArticles().length;
  elements.archivedCount.textContent = state.archived.size;
  elements.historyCount.textContent = state.payload.archive_count ?? state.archiveTotal ?? "—";
  elements.briefCount.textContent = dailyBriefArticles().length;
  elements.sortLabel.textContent = {
    saved: "Learning library",
    unread: "Newest unread",
    archived: "Recently archived",
    history: "Full history",
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
    .map((source) => {
      const muted = state.mutedSources.has(source.id);
      const reduced = state.reducedSources.has(source.id);
      const active = state.activeSources.has(source.id) && !muted;
      const status = muted ? "Muted — click to restore" : reduced ? "Reduced in Daily brief" : active ? "Included" : "Filtered out";
      return `
        <button class="source-button${active ? "" : " is-muted"}${muted ? " is-source-muted" : ""}${reduced ? " is-reduced" : ""}" type="button" data-source-id="${escapeHtml(source.id)}" style="--source-accent:${escapeHtml(source.accent)}" aria-pressed="${active}" title="${escapeHtml(status)}">
          <span class="source-dot"></span>
          <span>${escapeHtml(source.name)}</span>
          ${muted ? '<em aria-hidden="true">muted</em>' : reduced ? '<em aria-hidden="true">less</em>' : ""}
          <strong>${sourceCount(source.id)}</strong>
        </button>`;
    })
    .join("");
  elements.sourceOrbit.innerHTML = sources
    .map(
      (source) =>
        `<i class="${source.ok ? "" : "is-offline"}" title="${escapeHtml(`${source.name}: ${source.ok ? "connected" : "unavailable"}`)}" style="--source-accent:${escapeHtml(source.accent)}"></i>`,
    )
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
  elements.heroUnique.textContent = payload.unique_count ?? state.articles.length;
  elements.heroCollapsed.textContent = payload.duplicates_collapsed ?? 0;
  elements.lastUpdated.textContent = payload.generated_at
    ? `Updated ${relativeTime(payload.generated_at)}${payload.cached ? " · local cache" : ""}`
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

async function loadFeed(force = false, { background = false } = {}) {
  if (!background) {
    elements.refresh.classList.add("is-loading");
    elements.refresh.disabled = true;
    elements.stories.setAttribute("aria-busy", "true");
  }
  try {
    const response = await fetch(`/api/feed${force ? "?refresh=1" : ""}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Feed request failed (${response.status})`);
    const payload = await response.json();
    if (payload.error) throw new Error(payload.detail || payload.error);
    updateDashboard(payload, { background });
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
  const response = await fetch(url, {
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

function setFeedback(articleId, requestedValue) {
  const article = [...state.articles, ...state.archiveArticles].find((item) => item.id === articleId);
  if (!article) return;
  const current = state.feedback.get(articleId)?.value || 0;
  if (current === requestedValue) {
    state.feedback.delete(articleId);
  } else {
    state.feedback.set(articleId, {
      id: articleId,
      value: requestedValue,
      source_id: article.source_id || "",
      software_tags: articleSoftwareCategories(article),
      topic_tags: articleTopics(article),
    });
  }
  queueUserStateSave();
  render();
}

function setSourcePreference(sourceId, action) {
  if (action === "mute") {
    state.mutedSources.add(sourceId);
    state.reducedSources.delete(sourceId);
  } else if (action === "reduce") {
    state.reducedSources.add(sourceId);
    state.mutedSources.delete(sourceId);
  } else {
    state.reducedSources.delete(sourceId);
    state.mutedSources.delete(sourceId);
    state.activeSources.add(sourceId);
  }
  queueUserStateSave();
  renderSources(state.payload.sources || []);
  if (usesArchiveView()) loadArchive();
  else render();
}

function toggleRead(articleId) {
  state.read.has(articleId) ? state.read.delete(articleId) : state.read.add(articleId);
  queueUserStateSave();
  render();
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
    if (document.visibilityState === "visible") loadFeed(false, { background: true });
  }, 15 * 60 * 1000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.firstFeedLoaded) {
    loadFeed(false, { background: true });
  }
});

document.addEventListener("click", (event) => {
  const resetFeedback = event.target.closest("[data-reset-feedback]");
  if (resetFeedback) {
    state.feedback.clear();
    queueUserStateSave();
    render();
    return;
  }

  const searchToken = event.target.closest("[data-search-token]");
  if (searchToken) {
    const token = searchToken.dataset.searchToken;
    const existing = elements.search.value.trim();
    elements.search.value = existing ? `${existing} ${token}` : token;
    state.search = elements.search.value;
    elements.search.focus();
    if (usesArchiveView()) loadArchive();
    else render();
    return;
  }

  const loadMoreArchive = event.target.closest("[data-load-more-archive]");
  if (loadMoreArchive) {
    loadArchive({ append: true });
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

  const feedbackButton = event.target.closest("[data-feedback-id]");
  if (feedbackButton) {
    setFeedback(feedbackButton.dataset.feedbackId, Number(feedbackButton.dataset.feedbackValue));
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
    if (state.view === "saved" && wasSaved) state.archiveTotal = Math.max(0, state.archiveTotal - 1);
    queueUserStateSave();
    render();
    return;
  }

  const archiveButton = event.target.closest("[data-archive-id]");
  if (archiveButton) {
    const id = archiveButton.dataset.archiveId;
    const wasArchived = state.archived.has(id);
    wasArchived ? state.archived.delete(id) : state.archived.add(id);
    if (state.view === "archived" && wasArchived) state.archiveTotal = Math.max(0, state.archiveTotal - 1);
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
    if (state.mutedSources.has(id)) {
      state.mutedSources.delete(id);
      state.reducedSources.delete(id);
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
    if (usesArchiveView()) loadArchive();
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
    if (usesArchiveView()) loadArchive();
    else render();
    return;
  }

  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    state.view = viewButton.dataset.view;
    if (usesArchiveView()) loadArchive();
    else render();
  }
});

elements.search.addEventListener("input", () => {
  state.search = elements.search.value;
  window.clearTimeout(archiveSearchTimer);
  if (usesArchiveView()) {
    archiveSearchTimer = window.setTimeout(() => loadArchive(), 250);
  } else {
    render();
  }
});

document.addEventListener("input", (event) => {
  const note = event.target.closest?.("[data-note-id]");
  if (!note) return;
  const value = note.value.trim();
  if (value) state.notes[note.dataset.noteId] = value;
  else delete state.notes[note.dataset.noteId];
  localStorage.setItem(storageKeys.notes, JSON.stringify(state.notes));
  localStorage.setItem(storageKeys.stateDirty, "1");
  window.clearTimeout(noteSaveTimer);
  noteSaveTimer = window.setTimeout(queueUserStateSave, 500);
});

elements.refresh.addEventListener("click", () => loadFeed(true));

elements.briefButton.addEventListener("click", openBriefing);

elements.manageSources.addEventListener("click", openSourceManager);

document.querySelectorAll("[data-close-briefing]").forEach((button) => {
  button.addEventListener("click", closeBriefing);
});

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

elements.scrollTop.addEventListener("click", () => {
  const firstArticle = elements.grid.querySelector(".story-card:not(.skeleton-card)");
  if (!firstArticle) {
    window.scrollTo({ top: 0 });
    return;
  }
  firstArticle.scrollIntoView({ behavior: "auto", block: "center" });
});

document.querySelector("#reset-sources").addEventListener("click", () => {
  state.activeSources = new Set((state.payload.sources || []).map((source) => source.id));
  state.mutedSources.clear();
  state.reducedSources.clear();
  queueUserStateSave();
  renderSources(state.payload.sources || []);
  if (usesArchiveView()) loadArchive();
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
  } else if (event.key === "Escape" && !elements.sourceManagerPanel.hidden) {
    closeSourceManager();
  }

  if (event.ctrlKey || event.metaKey || event.altKey || state.briefOpen || !elements.sourceManagerPanel.hidden || keyboardTargetIsEditable(event.target)) return;
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
    if (state.view === "saved" && wasSaved) state.archiveTotal = Math.max(0, state.archiveTotal - 1);
    queueUserStateSave();
    render();
  } else if (key === "a") {
    event.preventDefault();
    const wasArchived = state.archived.has(state.keyboardArticleId);
    wasArchived ? state.archived.delete(state.keyboardArticleId) : state.archived.add(state.keyboardArticleId);
    if (state.view === "archived" && wasArchived) state.archiveTotal = Math.max(0, state.archiveTotal - 1);
    queueUserStateSave();
    render();
  } else if (key === "m") {
    event.preventDefault();
    toggleRead(state.keyboardArticleId);
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
  localStorage.setItem(storageKeys.lastVisit, state.sessionStartedAt);
  scheduleBackgroundChecks();
}

initialize();
