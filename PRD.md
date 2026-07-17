# CG Signal Product Requirements Document

**Status:** Active local product  
**Last updated:** July 2026

## Product summary

CG Signal is a private, local dashboard for efficiently gathering high-value
CG, animation, and game-development information from many publishers without
opening each site individually. It reduces repeated coverage, separates
technical information from business reporting, and prioritizes stories around
the user's production tools and interests.

## Problem

Useful information is distributed across English and Japanese publications,
official product blogs, research sources, and developer interviews. Reviewing
each site individually is slow, prolific sources can overwhelm quieter ones,
and multiple publications often report the same underlying announcement.

The product must answer three questions quickly:

1. What deserves attention today?
2. Why is it relevant to the user's work?
3. What can safely be ignored, saved for later, or archived?

## Product vision

Create a calm, trustworthy research dashboard that makes a broad chronological
feed easy to narrow, while keeping the original publishers one click away.

## Primary user

A CG and game-development practitioner who currently uses:

- Unreal Engine
- Substance 3D Painter
- Blender

And is actively interested in:

- Substance 3D Designer
- Houdini
- Spine

## Goals

- Minimize time spent scanning low-value or repeated stories.
- Surface practical workflows, techniques, releases, research, and production
  breakdowns related to the user's tools.
- Keep industry and business reporting available without letting it dominate
  technical research.
- Preserve useful material through saving and archiving.
- Make accumulated learning searchable through notes and structured queries.
- Keep personal data private and local, require no account inside CG Signal,
  and remain free of ongoing API or SaaS costs.
- Make ranking understandable rather than presenting a black-box feed.
- Provide an always-available mobile reading surface without requiring the
  Windows PC to remain powered on.

## Non-goals

- Replacing publishers' full articles or bypassing their websites.
- Building a general-purpose social-media reader.
- Ingesting paid APIs such as the X API.
- Requiring cloud hosting for the desktop application or storing personal
  research state in a hosted service.
- Requiring an account inside the reader, advertising, or behavioral tracking.
- Automatically publishing, messaging, or sharing content externally.

## Product principles

1. **Signal before volume:** adding sources is useful only when relevance and
   deduplication remain effective.
2. **Original source first:** every story opens on the publisher's website.
3. **Transparent prioritization:** show why an item was selected.
4. **Quiet by default:** repeated coverage and archived items stay out of the
   active reading flow.
5. **Local by default:** personal state and optional intelligence remain on the
   user's computer.
6. **Graceful degradation:** cached feeds and browser state keep the dashboard
   useful during temporary source or local-server failures.

## Current functional requirements

### Feed gathering

- Gather supported RSS and Atom sources at most every 15 minutes unless a
  manual refresh is requested.
- Continue serving the last successful cache when individual feeds fail.
- Retrieve standard article preview images when feeds omit thumbnails.
- Support English and Japanese titles, summaries, dates, and classifications.

### Organization and deduplication

- Fold likely duplicate coverage into one lead card.
- Retain links to related reports beneath the lead story.
- Separate **Tech & Development** from **Industry & Business**.
- Support software/context, production-subcategory, information-type, source,
  search, saved, unread, and archive filters.
- Improve bilingual deduplication by requiring a shared event type and
  distinctive entities when English and Japanese titles have little literal
  overlap.

### Latest Signal facets

- Present the primary feed in chronological, newest-first order without a
  relevance threshold, result cap, or hidden re-ranking.
- Provide one visible **Categories** selector containing only non-empty groups,
  with live counts that respond to the other active filters.
- Classify software/context under Unreal Engine, Unity, Blender, Substance 3D,
  Houdini, AI, **Production techniques**, or **Industry context**. Substance
  Painter, Substance Designer, and generic Substance coverage must share the
  Substance 3D category. Keep watchlist categories visible even when their
  current filtered count is zero.
- Classify production topics under modeling, materials, animation, rendering,
  VFX, technical art, pipelines, game development, breakdowns, research,
  releases, or assets/inspiration, with a clear fallback for unmatched
  production coverage.
- Reveal production subcategories only when **Production techniques** is the
  sole selected category. Hide and clear them for software and industry
  categories to keep the filter area compact.
- Keep every main category choice and its unrefined count visible while a
  Production techniques subcategory is active.
- Retain all detected software and production-topic tags. Multi-tool articles
  must match every relevant category filter while keeping one primary card label
  and one card in the unfiltered feed.
- Prefer software mentions in titles over incidental summary mentions when
  choosing the primary label.
- Allow exactly one active category and one active Production techniques
  subcategory. Selecting another option replaces the current selection;
  selecting the active option resets that row to its **All** state. Clicking an
  already-active **All** option has no further effect.
- Keep read stories in Latest Signal; remove only archived stories from the
  active chronological feed.
- Do not expose broad inferred subject filters such as Engines, 3D Art, Tools,
  Game Development, or Industry when their classification is unreliable.

### Daily Brief

- Select no more than nine unread, non-archived stories.
- Prioritize up to six technical stories and include up to three industry
  stories so important business context is not lost.
- Order selections by relevance and freshness.
- Provide short extractive summaries and relevance reasons.
- Allow the selected brief—not the entire backlog—to be marked read at once.
- Accept explicit More/Less feedback and use software, topic, and source overlap
  to adjust only Daily Brief ranking. Keep Latest Signal chronological.
- Expose the accumulated category, technique, and source weights, including the
  numeric adjustment applied to each Daily Brief story, and allow feedback to
  be reset.
- Exclude muted sources and lower reduced sources without deleting their data.

### Search and triage

- Search article titles, summaries, sources, classifications, related coverage,
  relevance reasons, and personal research notes.
- Support AND-combined hashtags for common software plus structured
  `#software:`, `#topic:`, `#source:`, and `#is:` operators.
