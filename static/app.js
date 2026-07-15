const storageKeys = {
  read: "cg-signal:read",
  saved: "cg-signal:saved",
  theme: "cg-signal:theme",
  layout: "cg-signal:layout",
  lane: "cg-signal:lane",
};

const state = {
  payload: null,
  articles: [],
  activeSources: new Set(),
  topic: "All",
  lane: localStorage.getItem(storageKeys.lane) || "All",
  view: "all",
  search: "",
  read: readSet(storageKeys.read),
  saved: readSet(storageKeys.saved),
  layout: localStorage.getItem(storageKeys.layout) || "grid",
  briefOpen: false,
};

const elements = {
  grid: document.querySelector("#story-grid"),
  stories: document.querySelector("#stories"),
  empty: document.querySelector("#empty-state"),
  sourceFilters: document.querySelector("#source-filters"),
  sourceOrbit: document.querySelector("#source-orbit"),
  visibleCount: document.querySelector("#visible-count"),
  allCount: document.querySelector("#all-count"),
  savedCount: document.querySelector("#saved-count"),
  unreadCount: document.querySelector("#unread-count"),
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

function unreadArticles() {
  return state.articles.filter((article) => !state.read.has(article.id));
}

function buildBriefing() {
  const unread = unreadArticles();
  elements.briefCount.textContent = unread.length;
  elements.briefingMarkRead.disabled = unread.length === 0;

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

  const topics = new Map();
  const sources = new Map();
  unread.forEach((article) => {
    topics.set(article.topic, (topics.get(article.topic) || 0) + 1);
    article.sources.forEach((source) => sources.set(source.name, (sources.get(source.name) || 0) + 1));
  });
  const rankedTopics = [...topics.entries()].sort((left, right) => right[1] - left[1]);
  const rankedSources = [...sources.entries()].sort((left, right) => right[1] - left[1]);
  const topicPhrase = rankedTopics
    .slice(0, 2)
    .map(([topic, count]) => `${escapeHtml(topic)} (${count})`)
    .join(" and ");
  const sourcePhrase = rankedSources[0] ? `${escapeHtml(rankedSources[0][0])} has the most updates.` : "";
  const topicPills = rankedTopics
    .slice(0, 5)
    .map(([topic, count]) => `<span class="briefing-topic">${escapeHtml(topic)} <strong>${count}</strong></span>`)
    .join("");
  const highlights = unread
    .slice(0, 8)
    .map(
      (article) => `
        <a class="briefing-item" href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" data-read-id="${escapeHtml(article.id)}" style="--story-accent:${escapeHtml(article.accent)}">
          <span class="briefing-item-dot"></span>
          <span class="briefing-item-copy">
            <span class="briefing-item-meta">${escapeHtml(article.source)} · ${escapeHtml(relativeTime(article.published_at))}</span>
            <h3>${escapeHtml(article.title)}</h3>
            <p>${escapeHtml(briefExcerpt(article.summary))}</p>
          </span>
          <span class="briefing-item-arrow" aria-hidden="true">↗</span>
        </a>`,
    )
    .join("");

  elements.briefingContent.innerHTML = `
    <p class="briefing-lead"><strong>${unread.length} unread ${unread.length === 1 ? "story is" : "stories are"} waiting.</strong> The strongest activity is in ${topicPhrase}. ${sourcePhrase}</p>
    <div class="briefing-topics">${topicPills}</div>
    <p class="briefing-section-label">Newest highlights</p>
    <div class="briefing-highlights">${highlights}</div>`;
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

function filteredArticles() {
  const query = state.search.trim().toLocaleLowerCase();
  return state.articles.filter((article) => {
    if (!matchesSource(article)) return false;
    if (state.lane !== "All" && (article.lane || "Tech & Development") !== state.lane) return false;
    if (state.topic !== "All" && article.topic !== state.topic) return false;
    if (state.view === "saved" && !state.saved.has(article.id)) return false;
    if (state.view === "unread" && state.read.has(article.id)) return false;
    if (!query) return true;
    const haystack = [
      article.title,
      article.summary,
      article.source,
      article.topic,
      article.lane,
      ...article.related.map((item) => `${item.source} ${item.title}`),
    ]
      .join(" ")
      .toLocaleLowerCase();
    return haystack.includes(query);
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
  const imageUrl = safeUrl(article.image);
  const image = imageUrl === "#" ? "" : `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`;
  const coverage = article.source_count > 1 ? `${article.source_count} sources` : "Single source";
  const lane = article.lane || "Tech & Development";
  const laneLabel = lane === "Industry & Business" ? "Business" : "Tech";
  return `
    <article class="story-card${read ? " is-read" : ""}" data-id="${escapeHtml(article.id)}" style="--story-accent:${escapeHtml(article.accent)}">
      <div class="story-visual${image ? "" : " image-failed"}" data-topic="${escapeHtml(article.topic)}">
        ${image}
        <div class="visual-overlay"></div>
        <span class="visual-topic">${escapeHtml(article.topic)}</span>
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
        <div class="story-footer">
          <div class="source-stack">${sourceStack(article)}<span class="coverage-label">${coverage}</span></div>
          <button class="save-button${saved ? " is-saved" : ""}" type="button" data-save-id="${escapeHtml(article.id)}" aria-label="${saved ? "Remove from saved" : "Save story"}" aria-pressed="${saved}">${saved ? "★" : "☆"}</button>
        </div>
        ${relatedCoverage(article)}
      </div>
    </article>`;
}

function render() {
  if (!state.payload) return;
  const visible = filteredArticles();
  elements.grid.classList.toggle("is-list", state.layout === "list");
  elements.grid.classList.remove("loading-grid");
  elements.grid.innerHTML = visible.map(storyCard).join("");
  elements.empty.hidden = visible.length > 0;
  elements.grid.hidden = visible.length === 0;
  elements.visibleCount.textContent = `${visible.length} ${visible.length === 1 ? "story" : "stories"}`;
  elements.allCount.textContent = state.articles.length;
  elements.savedCount.textContent = state.articles.filter((article) => state.saved.has(article.id)).length;
  elements.unreadCount.textContent = state.articles.filter((article) => !state.read.has(article.id)).length;
  elements.briefCount.textContent = unreadArticles().length;
  if (state.briefOpen) buildBriefing();

  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll("[data-topic]").forEach((button) => {
    if (!button.classList.contains("topic-button")) return;
    const active = button.dataset.topic === state.topic;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
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
    saveSet(storageKeys.saved, state.saved);
    render();
    return;
  }

  const storyLink = event.target.closest("[data-read-id]");
  if (storyLink) {
    state.read.add(storyLink.dataset.readId);
    saveSet(storageKeys.read, state.read);
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

  const topicButton = event.target.closest(".topic-button");
  if (topicButton) {
    state.topic = topicButton.dataset.topic;
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
  unreadArticles().forEach((article) => state.read.add(article.id));
  saveSet(storageKeys.read, state.read);
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
  state.topic = "All";
  state.lane = "All";
  localStorage.setItem(storageKeys.lane, state.lane);
  state.view = "all";
  state.search = "";
  elements.search.value = "";
  renderSources(state.payload.sources || []);
  render();
});

document.querySelector("#mark-all-read").addEventListener("click", () => {
  filteredArticles().forEach((article) => state.read.add(article.id));
  saveSet(storageKeys.read, state.read);
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

loadFeed();
