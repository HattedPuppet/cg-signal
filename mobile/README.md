# CG Signal Mobile

This directory contains the public-safe static mobile companion.

- `site/` is the responsive installable web app.
- `build_mobile.py` gathers the normal feeds and writes a sanitized deployment
  to `dist/`.
- `dist/` is generated and must not be committed.

Only allowlisted RSS-derived fields enter `feed.json`. The builder uses an
isolated temporary cache, so desktop history, saved state, preferences,
and locally configured custom sources cannot be included accidentally.
The workflow keeps public publisher validators, per-source RSS snapshots, and
validated thumbnail bytes under `.mobile-cache/http`. This allows conditional
HTTP requests between runs without placing desktop state in the deployment
artifact.

The workflow keeps request state and a sanitized history snapshot in the
private, repository-scoped Actions cache (`.mobile-cache/http` and
`.mobile-cache/history/feed.json`). A cache miss builds from the current
snapshot only; no public Pages feed is downloaded for recovery. The merge
retains up to 1,500 allowlisted articles from the last 100 days, removes
duplicate URLs, and drops anything outside the public field allowlist again.
The publication policy is reapplied to fresh feeds, source snapshots, and
carried history so future/calendar entries cannot return through a cache.
Source metadata exposes only the last successful check and stale-cache status,
not request validators or error details.
Use the manual `discard_history` input to remove the carried history snapshot
and its thumbnail index/assets before a build while retaining HTTP validators
and feed validators.

The GitHub Pages workflow runs regression tests for code changes; scheduled
refreshes build the already-tested default branch. If no valid articles can be
gathered, the build fails and the previous working Pages deployment remains
available. Thumbnail enrichment is optional: a timeout or failure publishes
current articles with available verified images instead of freezing the feed.

The workflow targets a 30-minute refresh interval, not a timing guarantee. The
app checks for published updates every five minutes while visible, offers a
manual check, and warns once the published snapshot is over 90 minutes old.
Neither a phone refresh nor the manual button forces an RSS publisher update.
The service worker marks offline feed responses explicitly; existing pins and
source preferences survive shell/cache updates.

The shell uses compact cards by default while honoring an existing comfortable
layout preference. `site/feed-list.mjs` owns keyed cards, 40-story rendering
batches, and viewport anchors; the app owns filtering and staged feed updates.
Scroll near the end to load another batch, or use the accessible Show more
button. New arrivals are cached immediately but enter the visible list only
when the reader accepts the new-stories button. That button reflects the active
filters; publication status still describes the latest downloaded snapshot.
Tab-scoped session storage retains the view, filters, search, and reading
anchor across reloads. Pins and source preferences remain in local storage;
this release does not add syncing or a durable article library.
