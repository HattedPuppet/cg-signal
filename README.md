# CG Signal

A private, local RSS dashboard for CG and game-development news. It combines
CG and game publications, production breakdowns, animation-industry reporting,
graphics research, Japanese developer interviews, and official Unreal Engine
and Blender updates in one responsive timeline. Likely duplicate coverage is
folded into a single story card.

## Start the dashboard

On the configured Windows computer, open **CG Signal** from the Start menu. The
shortcut starts the dashboard silently if needed, then opens
`http://127.0.0.1:4310` in the default browser. Repeated clicks reuse the same
local server. Use **CG Signal - Stop** from the Start menu when you want to shut
it down.

For a portable fallback, double-click `run-dashboard.bat` or run
`launch-dashboard.ps1`. To recreate the Start menu shortcuts after moving the
project, run `install-shortcuts.ps1` once.

Alternatively, run:

```powershell
python server.py
```

The launcher uses Codex's bundled Python when available and otherwise looks for
Python 3 on the computer. The Python server itself has no third-party runtime
dependencies. No containers, accounts, API keys, or subscription fees are
required.

## Mobile companion

The public-safe mobile edition is built from `mobile/` and deployed through the
`Refresh mobile signal` GitHub Pages workflow. GitHub gathers the same feeds,
runs the existing classification and deduplication rules, and refreshes the
hosted static feed every 30 minutes. The workflow keeps HTTP validators and a
sanitized 100-day rolling history (up to 1,500 stories) in a repository-scoped
Actions cache, so a temporary publisher outage or a story rotating out of RSS
does not make recent coverage disappear. A cache miss publishes current
content only; the Windows PC does not need to be on.

