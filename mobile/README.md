# CG Signal Mobile

This directory contains the public-safe static mobile companion.

- `site/` is the responsive installable web app.
- `build_mobile.py` gathers the normal feeds and writes a sanitized deployment
  to `dist/`.
- `dist/` is generated and must not be committed.

Only allowlisted RSS-derived fields enter `feed.json`. The builder uses an
isolated temporary cache, so desktop history, notes, saved state, preferences,
and locally configured custom sources cannot be included accidentally.

The GitHub Pages workflow runs regression tests before every deployment. If no
valid articles can be gathered, the build fails and the previous working Pages
deployment remains available.
