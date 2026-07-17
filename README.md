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
Python 3 on the computer. There are no third-party packages, containers,
accounts, API keys, or subscription fees.

## Mobile companion

The public-safe mobile edition is built from `mobile/` and deployed through the
`Refresh mobile signal` GitHub Pages workflow. GitHub gathers the same feeds,
runs the existing classification and deduplication rules, and refreshes the
hosted static feed every 30 minutes. The Windows PC does not need to be on.

Open [CG Signal Mobile](https://hattedpuppet.github.io/cg-signal/) on Android,
then use the browser menu to install it or add it to the home screen.

The mobile edition includes Latest Signal, one-tap category and source chips
directly in the feed controls, a header search field with text and hashtag
support, an unread view, a short Daily Brief, offline fallback, and Android
home-screen installation. The header and filter drawer now share one compact
sticky surface: tap or swipe its handle to pull the full controls out. A
persistent Top button jumps directly to the first visible article without a
smooth-scroll delay. Read markers are stored only in that phone's browser.

Source selection never requires opening a separate discovery panel. The optional
**Manage sources** sheet is only for hiding or restoring sources on this device.

Mobile source enablement is also device-local: **Manage sources** can hide or
restore any source present in the hosted feed without changing the desktop or
uploading the preference. Newly hosted sources appear automatically. Custom
RSS URLs added only to the desktop remain desktop-only unless they are later
added to the repository's public feed configuration.

The export uses an explicit field allowlist. It never publishes the desktop
SQLite archive, saved or archived IDs, notes, source preferences, feedback, or
any `.cache` file. Learning Library, History, source management, and preference
tuning remain desktop-only.

To build the same deployment locally from the current feed cache:

```powershell
python mobile/build_mobile.py --source-json .cache/feed-cache.json
```

The generated, disposable site is written to `mobile/dist/` and is ignored by
Git.

## How it behaves

- Feeds check quietly every 15 minutes while the dashboard is open and visible.
  **Check feeds** bypasses the cache and checks every source immediately.
- A small cache is stored in `.cache/feed-cache.json` so the last successful
  briefing remains available during a temporary feed failure.
- Every gathered story is also retained in `.cache/cg-signal.db`, a local
  SQLite archive. **History** searches this complete collection with paging, so
  articles remain findable after they disappear from a publisher's feed.
- Read, saved, archived, research-note, recommendation, and source-tuning states are persisted by the local server in
  `.cache/user-state.json`. Browser storage acts as a fallback and migrates
  existing saved/read state automatically. The database mirrors article state
  so full-history searches can use `#is:` filters and research notes.
- **Latest Signal** keeps the primary feed chronological and offers live
  category counts. Multi-tool stories can be found from every relevant software
  filter while appearing only once in the All Stories feed and retaining one
  clear primary card label.
- Desktop search lives in the sticky topbar, and the persistent Top button
  jumps directly to the first visible article for quick reading recovery.
- Desktop **View** switches between grid and list layouts, while **Density** toggles
  a locally saved compact card mode with smaller thumbnails and tighter metadata.
- A divider identifies stories published since the previous visit. Keyboard
  triage uses `J`/`K` to move, `Enter` to open, `S` to save, `A` to archive,
  and `M` to toggle read state.
- **Daily brief** selects up to nine high-value unread stories, prioritizing six
  technical items and reserving room for three industry updates. **More like
  this** and **Less like this** tune this brief locally. Its Preference tuning
  panel shows the learned category, technique, and source weights and the
  adjustment applied to each selected story. Its summaries are built from RSS
  excerpts and do not call an external AI service.
- Saved stories form a durable Learning Library grouped by software/context.
  Each item accepts a searchable research note. Saved and archived items remain
  available even after the live RSS window moves on, and every archived item
  can be restored.
- Search supports ordinary text plus combinable tags: `#unreal`, `#blender`,
  `#substance`, `#topic:animation`, `#source:"80 Level"`,
  `#is:unread`, `#is:saved`, and `#is:new`. Prefix a term with `-` to exclude it,
  such as `-#industry`.
- Individual sources can be temporarily filtered, reduced in the Daily Brief,
  or muted. Clicking a source isolates it; clicking it again restores all
  sources, and Ctrl/Cmd-click combines sources. **Reset** restores all source
  settings. **Manage** opens a local source manager where RSS/Atom URLs can be
  tested, added, disabled, or re-enabled without editing code.
- Articles remain on their publishers' websites; the dashboard only shows RSS
  metadata and short excerpts.
- When a feed omits thumbnails, the dashboard reads the article's standard
  Open Graph preview image and caches that lookup locally for 30 days.
- Deduplication compares canonical links, shared outbound links, similar titles,
  and product/version signatures that often survive between Japanese and
  English headlines. Related coverage remains expandable beneath the lead card.
- A separate information-type filter divides **Tech & Development** coverage
  from **Industry & Business** reporting. The compact **Categories** row uses a
  single selection; choosing another category replaces the current one, while
  clicking the active category returns to **All categories**.
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

The project intent and product requirements are maintained in `PRD.md`.
