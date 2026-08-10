/**
 * Pure client-domain helpers shared by the desktop dashboard and the mobile
 * public feed.  Keep browser state and rendering concerns out of this module
 * so it can also be exercised directly by Node's built-in test runner.
 */

export const FEED_SCHEMA_VERSION = 1;

export const ARTICLE_LANE_VALUES = new Set([
  "Tech & Development",
  "Industry",
  "Business",
]);
export const LANE_VALUES = new Set(["All", ...ARTICLE_LANE_VALUES]);

export const SOFTWARE_GROUP_ORDER = Object.freeze([
  "Unreal Engine",
  "Unity",
  "Blender",
  "Substance 3D",
  "Houdini",
  "AI",
  "Production techniques",
  "Industry context",
  "Business context",
]);

export const SOFTWARE_GROUP_COLORS = Object.freeze({
  "Unreal Engine": "#4b75ff",
  Unity: "#222c37",
  Blender: "#f18a21",
  "Substance 3D": "#9fa9ff",
  Houdini: "#ff7b38",
  AI: "#a77bff",
  "Production techniques": "#d7ff57",
  "Industry context": "#f4a261",
  "Business context": "#c78cff",
});

export const TOPIC_ORDER = Object.freeze([
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
]);

export const TOPIC_COLORS = Object.freeze({
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
});

export const SEARCH_ALIASES = new Map([
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
  ["ai", { field: "software", value: "AI" }],
  ["genai", { field: "software", value: "AI" }],
  ["production", { field: "software", value: "Production techniques" }],
  ["production-techniques", { field: "software", value: "Production techniques" }],
  ["industry", { field: "software", value: "Industry context" }],
  ["industry-context", { field: "software", value: "Industry context" }],
  ["business", { field: "software", value: "Business context" }],
  ["business-context", { field: "software", value: "Business context" }],
]);

const SEARCH_FIELDS = new Set(["software", "topic", "source", "is"]);

export function normalizeSoftwareCategory(value) {
  if (["Substance Painter", "Substance Designer", "Substance 3D"].includes(value)) {
    return "Substance 3D";
  }
  if (value === "Spine") return "";
  return value || "";
}

export function softwareGroup(article = {}) {
  const explicitGroup = normalizeSoftwareCategory(article.software_group);
  if (explicitGroup) return explicitGroup;
  const matchedReason = SOFTWARE_GROUP_ORDER.find((group) => (article.priority_reasons || []).includes(group));
  if (matchedReason) return matchedReason;
  if (article.lane === "Business") return "Business context";
  if (article.lane === "Industry") return "Industry context";
  return "Production techniques";
}

export function articleCategories(article = {}) {
  const tags = [...new Set((article.software_tags || []).map(normalizeSoftwareCategory))].filter(Boolean);
  return tags.length ? tags : [softwareGroup(article)];
}

export function articleTopics(article = {}) {
  if (softwareGroup(article) !== "Production techniques") return [];
  const tags = [...new Set(article.topic_tags || [])].filter(Boolean);
  return tags.length ? tags : ["Other production"];
}

export function articleSourceIds(article = {}) {
  const ids = new Set((article.sources || []).map((source) => source?.id).filter(Boolean));
  if (article.source_id) ids.add(article.source_id);
  return ids;
}

