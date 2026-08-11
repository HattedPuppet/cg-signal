# CG Signal Mobile

This directory contains the public-safe static mobile companion.

- `site/` is the responsive installable web app.
- `build_mobile.py` gathers the normal feeds and writes a sanitized deployment
  to `dist/`.
- `dist/` is generated and must not be committed.

Only allowlisted RSS-derived fields enter `feed.json`. The builder uses an
isolated temporary cache, so desktop history, notes, saved state, preferences,
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
Use the manual `discard_history` input to remove the carried history snapshot
and its thumbnail index/assets before a build while retaining HTTP validators
and feed validators.

The GitHub Pages workflow runs regression tests before every deployment. If no
valid articles can be gathered, the build fails and the previous working Pages
deployment remains available.