- Support quoted values and exclusions prefixed by `-`.
- Mark stories published since the previous visit without reordering the feed.
- Support keyboard triage with J/K, Enter, S, A, and M while ignoring shortcuts
  in text-entry controls.

### Learning Library and archive

- Save stories into a durable Learning Library grouped by primary software or
  context.
- Attach searchable local research notes to saved stories.
- Archive stories out of Latest Signal and Unread views.
- Provide a dedicated archive with restore controls.
- Retain gathered article metadata in a durable local history after articles
  rotate out of their publishers' RSS feeds.
- Provide paginated full-history search using the same text, hashtag,
  information-type, and source filters as the current feed.
- Populate the Learning Library and archive views from durable history rather
  than limiting them to the current RSS window.
- Persist read, saved, archived, note, feedback, and source-preference state in
  the local server's data directory, with browser storage as a migration and
  failure fallback.

### Source and refresh controls

- Allow a source to be temporarily filtered, reduced in personalized ranking,
  muted, or restored.
- Make a normal source click isolate that source, a repeated click restore all,
  and Ctrl/Cmd-click combine multiple sources.
- Provide an interface to add custom RSS or Atom URLs, test a feed before or
  after adding it, and disable or re-enable built-in and custom sources.
- Preserve historical articles when their source is disabled.
- Check feeds every 15 minutes while the visible dashboard is open, retain the
  current board on a background failure, and announce newly arrived stories.

### Local application experience

- Start silently from the installed Windows shortcut.
- Remain usable as a responsive browser application and installable PWA.
- Require no containers, third-party Python packages, accounts, or API keys.

### Hosted mobile companion

- Generate a static mobile feed from the same supported sources,
  classification, relevance, and deduplication rules as the desktop app.
- Refresh the hosted feed every 30 minutes through a scheduled GitHub workflow
  so availability does not depend on the user's PC.
- Provide Latest Signal, information-type and category filtering, source
  selection, text and hashtag search, unread filtering, and a short Daily Brief.
- Keep search, information type, category, and source controls reachable from
  any scroll position through a compact mobile discovery surface, with
  touch-sized one-tap source choices, a compact active-source summary in place
  of a native combo box, and clear active-state feedback.
- Let each phone enable or disable hosted sources independently, storing the
  disposable preference only in that browser and providing an Enable all reset.
- Automatically expose every source present in the hosted feed to mobile source
  management; keep desktop-only custom RSS URLs local unless explicitly added
  to a future public feed configuration.
- Support Android home-screen installation and retain the most recently opened
  feed for temporary offline access.
- Store read markers only in the phone's local browser storage.
- Keep History, Learning Library, saved and archived state, notes, preference
  feedback, and source management exclusive to the desktop application.
- Export only explicitly allowlisted public RSS-derived fields. Never publish
  `.cache`, the SQLite database, user state, notes, or preference data.

## Relevance model

Daily Brief ranking is deterministic and rule-based:

`priority = technical base + tool matches + depth signals + source confidence + corroboration - promotional noise + explicit preference matches - reduced-source penalty`

Scores select and order the short Daily Brief; they do not reorder Latest
Signal and are not a claim about objective article quality. Ranking rules must
remain testable and easy to adjust as the user's interests change.

## Core user journey

1. Open CG Signal from the Start menu.
2. Review Latest Signal, narrow it by category, optionally refine Production
   techniques, or open the Daily Brief.
3. Open valuable stories on the original site.
4. Save evergreen learning material, archive low-priority items, and mark the
   reviewed briefing read.
5. Use text or hashtag search for deeper exploration and add notes to saved
   learning material.

## Privacy, cost, and data constraints

- No telemetry, advertising, or external user profile.
- No paid APIs or recurring SaaS dependency.
- Feed metadata, preview-image cache, SQLite article history, source
  configuration, and user state remain under the local project directory.
- The hosted mobile artifact may contain current public RSS metadata and
  excerpts, but no personal state or long-term archive.
- Optional future models must be opt-in, local, and removable without breaking
  the core feed.

## Success measures

Success is evaluated through the user's experience rather than remote
analytics:

- The short Daily Brief can normally be reviewed in under ten minutes.
- Duplicate announcements rarely require opening more than one card.
- Latest Signal stays chronological while categories and contextual production
  subcategories reliably narrow it without duplicating cards.
- Saved material and notes remain retrievable and searchable after browser restarts.
- Historical articles remain searchable after the live RSS feed has rotated.
- Feed failures do not prevent access to the last successful briefing.
- The dashboard remains useful when all optional intelligence features are off.

## Quality requirements

- Software and production-topic classification, relevance, state normalization,
  feed parsing, thumbnail discovery, source configuration, archive search, and
  deduplication behavior must have automated regression tests.
- State writes must be bounded, validated, and atomic.
- Controls must have accessible names and keyboard-focus behavior.
- The UI must remain usable at desktop and mobile widths.

## Roadmap

### Near term

- Carefully selected official YouTube RSS sources.
- Optional local backup and export for the accumulated archive and notes.

### Optional intelligence

- **Local language model — high potential usefulness:** improve Japanese-English
  summaries, semantic deduplication, flexible tagging, and personalized daily
  synthesis. It must remain optional because model downloads, RAM/VRAM use, and
  slower refreshes are meaningful costs even without a subscription.
- **Local voice output — moderate situational usefulness:** make the short daily
  brief accessible hands-free. It should target the briefing rather than full
  articles and remain optional because pronunciation quality, especially for
  Japanese names and CG product terminology, varies by installed voice.

## Open decisions

- Whether archived items should be retained indefinitely or pruned after a
  configurable period.
- What local hardware budget is acceptable for optional summarization and
  speech generation.
