# CG Signal Mobile

This directory contains the public-safe static mobile companion.

- `site/` is the responsive installable web app.
- `build_mobile.py` gathers the normal feeds and writes a sanitized deployment
  to `dist/`.
- `dist/` is generated and must not be committed.

Only allowlisted RSS-derived fields enter `feed.json`. The builder uses an
isolated temporary cache, so desktop history, notes, saved state, preferences,
and locally configured custom sources cannot be included accidentally.
The workflow separately caches only public publisher validators, per-source RSS
snapshots, and thumbnail lookups. This allows conditional HTTP requests between
runs without placing desktop state in the deployment artifact.

Before a scheduled deployment, the workflow downloads the currently published
public feed and merges it with the new snapshot. The merge retains up to 1,500
allowlisted articles from the last 100 days, removes duplicate URLs, and drops
anything outside the public field allowlist again. This cushions transient feed
failures and short publisher RSS windows without exposing desktop-only data.

The GitHub Pages workflow runs regression tests before every deployment. If no
valid articles can be gathered, the build fails and the previous working Pages
deployment remains available.