Open [CG Signal Mobile](https://hattedpuppet.github.io/cg-signal/) on Android,
then use the browser menu to install it or add it to the home screen.

The mobile edition opens to **Latest Signal**, a strict newest-first chronology
with **This month**, **Last 3 months**, and **All available** publication
windows. **Pinned** stories stay device-local. It also includes one-tap
category and source chips directly in the feed controls, a header search field
with text and hashtag support, offline fallback, and Android home-screen
installation.
Stories are grouped into **Last 24 hours**, **Last 3 days**, and **Earlier** so
freshness is visible without maintaining read markers. The header and filter
drawer share one compact sticky surface: tap or swipe its handle to pull the
full controls out. A persistent Top button jumps directly to the first visible
article without a smooth-scroll delay. Pins are stored only in that phone's
browser.

Source selection never requires opening a separate discovery panel. The optional
**Manage sources** sheet is only for hiding or restoring sources on this device.

Mobile source enablement is also device-local: **Manage sources** can hide or
restore any source present in the hosted feed without changing the desktop or
uploading the preference. Newly hosted sources appear automatically. Custom
RSS URLs added only to the desktop remain desktop-only unless they are later
added to the repository's public feed configuration.

The export uses an explicit field allowlist. It never publishes the desktop
SQLite article history, saved IDs, notes, or any `.cache` file. Learning
Library, History, notes, and desktop source configuration remain desktop-only;
mobile source enablement and pins are device-local.

To build the same deployment locally from the current feed cache:

```powershell
python mobile/build_mobile.py --source-json .cache/feed-cache.json
```

Pass `--previous-json path/to/feed.json` to exercise the same rolling-history
merge used by the Pages workflow.

The generated, disposable site is written to `mobile/dist/` and is ignored by
Git.

## How it behaves

- Feeds check quietly every 15 minutes while the dashboard is open and visible.
  An existing local feed is shown immediately while an expired cache refreshes
  in the background. **Check feeds** bypasses the cache and checks every source
  immediately.
- A small cache is stored in `.cache/feed-cache.json` so the last successful
  feed remains available during a temporary feed failure.
- Per-source snapshots and HTTP validators are stored in
  `.cache/feed-source-cache.json`. Refreshes send `If-None-Match` and
  `If-Modified-Since` when publishers support them, avoiding downloads and XML
  parsing for unchanged feeds. A failed source can reuse its last snapshot.
- Every gathered story is also retained in `.cache/cg-signal.db`, a local
  SQLite archive. **History** searches this complete collection with paging, so
  articles remain findable after they disappear from a publisher's feed.
- Saved and research-note states are persisted transactionally in
  `.cache/cg-signal.db`. Browser storage acts as
  a failure fallback and migrates existing saved state automatically. Existing
  `.cache/user-state.json` data is imported once during the upgrade and then
  retained unchanged as recovery evidence. Full-history searches use the same
  SQLite state for `#is:` filters and research notes.
- **Latest Signal** is the default chronological view and defaults to stories published **this month**. The publication
  window can be widened to the **last three months** (with month separators for
  scanning) or to all articles currently returned by the feeds. Older stories
  are not deleted: desktop **History** searches the complete local archive.
- **Latest Signal** keeps the primary feed chronological and offers live
  category counts. Multi-tool stories can be found from every relevant software
  filter while appearing only once in the All Stories feed and retaining one
  clear primary card label.
- Desktop search lives in the sticky topbar, and the persistent Top button
  jumps directly to the first visible article for quick reading recovery.
- Desktop **View** switches between grid and list layouts. The left navigation
  and source panel can be collapsed into a drawer when you want more room for
  the feed.
- A divider identifies stories published since the previous visit. Keyboard
  triage uses `J`/`K` to move, `Enter` to open, and `S` to save.
- Saved stories form a durable Learning Library grouped by software/context.
  Each item accepts a searchable research note and remains available after the
  live RSS window moves on.
- Search supports ordinary text plus combinable tags: `#unreal`, `#blender`,
  `#substance`, `#topic:animation`, `#source:"80 Level"`,
  `#is:saved`, and `#is:new`. Prefix a term with `-` to exclude it,
  such as `-#industry`; `#industry` selects Industry context and `#business`
  selects Business context.
- Individual sources can be temporarily filtered or muted. Clicking a source
  isolates it; clicking it again restores all
  sources, and Ctrl/Cmd-click combines sources. **Reset** restores all source
  settings. **Manage** opens a local source manager where RSS/Atom URLs can be
  tested, added, disabled, or re-enabled without editing code.
- Articles remain on their publishers' websites; the dashboard only shows RSS
  metadata and short excerpts.
- When a feed omits thumbnails, articles are published to the dashboard first.
  A separate background pass reads standard Open Graph preview images, updates
  the visible cards when ready, and caches validated JPEG/PNG/WebP bytes as
  app-owned content-addressed assets.
- Deduplication compares canonical links, shared outbound links, similar titles,
  and product/version signatures that often survive between Japanese and
  English headlines. Related coverage remains expandable beneath the lead card.
- Separate information-type filters keep **Tech & Development**, **Industry**,
  and **Business** exclusive. Player-facing game news and updates (including
  DLC, gacha, battle passes, in-game pricing, releases, and roadmaps) use the
  Industry lane; corporate matters such as M&A, earnings, stocks, workforce,
  funding, closures, executives, and legal or company-policy reporting use
  Business. The compact **Categories** row uses a single selection; choosing
  another category replaces the current one, while clicking the active category
  returns to **All categories**.
- Selecting **Production techniques** alone reveals a contextual subcategory row
  for modeling, materials, animation, rendering, VFX, technical art, pipelines,
  game development, production breakdowns, research, product updates, and
  assets/inspiration. The main category choices and their counts remain
  visible while refining. Subcategories use the same single-selection and
  click-again-to-reset behavior, and the row stays hidden elsewhere.
- The current category watchlist includes Unreal Engine, Unity, Blender,
  Substance 3D, Houdini, and AI terminology for grouping and
  relevance scoring.
  Substance Painter, Designer, and general Substance coverage share the single
  **Substance 3D** category. Unreal Engine and Blender also have first-party feeds; the other vendors do
  not currently expose reliable general RSS feeds, so their coverage comes from
  the editorial sources.

The hidden launcher records the active server in `.cache/server.pid`; the stop
shortcut only closes that local CG Signal process.

Desktop notes and source mute state stay local to the PC. Mobile pins and source
enablement stay local to that device and are never synced to the desktop. The
project intent and product requirements are maintained in `PRD.md`.
