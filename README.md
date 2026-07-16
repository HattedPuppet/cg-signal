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

## How it behaves

- Feeds refresh at most every 15 minutes unless you click **Check feeds**, which
  bypasses the cache and checks every source immediately.
- A small cache is stored in `.cache/feed-cache.json` so the last successful
  briefing remains available during a temporary feed failure.
- Read, saved, and archived states are persisted by the local server in
  `.cache/user-state.json`. Browser storage acts as a fallback and migrates
  existing saved/read state automatically.
- **Latest Signal** keeps the primary feed chronological and offers live
  category counts. Multi-tool stories can be found from every relevant software
  filter while appearing only once in the All Stories feed and retaining one
  clear primary card label.
- **Daily brief** selects up to nine high-value unread stories, prioritizing six
  technical items and reserving room for three industry updates. Its summaries
  are built from RSS excerpts and do not call an external AI service.
- Stories can be saved for research or archived out of the active feed. The
  archive remains searchable and every item can be restored.
- Articles remain on their publishers' websites; the dashboard only shows RSS
  metadata and short excerpts.
- When a feed omits thumbnails, the dashboard reads the article's standard
  Open Graph preview image and caches that lookup locally for 30 days.
- Deduplication compares canonical links, shared outbound links, similar titles,
  and product/version signatures that often survive between Japanese and
  English headlines. Related coverage remains expandable beneath the lead card.
- A separate information-type filter divides **Tech & Development** coverage
  from **Industry & Business** reporting. The compact **Categories** row supports
  multiple selections with OR behavior.
- Selecting **Production techniques** alone reveals a contextual subcategory row
  for modeling, materials, animation, rendering, VFX, technical art, pipelines,
  and game development. The main category choices and their counts remain
  visible while refining, and the subcategory row stays hidden elsewhere.
- The current tool watchlist includes Unreal Engine, Substance 3D Painter and
  Designer, Blender, Houdini, and Spine terminology for software grouping and
  relevance scoring.
  Unreal Engine and Blender also have first-party feeds; the other vendors do
  not currently expose reliable general RSS feeds, so their coverage comes from
  the editorial sources.

The hidden launcher records the active server in `.cache/server.pid`; the stop
shortcut only closes that local CG Signal process.

The project intent and product requirements are maintained in `PRD.md`.
