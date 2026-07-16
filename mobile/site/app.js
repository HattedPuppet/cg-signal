const storageKeys = {
  read: "cg-signal-mobile:read",
  feed: "cg-signal-mobile:last-feed",
};

const CATEGORY_ORDER = [
  "Unreal Engine",
  "Unity",
  "Blender",
  "Substance 3D",
  "Houdini",
  "AI",
  "Production techniques",
  "Industry context",
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
]);

const state = {
  payload: null,
  articles: [],
  read: readSet(),
  lane: "All",
  category: "All",
  source: "All",
  search: "",
  view: "latest",
  installPrompt: null,
  lastFetchAt: 0,
};

const elements = {
  storyList: document.querySelector("#story-list"),
  empty: document.querySelector("#empty-state"),
  storyTotal: document.querySelector("#story-total"),
  repeatTotal: document.querySelector("#repeat-total"),
  briefTotal: document.querySelector("#brief-total"),
  unreadTotal: document.querySelector("#unread-total"),
  resultCount: document.querySelector("#result-count"),
  updateStatus: document.querySelector("#update-status"),
  connectionDot: document.querySelector("#connection-dot"),
  categoryList: document.querySelector("#category-list"),
  sourceSelect: document.querySelector("#source-select"),
  search: document.querySelector("#search-input"),
  clearSearch: document.querySelector("#clear-search"),
  notice: document.querySelector("#notice"),
  feedKicker: document.querySelector("#feed-kicker"),
  feedTitle: document.querySelector("#feed-title"),
  install: document.querySelector("#install-button"),
  briefPanel: document.querySelector("#brief-panel"),
  briefList: document.querySelector("#brief-list"),
  briefIntro: document.querySelector("#brief-intro"),
  briefMarkRead: document.querySelector("#brief-mark-read"),
};

function readSet() {
  try {
    const value = JSON.parse(localStorage.getItem(storageKeys.read) || "[]");
    return new Set(Array.isArray(value) ? value : []);
  } catch {
    return new Set();
  }
}

function persistRead() {
  const currentIds = new Set(state.articles.map((article) => article.id));
  const bounded = [...state.read].filter((id) => currentIds.has(id)).slice(-1500);
  state.read = new Set(bounded);
  localStorage.setItem(storageKeys.read, JSON.stringify(bounded));
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
    : [article.lane === "Industry & Business" ? "Industry context" : "Production techniques"];
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
    .replace(/#substance\s+(?:painter|designer|3d)\b/gi, '#software:"Substance 3D"');
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
    if (token.value === "read") return state.read.has(article.id);
    if (token.value === "unread") return !state.read.has(article.id);
    if (token.value === "new") return Date.now() - new Date(article.published_at).getTime() < 24 * 60 * 60 * 1000;
    return false;
  }
  return searchableText(article).includes(token.value);
}

function matchesSearch(article) {
  return parseSearch(state.search).every((token) => tokenMatches(article, token) !== token.negative);
}

function matchesBaseFilters(article) {
  if (state.lane !== "All" && article.lane !== state.lane) return false;
  if (state.source !== "All" && !(article.sources || []).some((source) => source.id === state.source)) return false;
  if (state.view === "unread" && state.read.has(article.id)) return false;
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

function renderCategories() {
  const counts = categoryCounts();
  const categories = CATEGORY_ORDER.filter((category) => counts.has(category) || category === state.category);
  const allCount = state.articles.filter(matchesBaseFilters).length;
  elements.categoryList.innerHTML = ["All", ...categories].map((category) => {
    const active = category === state.category;
    const label = category === "All" ? "All categories" : category;
    const count = category === "All" ? allCount : (counts.get(category) || 0);
    const color = category === "All" ? "#d7ff57" : (CATEGORY_COLORS[category] || "#7fa9ff");
    return `<button class="category-button${active ? " is-active" : ""}" type="button" data-category="${escapeHtml(category)}" aria-pressed="${active}" style="--category-accent:${escapeHtml(color)}"><span>${escapeHtml(label)}</span><strong>${count}</strong></button>`;
  }).join("");
}

function trimSummary(value) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  return clean.length > 245 ? `${clean.slice(0, 242).trimEnd()}…` : clean;
}