export function normalizeSearchQuery(query = "") {
  return String(query ?? "")
    .replace(/#unreal\s+engine\b/giu, '#software:"Unreal Engine"')
    .replace(/#substance\s+(?:painter|designer|3d)\b/giu, '#software:"Substance 3D"')
    .replace(/#production\s+techniques\b/giu, '#software:"Production techniques"')
    .replace(/#industry\s+context\b/giu, '#software:"Industry context"')
    .replace(/#business\s+context\b/giu, '#software:"Business context"');
}

export function searchTokens(query = "") {
  const normalized = normalizeSearchQuery(query).trim();
  if (!normalized) return [];
  const rawTokens = normalized.match(/-?#(?:software|topic|source|is):(?:"[^"]+"|'[^']+'|\S+)|-?#[\p{L}\p{N}_-]+|-?"[^"]+"|-?\S+/giu) || [];
  return rawTokens.map((raw) => {
    const negative = raw.startsWith("-");
    let token = negative ? raw.slice(1) : raw;
    let field = "text";
    let value = token;
    if (token.startsWith("#")) {
      token = token.slice(1);
      const separator = token.indexOf(":");
      if (separator > 0) {
        const possibleField = token.slice(0, separator).toLocaleLowerCase();
        if (SEARCH_FIELDS.has(possibleField)) {
          field = possibleField;
          value = token.slice(separator + 1);
        } else {
          value = token;
        }
      } else {
        const alias = SEARCH_ALIASES.get(token.toLocaleLowerCase().replaceAll("_", "-"));
        if (alias) {
          field = alias.field;
          value = alias.value;
        } else {
          value = token;
        }
      }
    }
    value = value.replace(/^['"]|['"]$/g, "").toLocaleLowerCase();
    if (field === "software" && ["substance painter", "substance designer"].includes(value)) {
      value = "substance 3d";
    }
    return { negative, field, value };
  }).filter((token) => token.value);
}

export function searchableArticleText(article = {}, extraText = "") {
  return [
    article.title,
    article.summary,
    article.source,
    article.lane,
    article.software_group,
    ...(article.software_tags || []),
    ...(article.topic_tags || []),
    ...(article.priority_reasons || []),
    ...(article.related || []).map((item) => `${item?.source || ""} ${item?.title || ""}`),
    extraText,
  ].filter(Boolean).join(" ").toLocaleLowerCase();
}

export function matchesSearchToken(article, token, { isStatus, extraText = "" } = {}) {
  const value = token.value;
  if (token.field === "software") {
    return articleCategories(article).some((item) => item.toLocaleLowerCase().includes(value));
  }
  if (token.field === "topic") {
    return articleTopics(article).some((item) => item.toLocaleLowerCase().includes(value));
  }
  if (token.field === "source") {
    const sourceText = [
      ...(article.sources || []).map((source) => `${source?.id || ""} ${source?.name || ""}`),
      article.source_id,
      article.source,
    ].filter(Boolean).join(" ").toLocaleLowerCase();
    return sourceText.includes(value);
  }
  if (token.field === "is") {
    return typeof isStatus === "function" ? Boolean(isStatus(article, value, token)) : false;
  }
  return searchableArticleText(article, extraText).includes(value);
}

/**
 * Match every search token.  The callback is intentionally supplied by the
 * caller because desktop and mobile have different local #is state.
 */
export function matchesSearch(article, query, options = {}) {
  const normalizedOptions = options || {};
  return searchTokens(query).every((token) => {
    const matched = matchesSearchToken(article, token, normalizedOptions);
    return token.negative ? !matched : matched;
  });
}

export function publicationWindowStart(timeWindow = "month", now = new Date()) {
  if (timeWindow === "all") return null;
  const monthOffset = timeWindow === "quarter" ? 2 : 0;
  const current = now instanceof Date ? now : new Date(now);
  return new Date(current.getFullYear(), current.getMonth() - monthOffset, 1);
}

export function articleWithinPublicationWindow(article, timeWindow = "month", now = new Date()) {
  const start = publicationWindowStart(timeWindow, now);
  if (!start) return true;
  const published = new Date(article?.published_at);
  return Number.isNaN(published.getTime()) || published >= start;
}

export function feedPayloadHasSchema(payload) {
  return payload?.feed_schema_version === FEED_SCHEMA_VERSION;
}

const MAX_FEED_ARTICLES = 1500;
const MAX_FEED_SOURCES = 300;
const MAX_ARTICLE_STRING = 5000;
const MAX_TAGS = 32;
const MAX_TAG_LENGTH = 120;
const MAX_RELATED = 8;

function nonEmptyString(value, maximum = MAX_ARTICLE_STRING) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maximum;
}

function optionalString(value, maximum = MAX_ARTICLE_STRING) {
  return value === undefined || (typeof value === "string" && value.length <= maximum);
}

function boundedString(value, maximum = MAX_ARTICLE_STRING) {
  return typeof value === "string" && value.length <= maximum;
}

function publicHttpUrl(value, { required = false } = {}) {
  if (value === undefined || value === "") return !required;
  if (!nonEmptyString(value, 4096)) return false;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol)
      && !parsed.username
      && !parsed.password
      && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

const THUMBNAIL_REFERENCE_RE = /^thumbnails\/[0-9a-f]{64}\.(?:jpg|png|webp)$/;

function thumbnailReferenceIsValid(value) {
  return value === undefined || value === "" || (typeof value === "string" && THUMBNAIL_REFERENCE_RE.test(value));
}

function boundedStringArray(value, maximum = MAX_TAGS) {
  return Array.isArray(value)
    && value.length <= maximum
    && value.every((item) => nonEmptyString(item, MAX_TAG_LENGTH));
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function validNestedSource(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return false;
  return nonEmptyString(source.id, 200)
    && nonEmptyString(source.name, 300)
    && optionalString(source.accent, 100)
    && optionalString(source.site, 4096)
    && publicHttpUrl(source.site)
    && (source.ok === undefined || typeof source.ok === "boolean")
    && (source.count === undefined || finiteNumber(source.count));
}

function validRelatedArticle(related) {
  if (!related || typeof related !== "object" || Array.isArray(related)) return false;
  return nonEmptyString(related.title, MAX_ARTICLE_STRING)
    && nonEmptyString(related.source, 300)
    && publicHttpUrl(related.url, { required: true })
    && nonEmptyString(related.published_at, 128)
    && optionalString(related.source_id, 200)
    && optionalString(related.accent, 100);
}

function validArticle(article) {
  if (!article || typeof article !== "object" || Array.isArray(article)) return false;
  const requiredStrings = [
    "id", "title", "published_at", "source", "source_id", "lane", "software_group",
  ];
  if (!requiredStrings.every((field) => nonEmptyString(article[field]))) return false;
  if (!boundedString(article.summary)) return false;
  if (!ARTICLE_LANE_VALUES.has(article.lane) || !publicHttpUrl(article.url, { required: true })) return false;
  if (!["image", "source_site", "topic", "accent"].every((field) => optionalString(article[field]))) return false;
  if (!thumbnailReferenceIsValid(article.image) || !publicHttpUrl(article.source_site)) return false;
  for (const field of ["software_tags", "topic_tags", "priority_reasons"]) {
    if (field in article && !boundedStringArray(article[field])) return false;
  }
  if (article.related !== undefined
    && (!Array.isArray(article.related) || article.related.length > MAX_RELATED || !article.related.every(validRelatedArticle))) {
    return false;
  }
  if (article.sources !== undefined
    && (!Array.isArray(article.sources) || article.sources.length > MAX_RELATED || !article.sources.every(validNestedSource))) {
    return false;
  }
  for (const field of ["source_count", "cluster_size", "priority_score"]) {
    if (article[field] !== undefined && !finiteNumber(article[field])) return false;
  }
  return true;
}

export function feedPayloadIsStructurallyCompatible(payload) {
  if (!feedPayloadHasSchema(payload) || !payload || typeof payload !== "object") return false;
  if (payload.schema_version !== undefined && payload.schema_version !== FEED_SCHEMA_VERSION) return false;
  if (!nonEmptyString(payload.generated_at, 128)) return false;
  if (!Array.isArray(payload.articles) || payload.articles.length > MAX_FEED_ARTICLES) {
    return false;
  }
  if (!Array.isArray(payload.sources) || payload.sources.length > MAX_FEED_SOURCES || !payload.sources.every(validNestedSource)) {
    return false;
  }
  if (payload.unavailable_sources !== undefined
    && (!Array.isArray(payload.unavailable_sources)
      || payload.unavailable_sources.length > MAX_FEED_SOURCES
      || !payload.unavailable_sources.every((item) => nonEmptyString(item, 300)))) {
    return false;
  }
  for (const field of ["classification_revision", "classification_version"]) {
    if (payload[field] !== undefined && !finiteNumber(payload[field])) return false;
  }
  for (const field of ["unique_count", "duplicates_collapsed", "carried_forward_count"]) {
    if (payload[field] !== undefined && !finiteNumber(payload[field])) return false;
  }
  return payload.articles.every(validArticle);
}

export { validArticle as articleIsRenderSafe };
export { thumbnailReferenceIsValid };
