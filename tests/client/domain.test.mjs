import test from "node:test";
import assert from "node:assert/strict";

import {
  ARTICLE_LANE_VALUES,
  SOFTWARE_GROUP_COLORS,
  SOFTWARE_GROUP_ORDER,
  FEED_SCHEMA_VERSION,
  articleCategories,
  articleSourceIds,
  articleTopics,
  articleWithinPublicationWindow,
  feedPayloadIsStructurallyCompatible,
  matchesSearch,
  normalizeSearchQuery,
  publicationWindowStart,
  searchTokens,
  softwareGroup,
  thumbnailReferenceIsValid,
} from "../../static/domain.mjs";

const article = {
  id: "story-1",
  title: "A Blender workflow 日本語 guide",
  summary: "Production techniques for procedural materials",
  url: "https://example.com/story-1",
  image: "",
  source: "Example Studio",
  source_id: "example",
  source_site: "https://example.com",
  accent: "#fff",
  lane: "Tech & Development",
  software_group: "Blender",
  software_tags: ["Blender"],
  topic_tags: ["Materials & texturing"],
  priority_reasons: [],
  published_at: "2026-08-05T12:00:00Z",
  sources: [{ id: "example", name: "Example Studio" }],
  related: [],
};

test("search grammar handles aliases, quoted values, negation, and Unicode", () => {
  const tokens = searchTokens('#unreal -#industry #topic:"Materials & texturing" "日本語"');
  assert.deepEqual(tokens, [
    { negative: false, field: "software", value: "unreal engine" },
    { negative: true, field: "software", value: "industry context" },
    { negative: false, field: "topic", value: "materials & texturing" },
    { negative: false, field: "text", value: "日本語" },
  ]);
  assert.equal(normalizeSearchQuery("#unreal engine"), '#software:"Unreal Engine"');
});

test("shared search delegates #is status semantics to the caller", () => {
  const calls = [];
  assert.equal(matchesSearch(article, "#blender #is:saved", {
    isStatus: (item, status) => {
      calls.push([item.id, status]);
      return status === "saved";
    },
  }), true);
  assert.deepEqual(calls, [["story-1", "saved"]]);
  assert.equal(matchesSearch(article, "#is:saved", { isStatus: () => false }), false);
});

test("categories and source IDs are normalized consistently", () => {
  assert.deepEqual(articleCategories(article), ["Blender"]);
  assert.deepEqual(articleTopics(article), []);
  assert.equal(softwareGroup({ lane: "Industry" }), "Industry context");
  assert.deepEqual([...articleSourceIds(article)], ["example"]);
  assert.equal(SOFTWARE_GROUP_ORDER[0], "Unreal Engine");
  assert.equal(SOFTWARE_GROUP_COLORS.Blender, "#f18a21");
});

test("production articles expose their topic fallback", () => {
  assert.deepEqual(articleCategories({ lane: "Tech & Development" }), ["Production techniques"]);
  assert.deepEqual(articleTopics({ lane: "Tech & Development" }), ["Other production"]);
});

test("publication windows share month and quarter boundaries", () => {
  const now = new Date("2026-08-07T12:00:00Z");
  const monthStart = publicationWindowStart("month", now);
  const quarterStart = publicationWindowStart("quarter", now);
  assert.deepEqual([monthStart.getFullYear(), monthStart.getMonth(), monthStart.getDate()], [2026, 7, 1]);
  assert.deepEqual([quarterStart.getFullYear(), quarterStart.getMonth(), quarterStart.getDate()], [2026, 5, 1]);
  assert.equal(articleWithinPublicationWindow({ published_at: "2026-08-01T00:00:00Z" }, "month", now), true);
  assert.equal(articleWithinPublicationWindow({ published_at: "2026-07-31T10:00:00Z" }, "month", now), false);
  assert.equal(articleWithinPublicationWindow({ published_at: "not-a-date" }, "month", now), true);
  assert.equal(articleWithinPublicationWindow({ published_at: "2020-01-01T00:00:00Z" }, "all", now), true);
});

test("feed schema contract accepts only the canonical structural revision", () => {
  assert.equal(FEED_SCHEMA_VERSION, 1);
  assert.deepEqual([...ARTICLE_LANE_VALUES], ["Tech & Development", "Industry", "Business"]);
  const valid = {
    feed_schema_version: 1,
    generated_at: "2026-08-05T12:00:00Z",
    sources: [{ id: "example", name: "Example Studio" }],
    articles: [article],
  };
  assert.equal(feedPayloadIsStructurallyCompatible(valid), true);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, summary: "" }] }), true);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, summary: null }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, feed_schema_version: 2 }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, classification_version: 999 }), true);
  assert.equal(feedPayloadIsStructurallyCompatible({ feed_schema_version: 1, articles: [{ ...article, lane: "Unknown" }] }), false);
});

test("feed schema rejects malformed render fields and URLs", () => {
  const valid = {
    feed_schema_version: 1,
    generated_at: "2026-08-05T12:00:00Z",
    sources: [{ id: "example", name: "Example Studio" }],
    articles: [article],
  };
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, software_tags: null }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, sources: [null] }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, related: [{ title: "x" }] }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, url: "javascript:alert(1)" }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, image: "https://cdn.example.test/card.jpg" }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, unavailable_sources: [null] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, articles: [{ ...article, priority_score: Number.NaN }] }), false);
  assert.equal(feedPayloadIsStructurallyCompatible({ ...valid, schema_version: 2 }), false);
});

test("thumbnail references are exact content-addressed project-relative assets", () => {
  const reference = `thumbnails/${"a".repeat(64)}.jpg`;
  assert.equal(thumbnailReferenceIsValid(reference), true);
  assert.equal(feedPayloadIsStructurallyCompatible({
    feed_schema_version: 1,
    generated_at: "2026-08-05T12:00:00Z",
    sources: [{ id: "example", name: "Example Studio" }],
    articles: [{ ...article, image: reference }],
  }), true);
  assert.equal(thumbnailReferenceIsValid(`thumbnails/${"A".repeat(64)}.jpg`), false);
  assert.equal(thumbnailReferenceIsValid("/assets/card.jpg"), false);
  assert.equal(thumbnailReferenceIsValid("https://cdn.example/card.jpg"), false);
  assert.equal(thumbnailReferenceIsValid(""), true);
});