function storyMarkup(article) {
  const read = state.read.has(article.id);
  const category = primaryCategory(article);
  const imageUrl = safeUrl(article.image);
  const image = imageUrl === "#" ? "" : `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`;
  const reasons = [...new Set([...(article.software_tags || []), ...(article.topic_tags || [])])]
    .filter((reason) => reason !== category)
    .slice(0, 2);
  const coverage = article.source_count > 1 ? `${article.source_count} sources` : "Single source";
  return `
    <article class="story-card${read ? " is-read" : ""}" style="--story-accent:${escapeHtml(article.accent || CATEGORY_COLORS[category] || "#7fa9ff")}">
      <div class="story-image${image ? "" : " no-image"}">
        ${image}
        <span>${escapeHtml(category)}</span>
      </div>
      <div class="story-body">
        <div class="story-meta">
          <span class="source-name">${escapeHtml(article.source)}</span>
          <span class="lane-label${article.lane === "Industry & Business" ? " is-industry" : ""}">${article.lane === "Industry & Business" ? "Industry" : "Tech"}</span>
          <time datetime="${escapeHtml(article.published_at)}">${escapeHtml(relativeTime(article.published_at))}</time>
        </div>
        <h3><a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}">${escapeHtml(article.title)}</a></h3>
        <p>${escapeHtml(trimSummary(article.summary))}</p>
        ${reasons.length ? `<div class="reason-list">${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>` : ""}
        <footer><span>${escapeHtml(coverage)}</span><a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}">Read original <i aria-hidden="true">↗</i></a></footer>
      </div>
    </article>`;
}

function briefArticles() {
  const ranked = state.articles
    .filter((article) => !state.read.has(article.id))
    .sort((left, right) => (right.priority_score || 0) - (left.priority_score || 0));
  const technical = ranked.filter((article) => article.lane !== "Industry & Business").slice(0, 6);
  const industry = ranked.filter((article) => article.lane === "Industry & Business").slice(0, 3);
  const chosen = [...technical, ...industry];
  for (const article of ranked) {
    if (chosen.length >= 9) break;
    if (!chosen.some((item) => item.id === article.id)) chosen.push(article);
  }
  return chosen.sort((left, right) => (right.priority_score || 0) - (left.priority_score || 0));
}

function renderBrief() {
  const articles = briefArticles();
  const technicalCount = articles.filter((article) => article.lane !== "Industry & Business").length;
  const industryCount = articles.length - technicalCount;
  elements.briefIntro.textContent = articles.length
    ? `${articles.length} unread stories: ${technicalCount} technical ${technicalCount === 1 ? "signal" : "signals"}${industryCount ? ` and ${industryCount} industry ${industryCount === 1 ? "update" : "updates"}` : ""}.`
    : "You have read every story in the current mobile feed.";
  elements.briefList.innerHTML = articles.length
    ? articles.map((article, index) => `
      <a class="brief-item${article.lane === "Industry & Business" ? " is-industry" : ""}" href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}">
        <span>${String(index + 1).padStart(2, "0")}</span>
        <div><small>${escapeHtml(article.source)} · ${escapeHtml(primaryCategory(article))}</small><strong>${escapeHtml(article.title)}</strong><p>${escapeHtml(trimSummary(article.summary))}</p></div>
        <i aria-hidden="true">↗</i>
      </a>`).join("")
    : `<div class="brief-empty"><span>✓</span><strong>Briefing complete</strong><p>Fresh stories will appear after the next scheduled update.</p></div>`;
  elements.briefMarkRead.disabled = !articles.length;
}

function updateLaneCounts() {
  document.querySelectorAll("[data-lane]").forEach((button) => {
    const lane = button.dataset.lane;
    const count = lane === "All" ? state.articles.length : state.articles.filter((article) => article.lane === lane).length;
    const active = lane === state.lane;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
    button.querySelector("strong").textContent = count;
  });
}

function render() {
  if (!state.payload) return;
  const articles = visibleArticles();
  const unread = state.articles.filter((article) => !state.read.has(article.id)).length;
  const brief = briefArticles();
  renderCategories();
  updateLaneCounts();
  elements.storyTotal.textContent = state.articles.length;
  elements.repeatTotal.textContent = state.payload.duplicates_collapsed || 0;
  elements.briefTotal.textContent = brief.length;
  elements.unreadTotal.textContent = unread;
  elements.resultCount.textContent = `${articles.length} ${articles.length === 1 ? "result" : "results"}`;
  elements.feedKicker.textContent = state.view === "unread" ? "Unread signal" : "Latest signal";
  elements.feedTitle.textContent = state.view === "unread" ? "Still waiting for you" : "What’s worth a look";
  elements.storyList.innerHTML = articles.map(storyMarkup).join("");
  elements.storyList.hidden = articles.length === 0;
  elements.empty.hidden = articles.length > 0;
  elements.storyList.setAttribute("aria-busy", "false");
  elements.clearSearch.hidden = !state.search;
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view || (button.dataset.view === "latest" && state.view === "latest");
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  renderBrief();
}

function renderSources() {
  elements.sourceSelect.innerHTML = `<option value="All">All sources</option>${(state.payload.sources || []).map((source) => `<option value="${escapeHtml(source.id)}">${escapeHtml(source.name)} · ${source.count || 0}</option>`).join("")}`;
  elements.sourceSelect.value = state.source;
}

function updateConnection(online, cached = false) {
  elements.connectionDot.classList.toggle("is-offline", !online);
  if (!state.payload) return;
  const generated = state.payload.generated_at ? relativeTime(state.payload.generated_at) : "recently";
  elements.updateStatus.textContent = cached ? `Offline copy · updated ${generated}` : `Updated ${generated} · refreshes every 30 minutes`;
}

async function loadFeed() {
  state.lastFetchAt = Date.now();
  try {
    const response = await fetch("./feed.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Feed request failed (${response.status})`);
    const payload = await response.json();
    if (!Array.isArray(payload.articles) || !payload.articles.length) throw new Error("The hosted feed is empty.");
    state.payload = payload;
    state.articles = payload.articles;
    localStorage.setItem(storageKeys.feed, JSON.stringify(payload));
    persistRead();
    renderSources();
    render();
    updateConnection(true);
    if (payload.unavailable_sources?.length) {
      elements.notice.textContent = `Some sources missed the latest update: ${payload.unavailable_sources.join(", ")}. The remaining feed is current.`;
      elements.notice.hidden = false;
    } else {
      elements.notice.hidden = true;
    }
  } catch (error) {
    try {
      const cached = JSON.parse(localStorage.getItem(storageKeys.feed) || "null");
      if (!cached?.articles?.length) throw error;
      state.payload = cached;
      state.articles = cached.articles;
      renderSources();
      render();
      updateConnection(false, true);
      elements.notice.textContent = "The network is unavailable, so the most recent copy stored on this phone is shown.";
      elements.notice.hidden = false;
    } catch {
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
  elements.search.value = "";
  elements.sourceSelect.value = "All";
  render();
}

function openBrief() {
  renderBrief();
  elements.briefPanel.hidden = false;
  document.body.classList.add("brief-open");
  elements.briefPanel.querySelector("[data-close-brief]").focus();
}

function closeBrief() {
  elements.briefPanel.hidden = true;
  document.body.classList.remove("brief-open");
}

document.addEventListener("click", (event) => {
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
    if (viewButton.dataset.view === "brief") openBrief();
    else {
      state.view = viewButton.dataset.view;
      render();
      window.scrollTo({ top: document.querySelector(".controls").offsetTop - 8, behavior: "smooth" });
    }
    return;
  }

  const readLink = event.target.closest("[data-read-id]");
  if (readLink) {
    state.read.add(readLink.dataset.readId);
    persistRead();
    window.setTimeout(render, 100);
  }
});

elements.search.addEventListener("input", () => {
  state.search = elements.search.value.trim();
  render();
});

elements.clearSearch.addEventListener("click", () => {
  elements.search.value = "";
  state.search = "";
  elements.search.focus();
  render();
});

elements.sourceSelect.addEventListener("change", () => {
  state.source = elements.sourceSelect.value;
  render();
});

document.querySelector("#clear-filters").addEventListener("click", resetFilters);
document.querySelector("#brief-hero-button").addEventListener("click", openBrief);
document.querySelectorAll("[data-close-brief]").forEach((button) => button.addEventListener("click", closeBrief));

document.querySelector("#mark-visible-read").addEventListener("click", () => {
  visibleArticles().forEach((article) => state.read.add(article.id));
  persistRead();
  render();
});

elements.briefMarkRead.addEventListener("click", () => {
  briefArticles().forEach((article) => state.read.add(article.id));
  persistRead();
  render();
  closeBrief();
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
  if (event.key === "Escape" && !elements.briefPanel.hidden) closeBrief();
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js").catch(() => {}));
}

loadFeed();
