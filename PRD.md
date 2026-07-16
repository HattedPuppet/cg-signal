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
- Remain private, local, account-free, and free of ongoing API or SaaS costs.
- Make ranking understandable rather than presenting a black-box feed.

## Non-goals

- Replacing publishers' full articles or bypassing their websites.
- Building a general-purpose social-media reader.
- Ingesting paid APIs such as the X API.
- Requiring cloud hosting, user accounts, advertising, or behavioral tracking.
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

### Latest Signal facets

- Present the primary feed in chronological, newest-first order without a
  relevance threshold, result cap, or hidden re-ranking.
- Provide one visible **Categories** selector containing only non-empty groups,
  with live counts that respond to the other active filters.
- Classify software/context under Unreal Engine, Blender, Substance Painter,
  Substance Designer, Houdini, Spine, **Production techniques**, or **Industry
  context**.
- Classify production topics under modeling, materials, animation, rendering,
  VFX, technical art, pipelines, or game development, with a clear fallback for
  unmatched production coverage.
- Reveal production subcategories only when **Production techniques** is the
  sole selected category. Hide and clear them for software and industry
  categories to keep the filter area compact.
- Retain all detected software and production-topic tags. Multi-tool articles
  must match every relevant category filter while keeping one primary card label
  and one card in the unfiltered feed.
- Prefer software mentions in titles over incidental summary mentions when
  choosing the primary label.
- Support multiple active category choices with OR behavior. Production
  subcategories also use OR behavior and refine Production techniques only.
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

### Save and archive

- Save stories as a durable research collection.
- Archive stories out of Latest Signal and Unread views.
- Provide a dedicated archive with restore controls.
- Persist read, saved, and archived identifiers in the local server's data
  directory, with browser storage as a migration and failure fallback.

### Local application experience

- Start silently from the installed Windows shortcut.
- Remain usable as a responsive browser application and installable PWA.
- Require no containers, third-party Python packages, accounts, or API keys.

## Relevance model

Daily Brief ranking is deterministic and rule-based:

`priority = technical base + tool matches + depth signals + source confidence + corroboration - promotional noise`

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
5. Use source, information-type, and search filters for deeper exploration.

## Privacy, cost, and data constraints

- No telemetry, advertising, or external user profile.
- No paid APIs or recurring SaaS dependency.
- Feed metadata, preview-image cache, and user state remain under the local
  project directory.
- Optional future models must be opt-in, local, and removable without breaking
  the core feed.

## Success measures

Success is evaluated through the user's experience rather than remote
analytics:

- The short Daily Brief can normally be reviewed in under ten minutes.
- Duplicate announcements rarely require opening more than one card.
- Latest Signal stays chronological while categories and contextual production
  subcategories reliably narrow it without duplicating cards.
- Saved material remains retrievable after browser restarts.
- Feed failures do not prevent access to the last successful briefing.
- The dashboard remains useful when all optional intelligence features are off.

## Quality requirements

- Software and production-topic classification, relevance, state normalization,
  feed parsing, thumbnail discovery, and deduplication behavior must have
  automated regression tests.
- State writes must be bounded, validated, and atomic.
- Controls must have accessible names and keyboard-focus behavior.
- The UI must remain usable at desktop and mobile widths.

## Roadmap

### Near term

- Searchable long-term article archive.
- Learning Library organized by software and production topic.
- Explicit **More like this / Less like this** preference controls.
- Carefully selected official YouTube RSS sources.

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

- Whether saved tutorials should automatically enter a separate Learning
  Library.
- Whether archived items should be retained indefinitely or pruned after a
  configurable period.
- Whether explicit preference feedback should tune rule weights before adding
  any local model.
- What local hardware budget is acceptable for optional summarization and
  speech generation.
