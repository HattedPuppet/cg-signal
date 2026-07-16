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

Create a calm, trustworthy research inbox that turns a broad feed into a short
daily decision queue while keeping the original publishers one click away.

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
- Support topic, source, search, saved, unread, and archive filters.

### Focus Inbox

- Score stories locally using software-interest matches, practical-depth terms,
  source authority, corroborating coverage, and promotional-noise penalties.
- Give title matches more weight than incidental summary mentions.
- Show concise reasons such as `Unreal Engine`, `Workflow or pipeline`, or
  `Multiple sources`.
- Group the resulting inbox into software sections for Unreal Engine, Blender,
  Substance Painter, Substance Designer, Houdini, and Spine.
- Give each story one primary software section so multi-tool articles are not
  repeated. Prefer title mentions over incidental summary mentions while
  retaining all detected software as searchable metadata.
- Place strong cross-tool stories under **Production techniques** and unmatched
  business stories under **Industry context**.
- Display only unread, non-archived strong matches.
- Cap the inbox at 30 items and limit early results from any one source.

### Daily Brief

- Select no more than nine unread, non-archived stories.
- Prioritize up to six technical stories and include up to three industry
  stories so important business context is not lost.
- Order selections by relevance and freshness.
- Provide short extractive summaries and relevance reasons.
- Allow the selected brief—not the entire backlog—to be marked read at once.

### Save and archive

- Save stories as a durable research collection.
- Archive stories out of Latest, Focus, and Unread views.
- Provide a dedicated archive with restore controls.
- Persist read, saved, and archived identifiers in the local server's data
  directory, with browser storage as a migration and failure fallback.

### Local application experience

- Start silently from the installed Windows shortcut.
- Remain usable as a responsive browser application and installable PWA.
- Require no containers, third-party Python packages, accounts, or API keys.

## Relevance model

The current ranking is deterministic and rule-based:

`priority = technical base + tool matches + depth signals + source confidence + corroboration - promotional noise`

Scores are a sorting aid, not a claim about objective article quality. Ranking
rules must remain testable and easy to adjust as the user's interests change.

## Core user journey

1. Open CG Signal from the Start menu.
2. Review the Focus Inbox or open the Daily Brief.
3. Open valuable stories on the original site.
4. Save evergreen learning material, archive low-priority items, and mark the
   reviewed briefing read.
5. Use Latest or subject filters when deeper exploration is desired.

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

- The daily high-value queue can normally be reviewed in under ten minutes.
- Duplicate announcements rarely require opening more than one card.
- Focus results visibly favor the configured tools and practical techniques.
- Saved material remains retrievable after browser restarts.
- Feed failures do not prevent access to the last successful briefing.
- The dashboard remains useful when all optional intelligence features are off.

## Quality requirements

- Classification, relevance, state normalization, feed parsing, thumbnail
  discovery, and deduplication behavior must have automated regression tests.
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
