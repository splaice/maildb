# Life Chronicle - Product and Interface Specification

**Version:** 1.0  
**Date:** 13 July 2026  
**Primary experience:** Life Chronicle  
**Secondary capabilities:** Research Desk, Topic Atlas, People, Files, Message Reader, Workspaces, Data Health

> The root and default experience is a time-first analyst workstation. All secondary lenses share the same working set and evidence context.


# Document control

**Table 1. Document metadata and normative status**

| Field | Specification |
| --- | --- |
| Document | Life Chronicle - Product and Interface Specification |
| Version | 1.0 - implementation baseline |
| Date | 13 July 2026 |
| Primary experience | Life Chronicle: a time-first analyst workstation |
| Secondary capabilities | Research Desk, Topic Atlas, People and Organizations, Files and Attachments, Message and Thread Reader, Workspaces, Data Health, Settings |
| Target deployment | Private, authenticated, desktop-first web application over PostgreSQL, pgvector, and an attachment file store |
| Intended readers | Product design agent, frontend agent, backend agent, data/ML agent, security reviewer, and archive owner |
| Normative language | MUST is required; SHOULD is expected unless a documented constraint prevents it; MAY is optional. |

> **Implementation baseline:** This specification resolves the primary design direction: the application opens into Life Chronicle. All other interfaces are secondary analysis tools that inherit the same working set, selection, and evidence context.


---


## Contents

**Table 2. Specification map**

| Section | Title |
| --- | --- |
| 1 | Product definition and success outcomes |
| 2 | Experience hierarchy and information architecture |
| 3 | Global analyst workstation shell |
| 4 | Life Chronicle - default experience |
| 5 | Research Desk - search and grounded analysis |
| 6 | Topic Atlas - semantic and hierarchical exploration |
| 7 | People and Organizations |
| 8 | Files and Attachments |
| 9 | Message and Thread Reader |
| 10 | Workspaces and case files |
| 11 | Data Health, settings, and administration |
| 12 | AI behavior, provenance, and trust |
| 13 | Visual and interaction design system |
| 14 | Accessibility, responsive behavior, and keyboard control |
| 15 | Privacy and security requirements |
| 16 | Performance and scale requirements |
| 17 | Reference architecture and API contracts |
| 18 | Functional requirements register |
| 19 | Acceptance workflows and definition of done |
| 20 | Delivery phases and agent implementation brief |
| Appendix | Object definitions and query examples |


---


# 1. Product definition and success outcomes

Life Chronicle is a private research environment for a lifelong email and attachment archive. It is not an inbox and it is not a generic chatbot. It presents the archive as an evidence-backed chronology that can be inspected, filtered, reconstructed, and converted into durable analyst work products.


## 1.1 Product mission

Enable the archive owner to understand what happened, when it happened, who was involved, what evidence supports the interpretation, and how themes evolved across decades. The default interaction model is chronological exploration. Search, semantic mapping, person analysis, file browsing, and AI-assisted research remain immediately available as secondary tools.


## 1.2 Primary user outcomes

**Table 3. Outcome-based product definition**

| Outcome | User evidence of success |
| --- | --- |
| Reconstruct a period | The user can select a date range, see activity and events at the correct level of detail, and open every underlying source without losing the period context. |
| Resolve a factual question | The user can ask or search, receive a grounded answer, inspect citations, and identify contradiction or missing evidence. |
| Explore a theme | The user can move from a timeline burst to topics, people, files, and related periods while the same scope persists. |
| Build a case file | The user can pin sources, notes, answers, events, and timelines to a workspace and export a provenance manifest. |
| Trust the archive | The user can distinguish immutable source data from derived objects, see extraction failures, and understand how an answer or event was generated. |


## 1.3 Product principles

**Table 4. Experience principles**

| Principle | Required interpretation |
| --- | --- |
| Time first | The initial route and home state are the Life Chronicle. Time is the primary coordinate system for orientation and discovery. |
| Source first | Every answer, topic, event, relationship, and summary must lead back to original messages or attachments. |
| One archive, many lenses | Chronicle, Research Desk, Topic Atlas, People, Files, and Workspaces are views of a shared working set, not disconnected applications. |
| Progressive disclosure | The default interface is usable without query syntax or retrieval controls, but precise controls are available to advanced users. |
| Analyst agency | The system proposes topics, events, and interpretations; the user can confirm, edit, merge, split, dismiss, or exclude them. |
| Professional density | The interface favors compact tables, aligned metadata, keyboard operation, deterministic state, and evidence inspection over decorative cards or animation. |
| Private by default | External model use, remote content loading, exports, and destructive operations are explicit and auditable. |


## 1.4 Explicit non-goals

- Sending, receiving, or managing live email accounts.
- Presenting an automatic autobiography as authoritative fact.
- Using communication frequency as a measure of emotional closeness or importance.
- Rendering a global social or semantic graph as the primary navigation model.
- Hiding retrieval, citations, extraction failures, model policy, or source provenance behind an opaque assistant.
- Overwriting original messages, attachments, headers, or extracted source records.


## 1.5 Initial archive assumptions

The system may adapt to existing table names, but the integration layer should assume that messages, thread relationships, attachment metadata, extracted text, and embeddings already exist in PostgreSQL and pgvector. Original attachment binaries or safe previews are available through a file store. Derived topics, identities, and events may be absent and should be created as versioned application data.

> **Adapter rule:** Do not force a destructive migration of the existing archive. Build a data adapter or database views that map current schema objects into the domain contracts in Section 17.


---


# 2. Experience hierarchy and information architecture


## 2.1 Experience priority

**Table 5. Application capability hierarchy**

| Priority | Experience | Role |
| --- | --- | --- |
| Primary | Life Chronicle | Default route, home experience, archive orientation, period reconstruction, event review, and cross-lens navigation. |
| Secondary | Research Desk | Precise search, natural-language questions, hybrid retrieval, result review, and evidence comparison. |
| Secondary | Topic Atlas | Topic hierarchy, semantic projection, topic river, matrix comparison, and topic curation. |
| Secondary | People and Organizations | Identity resolution, activity history, shared topics, co-participants, and filtered relationship graphs. |
| Secondary | Files and Attachments | Attachment discovery, native preview, extracted-text review, duplicates, and version families. |
| Secondary | Workspaces | Persistent case files containing live scope, pinned evidence, analyst notes, answers, and exports. |
| Supporting | Message and Thread Reader | Authoritative source reading and thread reconstruction. |
| Supporting | Data Health and Settings | Ingestion quality, extraction and embedding status, model policy, privacy, and system configuration. |


## 2.2 Route map

**Table 6. Stable route and state contract**

| Route | Screen | State behavior |
| --- | --- | --- |
| / or /chronicle | Life Chronicle | Default. Encodes date viewport, zoom, lanes, grouping, filters, selection, and compare state. |
| /research | Research Desk | Inherits working set. Encodes query, retrieval mode, grouping, sort, cursor, and selected evidence. |
| /topics | Topic Atlas | Inherits working set. Encodes topic view, projection version, selected topics, map viewport, and comparison. |
| /people | People and Organizations | Inherits working set. Encodes selected identity, aliases, date range, ego-graph depth, and edge filters. |
| /files | Files and Attachments | Inherits working set. Encodes file filters, preview state, duplicate grouping, and selected version family. |
| /workspaces/:id | Workspace | Restores saved scope, notebook or board layout, pinned objects, and workspace-specific conversation. |
| /source/:id | Message or attachment | Deep-linkable authoritative source view. Returns to previous lens with state preserved. |
| /data-health | Data Health | System status, jobs, failures, counts, and archive coverage. |
| /settings | Settings | Appearance, model routing, privacy, export, keyboard, and archive preferences. |


## 2.3 Shared working set

A working set is the current archive scope. It is the invariant that connects every interface. The working set may be the full archive, a query, a date range, a workspace, selected people, selected topics, selected files, selected mailboxes, or any intersection of those constraints.

> **Non-negotiable interaction rule:** Switching among Chronicle, Research Desk, Topic Atlas, People, and Files MUST preserve the working set. A user should never have to recreate filters after changing lenses.

![Figure 1: The state and trust model separates immutable sources, versioned derived objects, analyst corrections, and transient session state.](wireframes/08_state_trust_model.png)

*Figure 1. The state and trust model separates immutable sources, versioned derived objects, analyst corrections, and transient session state.*


---


## 2.4 Object classes

**Table 7. Archive object and mutability model**

| Class | Examples | Mutation policy |
| --- | --- | --- |
| Source | Message, thread membership, attachment, original headers, extracted passage | Immutable. Corrections create annotations or replacement extraction versions; they never overwrite the original. |
| Derived | Embedding, automatic topic, entity match, inferred event, generated summary | Versioned. Reproducible where practical. Always labeled with origin, version, and evidence. |
| Analyst | Manual topic, identity correction, note, confirmed event, pinned conclusion | Editable and attributed. Takes precedence over automatic labels without deleting prior derivations. |
| Session | Query, viewport, selected sources, open inspector, sort order | Serializable to URL and optionally saved into a workspace or saved view. |


---


# 3. Global analyst workstation shell

Every primary and secondary interface uses the same workstation shell. The shell should feel like professional research software: compact, stable, predictable, keyboard-accessible, and optimized for sustained use on large desktop displays.


## 3.1 Desktop layout

**Table 8. Global workstation geometry**

| Zone | Default size at 1440 px | Behavior |
| --- | --- | --- |
| Top command bar | 56 px height | Universal Search/Ask input, mode, archive selector, command palette, and user/security status. |
| Scope bar | 44 px height | Editable filter chips, result count, working-set name, save-state action, and reset control. |
| Primary navigation | 56 px collapsed or 216-240 px expanded | Chronicle is first and active by default. Expansion state persists per device. |
| Configuration rail | 220-280 px when used | Lens-specific controls and facet groups. Resizable and collapsible. |
| Analysis canvas | Flexible; minimum 640 px | Timeline, results, map, tables, or notebook. Owns primary scroll and zoom. |
| Evidence inspector | 340-420 px | Persistent source, event, topic, person, or file inspection. Resizable and detachable on large screens. |
| Status strip | 22-26 px optional | Query status, job state, selected count, model-routing status, and keyboard hints. |


## 3.2 Universal command bar

- One input supports Search, Ask, and Explore modes. The active mode is visible before execution.
- Autocomplete covers people, aliases, organizations, topics, mailboxes, filenames, date phrases, and structured search operators.
- Natural-language constraints become visible and editable chips after interpretation.
- The command palette opens with Ctrl/Cmd+K and exposes routes, saved views, people, topics, dates, workspaces, settings, and context-specific actions.
- The archive selector indicates whether the scope is all mailboxes, a subset, a workspace, or selected evidence.
- Any external model action displays the configured model route and privacy policy before private content is transmitted.


## 3.3 Scope bar

The scope bar is a compact, horizontal expression of the working set. Each token must identify its category, value, inclusion or exclusion state, and removal control. Tokens are keyboard navigable and can be reordered only when order has semantic meaning.

**Table 9. Working-set token behavior**

| Token type | Example | Required behavior |
| --- | --- | --- |
| Date | Date: 2014-2018 | Click opens a date editor; drag from Chronicle updates it; removing restores the prior broader range. |
| Person | Participant: Alice Chen | Resolves aliases; opening the token shows included addresses and identity confidence. |
| Topic | Topic: House renovation | Shows whether automatic, curated, or workspace-defined. Can include or exclude descendants. |
| Source | Source: Messages + PDFs | Supports message, thread, attachment, attachment text, and generated-event scopes. |
| Mailbox | Mailbox: Personal | Shows account coverage and whether hidden/deleted folders are included. |
| Exclusion | Exclude: Newsletters | Visually distinct from inclusive tokens and always stated in exported manifests. |


## 3.4 Evidence inspector

The right inspector is the consistent place to understand the selected object. It opens without replacing the current view and can be pinned, resized, or temporarily hidden. Selecting a citation, event, result, topic, person, graph edge, file, or timeline mark updates the inspector.

- Show object type, title, date, people, mailbox, thread, topic, and source status.
- Show the precise matching or supporting passage with surrounding context.
- Expose why the object is present: exact match, semantic match, topic membership, inferred relationship, or event evidence.
- Provide Open full source, Pin, Annotate, Exclude, Compare, and Copy reference actions as appropriate.
- Preserve the inspector selection while the user pans, filters, or changes secondary lenses, unless the selected object leaves the working set.


## 3.5 Selection and multi-selection

Single selection drives preview. Multi-selection creates an explicit selected set that can be summarized, compared, pinned, exported, or opened as a temporary working set. The interface must distinguish selected loaded items from Select all matching, which operates on the server-side query.


## 3.6 Saved state

**Table 10. Persistence model**

| Saved object | Contents | Use |
| --- | --- | --- |
| Saved view | Route, working set, view configuration, sort/grouping, and optional selected object | Return to an analytical lens without duplicating its sources. |
| Workspace | Saved live query plus pinned evidence, notes, generated answers, and curated events | Long-running investigation or case file. |
| Shareable deep link | Serializable non-secret UI state and stable object IDs | Bookmark or open the same state on another authenticated device. |


---


# 4. Life Chronicle - default experience

Life Chronicle is the default route, home screen, and primary orientation model. It renders the archive as a zoomable, evidence-backed chronology. The experience supports broad decade-scale orientation, fine-grained source review, event reconstruction, comparative analysis, and transitions into every secondary tool.

![Figure 2: Default Life Chronicle workstation with configuration rail, multi-lane timeline, density navigator, and evidence inspector.](wireframes/01_default_chronicle.png)

*Figure 2. Default Life Chronicle workstation with configuration rail, multi-lane timeline, density navigator, and evidence inspector.*


## 4.1 Default entry state

**Table 11. Chronicle entry behavior**

| Condition | Required initial state |
| --- | --- |
| First launch with indexed data | Open /chronicle to the full indexed date range. Use Year aggregation. Show Topic activity, Inferred events, Messages, Attachments, and People lanes. Open no inspector until a mark is selected. |
| Returning user | Restore the most recent Chronicle viewport and lane configuration unless the user explicitly chose Always open full archive. |
| Workspace launch | Open Chronicle scoped to the workspace live query and pinned date range. Display workspace name in the scope bar. |
| Deep link | Restore exactly the encoded working set, viewport, zoom, grouping, lane configuration, and selected object. |
| Archive still indexing | Show available coverage and a visible partial-index banner. Do not block exploration of completed periods. |


## 4.2 Chronicle screen anatomy

**Table 12. Chronicle layout contract**

| Region | Contents | Primary actions |
| --- | --- | --- |
| Configuration rail | View mode, lane grouping, visible lanes, event filters, saved lenses, compare controls | Toggle, reorder, resize, save lens, reset. |
| Chronicle toolbar | Visible period, zoom unit, fit, today, focus mode, compare mode, export snapshot | Change aggregation, jump, fit, enter focus, compare. |
| Time axis | Calendar units appropriate to zoom and archive locale | Pan, wheel/trackpad zoom, drag selection, jump to date. |
| Lanes | Topics, inferred events, source events, messages, files, people, organizations, notes | Hover, select, brush, group, reorder, collapse. |
| Density navigator | Full-scope minimap with active viewport | Drag viewport, resize range, jump to burst, fit all. |
| Evidence inspector | Selected mark or event plus source chain | Open, pin, annotate, confirm, dismiss, compare. |


## 4.3 Time navigation and zoom

Zoom changes aggregation rather than simply scaling marks. The system chooses an appropriate resolution based on viewport duration, pixel width, density, and server-side bucket availability. A user may override the suggested aggregation when a valid bucket exists.

![Figure 3: Chronicle level-of-detail behavior from decade-scale activity to individual source chronology.](wireframes/02_chronicle_zoom.png)

*Figure 3. Chronicle level-of-detail behavior from decade-scale activity to individual source chronology.*

**Table 13. Chronicle level-of-detail rules**

| Viewport | Default aggregation | Visible detail |
| --- | --- | --- |
| 10-100 years | Year or quarter | Topic bands, major events, message/file density, long-lived people and organizations. |
| 2-10 years | Quarter or month | Topic waves, event clusters, thread bursts, attachment categories, participant spans. |
| 2-24 months | Month or week | Named event markers, thread clusters, file groups, topic changes, person activity. |
| 1-8 weeks | Week or day | Individual threads, file events, inferred decisions, meetings, deadlines, and source clusters. |
| 1-7 days | Day or hour | Individual messages, attachments, analyst notes, event evidence, and precise timestamps. |
| Under 24 hours | Hour or minute when useful | Source-level chronology. No artificial precision for date-only or uncertain records. |


### Navigation controls

- Mouse wheel or trackpad pans vertically only when a lane has internal scroll; otherwise it pans time horizontally. Ctrl/Cmd+wheel or pinch zooms around the pointer.
- Drag on the empty axis to brush a date range. A brushed range may replace the scope date or become a comparison period.
- Shift+drag adds a second range for compare mode. Escape clears the most recent transient range.
- Double-click an activity burst to zoom to its natural extent. Double-click an individual mark to open Focus mode.
- The density navigator always shows the full working-set extent and the current viewport. It remains operable during loading.
- Zoom, pan, and range changes update the URL using debounced replace-state; completed analytical actions create history entries so browser Back remains meaningful.


## 4.4 Lane model

A lane is a time-aligned analytical row. Lanes may render continuous activity, discrete events, clustered items, spans, or counts. Lanes are resizable, reorderable, collapsible, and saved as part of the view configuration.

**Table 14. Default and optional Chronicle lanes**

| Lane | Default rendering | Interaction |
| --- | --- | --- |
| Topic activity | Stacked or separated normalized bands; top topics selected by working-set contribution | Select topic, isolate band, compare two topics, open Topic Atlas. |
| Inferred events | Diamonds or labeled clusters with origin and evidence-strength state | Open event reconstruction, confirm, edit, dismiss, pin. |
| Source events | Calendar invitations, explicit dates, contracts, invoices, travel dates, or other directly dated sources | Open authoritative source; distinguish exact from approximate dates. |
| Messages | Density histogram at broad zoom; thread clusters at medium zoom; individual message marks at close zoom | Select burst, open thread list, open message, create temporary scope. |
| Attachments | Counts by type at broad zoom; file groups at medium zoom; individual files at close zoom | Open file preview, version group, or source message. |
| People | Activity spans or selected identity rows; no claim of emotional importance | Open person profile, filter person, compare people. |
| Organizations | Association and communication spans | Open organization profile or filter. |
| Analyst notes | User-authored dated notes and confirmed events | Edit, pin, export, link to evidence. |


### Lane configuration

- The default lane order is Topics, Inferred events, Messages, Attachments, People.
- Each lane has a context menu for hide, isolate, group, normalize, change scale, open as list, and save lens.
- A global lane control can group by source type, topic, person, mailbox, organization, or workspace tag.
- A lane with more groups than can be rendered must aggregate and provide an Open as table action; it must not silently drop groups.
- The user can lock vertical lane sizes while changing time zoom.


## 4.5 Event model and reconstruction

Chronicle events can be direct source events, imported calendar events, analyst-authored events, or model-inferred events. The visual language and inspector must make origin unmistakable. Inferred events are useful analytical hypotheses, not source facts.

![Figure 4: Event reconstruction screen with an analyst conclusion, claim-to-evidence matrix, and chronological evidence chain.](wireframes/03_event_reconstruction.png)

*Figure 4. Event reconstruction screen with an analyst conclusion, claim-to-evidence matrix, and chronological evidence chain.*

**Table 15. Chronicle event schema**

| Event field | Requirement |
| --- | --- |
| Title | Concise and editable. Automatic titles retain prior versions in the event history. |
| Time | Start, optional end, timezone, precision, and uncertainty. Date-only records must not be rendered at a fabricated hour. |
| Origin | Source, imported, automatic, or analyst. Displayed in every event representation. |
| Type | Decision, meeting, travel, purchase, deadline, transition, document, communication burst, or user-defined. |
| Evidence strength | Based on number, quality, agreement, and directness of supporting sources. Avoid a false percentage unless calibrated. |
| Status | Unreviewed, confirmed, edited, dismissed, superseded, or unresolved. |
| Claims | Atomic claims with direct, supported, conflicting, or unresolved status. |
| Evidence | Ordered citations to messages, attachment passages, or other source objects. |
| Derivation | Generation date, model/process version, prompt policy version, and input scope fingerprint. |


### Event inspector behavior

- Selecting an event opens the summary, origin, time, participants, topics, status, and a compact evidence list.
- Open reconstruction expands into a claim-to-evidence view. Every claim can be selected to highlight supporting and conflicting sources.
- Confirm changes status without converting the event into a source fact. Edit creates an analyst revision while preserving the generated version.
- Dismiss hides the event from default views but retains it in a reviewable dismissed list. Regenerate creates a new version and never replaces an analyst edit.
- An event with contradictory evidence displays a conflict state and both source chains.


## 4.6 Focus mode

Focus mode turns a selected period, event, burst, topic band, person span, or source cluster into a bounded analytical workspace. The left rail becomes a local chronology; the center shows a narrative or source sequence; the right inspector remains evidence-oriented.

- Entry: double-click a mark, choose Focus period, or press Enter on a selected range.
- Scope: may be temporary or may replace the global working set after explicit confirmation.
- Views: chronological source list, event reconstruction, thread clusters, attachment groups, and generated period summary.
- Exit: returns to the previous viewport and selection exactly.
- Save: can create a saved view or workspace from the focused period.


## 4.7 Compare mode

Compare mode supports two date ranges, two topics, two people, or two workspace scopes. It uses aligned time axes where possible and small multiples when alignment would mislead.

**Table 16. Chronicle comparison modes**

| Comparison | Presentation |
| --- | --- |
| Two periods of equal duration | Aligned lanes with normalized or absolute counts selectable by the user. |
| Two periods of different duration | Small multiples with explicit durations; no forced one-to-one alignment. |
| Two topics | Activity bands, key people, source counts, attachments, and event summaries for each topic. |
| Two people | Communication activity, shared topics, co-participants, files, and overlapping periods. Avoid relationship-quality labels. |
| Before/after event | Fixed event anchor with pre-event and post-event windows. |


## 4.8 Chronicle search and Ask behavior

Search performed from Chronicle highlights and filters timeline activity rather than immediately replacing the timeline with a result list. The user can choose Spotlight, Filter, or Open in Research Desk.

**Table 17. Chronicle query actions**

| Action | Behavior |
| --- | --- |
| Spotlight | Keep the current working set and dim nonmatching marks. Useful for orientation. |
| Filter | Apply the interpreted constraints to the working set and recompute lanes. |
| Ask | Answer from the current visible scope by default; citations open in the inspector and matching periods are highlighted. |
| Open in Research Desk | Transfer the exact working set and query to the ranked result interface. |


## 4.9 Chronicle empty, loading, and degraded states

**Table 18. Chronicle state handling**

| State | Required response |
| --- | --- |
| No results in range | Show the selected range, active constraints, and nearest activity outside the range. Offer Remove filter, Expand range, and Open Data Health. |
| Partial bucket load | Render completed lanes, show lane-level skeletons, and retain pan/zoom. Never block the entire canvas. |
| Very dense period | Aggregate into clusters and show a density warning with Open as list. Do not attempt to draw every source. |
| Missing extracted text | Keep message/file metadata visible and show extraction status in the inspector. |
| Uncertain date | Render an interval or approximate marker and label the date precision. |
| Model unavailable | Hide or mark generated-event refresh controls; existing derived objects remain viewable. Source lanes continue to work. |
| Timezone ambiguity | Use archive display timezone while exposing original timestamp and timezone in source details. |


## 4.10 Chronicle acceptance criteria

1. Opening the application lands on Chronicle and shows the full available archive or the user-saved return state.
2. A user can zoom from decades to individual sources without losing working-set filters or selected evidence.
3. Changing a date range updates every visible lane and the source count consistently.
4. Selecting any generated event reveals origin, evidence, derivation version, and correction controls.
5. Opening a source from Chronicle and returning restores the exact time viewport and selection.
6. The user can brush a period, open it in Research Desk or Topic Atlas, and retain the same scope.
7. Dense periods aggregate safely and always provide an authoritative source list.
8. Chronicle remains usable with AI services disabled.


---


# 5. Research Desk - search and grounded analysis

Research Desk is the secondary precision interface. It combines structured filters, hybrid retrieval, grounded answers, ranked sources, and an evidence inspector. It is entered from the primary navigation or from any Chronicle selection.

![Figure 5: Research Desk with structured scope controls, grounded answer, evidence results, and source preview.](wireframes/04_research_desk.png)

*Figure 5. Research Desk with structured scope controls, grounded answer, evidence results, and source preview.*


## 5.1 Search modes

**Table 19. Retrieval modes**

| Mode | Purpose | Ranking inputs |
| --- | --- | --- |
| Hybrid - default | General archive retrieval when exact wording is uncertain | Full-text relevance, embedding similarity, metadata constraints, topic/entity matches, thread deduplication, exact-match boosts. |
| Exact | Known phrases, names, identifiers, dates, filenames, and headers | Phrase and token matches, metadata equality/ranges, field-specific boosts. Semantic expansion disabled unless explicitly added. |
| Semantic | Conceptual discovery when vocabulary is unknown | Embedding similarity plus metadata filters and duplicate suppression. Show semantic reason in details. |


## 5.2 Query interpretation

Natural-language constraints must become visible structured tokens before or immediately after execution. The user can correct an entity, date, file type, mailbox, inclusion, or exclusion without rewriting the whole query.

```text
User query:
  PDFs Alice sent about the renovation from 2014 through 2018

Interpreted scope:
  participant = person:alice-chen
  source_type = attachment
  mime_family = pdf
  topic = topic:house-renovation
  date.from = 2014-01-01
  date.to = 2018-12-31
```


## 5.3 Structured syntax

```text
from:  to:  cc:  participant:  subject:
after:  before:  on:  mailbox:  domain:
topic:  person:  organization:  filetype:  filename:
has:attachment  has:failed-extraction
is:thread  is:attachment  is:message

Examples:
from:alice filetype:pdf after:2015-01-01
subject:"final estimate"
participant:bob -topic:newsletter
has:attachment filename:invoice
```


## 5.4 Result presentation

**Table 20. Research Desk result cards**

| Result type | Required fields |
| --- | --- |
| Message | Type, subject, sender, recipients, date/time, mailbox, relevant passage, thread count, attachment badges, topic chips, match explanation. |
| Thread | Subject, date span, participants, message count, attachment count, representative passage, summary status, topic chips. |
| Attachment | Filename, type, source message, sender, date, page/sheet when known, matching passage, extraction status, version-family indicator. |
| Person or organization | Preferred name, aliases, time span, matched context, source count, identity status. |
| Topic | Label, origin, source counts, date range, representative sources, related topics. |
| Inferred event | Title, time, origin, status, evidence strength, supporting source count. Never mixed with source results without a type label. |


## 5.5 Grounded answer block

- Appears above results only in Ask mode. Results remain visible and independently sortable.
- Streams text and citations. A citation is actionable as soon as its source is available.
- States the number and types of retrieved sources and whether attachment text was included.
- Separates direct evidence, supported inference, unresolved claims, and contradictions.
- Provides Show retrieval set, Search only these sources, Pin answer, Copy with citations, and Report unsupported action controls.
- When no reliable answer exists, explains whether the failure is no matches, incomplete extraction, indirect evidence, or conflicting sources.


## 5.6 Ranking and grouping

- Do not apply a generic recency boost to historical archive queries. Date relevance comes from the query and working set.
- Suppress exact duplicate messages and repeated quoted bodies by default, while preserving an expansion control.
- Allow grouping by thread, person, topic, month/year, mailbox, organization, attachment type, or version family.
- Expose Why this matched behind a disclosure with exact, semantic, metadata, topic, entity, and thread contributions.
- Support Select all matching as a server-side selection token rather than loading all rows in the browser.


## 5.7 Research Desk acceptance criteria

1. One query searches message bodies and attachment text while clearly labeling the hit source.
2. Natural-language constraints are visible and editable as structured filters.
3. Every answer citation opens the supporting passage and the complete original source.
4. Contradictory claims are shown side by side with dates and citations.
5. Hybrid, Exact, and Semantic modes produce visibly distinct behavior and can be changed without losing filters.
6. Research Desk works as a non-AI search interface when model services are disabled.


---


# 6. Topic Atlas - semantic and hierarchical exploration

Topic Atlas is an exploratory secondary lens. It provides a reliable hierarchy first, then a semantic projection, time river, and comparison matrix. It must never imply that a two-dimensional embedding projection is an objective map of meaning.

![Figure 6: Topic Atlas with a curated hierarchy, semantic projection, and definitive topic inspector.](wireframes/05_topic_atlas.png)

*Figure 6. Topic Atlas with a curated hierarchy, semantic projection, and definitive topic inspector.*


## 6.1 Topic types

**Table 21. Topic origin and curation policy**

| Type | Origin | User control |
| --- | --- | --- |
| Automatic cluster | Clustering or classification over embeddings and metadata | Rename, describe, hide, merge, split, regenerate, include/exclude sources. |
| Curated topic | Automatic topic modified and confirmed by the user | Manual changes take precedence; automated refresh produces suggestions rather than overwrites. |
| Manual topic | Created from a query or source selection | Fully user controlled; may have live rules, pinned members, or both. |
| Workspace collection | Sources or topics used inside a workspace | Scoped to the workspace; may be promoted to a manual archive topic. |


## 6.2 Topic views

**Table 22. Topic Atlas analytical views**

| View | Role | Required safeguards |
| --- | --- | --- |
| Hierarchy - default | Deterministic browsing of topics and subtopics | Shows origin, counts, hidden state, and manual overrides. Supports search and keyboard tree navigation. |
| Semantic projection | Discover adjacent clusters and outliers | No invented axis labels. Level-of-detail aggregation. Every selection can open the definitive source list. |
| Topic river | Show topic activity over time | Use absolute or normalized mode with an explicit legend. Selection transfers to Chronicle. |
| Matrix | Compare topic by person, year, organization, or file type | Sort, normalize, show values, and provide accessible table alternative. |


## 6.3 Semantic projection interactions

- Far zoom shows major topics; medium zoom shows subtopics; near zoom shows thread or source clusters; individual sources appear only at the closest useful level.
- Hover shows label, origin, counts, date span, and representative sources. Click selects. Double-click zooms. Shift-click compares.
- Lasso creates a temporary working set and offers Open in Chronicle, Open as results, Create topic, and Create workspace.
- Search spotlight dims unrelated clusters without mutating the working set until the user chooses Apply filter.
- A visible note states that the projection is exploratory and may change when embeddings or clustering versions change.


## 6.4 Topic inspector and detail page

- Overview: label, description, origin, version, date range, source counts, and curation status.
- Activity: time series linked to Chronicle.
- Representative sources: chosen using diversity and centrality, not only highest similarity.
- Subtopics, key people, organizations, attachments, inferred events, related topics, notes, and topic history.
- Maintenance: rename, merge, split, move source, hide, regenerate label, convert to curated, and revert automatic version.


## 6.5 Topic acceptance criteria

1. Hierarchy is the default Topic Atlas view and remains usable without embeddings.
2. Selecting a topic updates the inspector and can open its exact sources in Research Desk.
3. The semantic map aggregates at scale and never attempts to draw the entire archive at once.
4. Manual topic corrections are preserved across automatic regeneration.
5. Every visual view has a list or table equivalent.


---


# 7. People and Organizations

People and Organizations provides identity resolution and historical communication context. It supports investigation without making unsupported social or emotional judgments.

![Figure 7: Secondary analysis tools: person profile, attachment browser, and workspace notebook.](wireframes/06_secondary_tools.png)

*Figure 7. Secondary analysis tools: person profile, attachment browser, and workspace notebook.*


## 7.1 Person profile

**Table 23. Person profile information architecture**

| Section | Contents |
| --- | --- |
| Identity | Preferred name, known addresses, aliases, role addresses, identity status, analyst notes. |
| Archive span | First and last interaction, active periods, message/thread counts, attachment counts. |
| Activity | Communication volume over time linked to Chronicle; absolute and normalized modes. |
| Topics | Common topics, changes over time, and topic-specific source counts. |
| Network | Frequent co-participants and organizations with evidence-backed edge reasons. |
| Sources | Threads, messages, files, events, and workspaces involving the identity. |


## 7.2 Identity resolution

- Mark the archive owner and all of the owner's addresses. Distinguish personal, work, shared, and role-based addresses.
- Merge duplicate identities, split incorrect merges, choose preferred display name, assign organization, and record rationale.
- Automatic identity suggestions show confidence and evidence such as display-name consistency, signature, domain, and thread co-occurrence.
- Manual merge/split decisions override automatic resolution and are versioned.


## 7.3 Relationship graph

The default graph is a filtered ego network, not a global archive graph. Nodes may be people, organizations, or optionally topics. Edges may represent direct exchange, thread co-participation, shared topic involvement, shared attachments, or organizational association. Selecting an edge opens the exact threads and counts that created it.

> **Interpretation guardrail:** Do not label edges as close, important, positive, negative, or intimate based only on message volume or network structure.


## 7.4 Organization profile

- Organization name, domains, aliases, active date span, known people, and organization notes.
- Topic and project activity over time with links to Chronicle.
- Messages, threads, attachments, events, and workspaces associated with the organization.
- Controls to merge domains, separate unrelated organizations, or mark service/newsletter senders.


---


# 8. Files and Attachments

Files and Attachments treats every attachment as a first-class archive object while retaining the source-message relationship. It supports known-file retrieval, vague-memory retrieval, preview, extraction review, duplicate grouping, and version comparison.


## 8.1 Browser views

**Table 24. Attachment browser views**

| View | Purpose | Required fields |
| --- | --- | --- |
| Table - default | Dense sorting and filtering | Filename, type, size, date, sender, source subject, page/sheet count, extraction state, duplicate/version state. |
| Compact list | Fast scanning with relevant passage | Same metadata plus one extracted-text hit. |
| Gallery | Images and visually distinctive documents | Safe thumbnail, filename, source, date, type, and extraction state. |
| Duplicate groups | Exact duplicate and probable version-family review | Hash status, filename relation, text similarity, dates, sources, and compare action. |


## 8.2 Preview behavior

- Use native safe preview for PDF, image, plain text, and supported office formats. Never execute macros or active content.
- Display preview and extracted text side by side when space permits. Search hits synchronize across preview page/sheet and extracted text.
- PDF citations include page. Spreadsheet citations include sheet and cell/range when the extractor provides them. Other documents include section or page when available.
- Always show the source message, sender, date, thread, attachment hash, extraction version, and download-original action.
- When preview is unavailable, retain metadata, extracted text, and a controlled download action.


## 8.3 Duplicate and version policy

**Table 25. Attachment grouping semantics**

| State | Meaning |
| --- | --- |
| Exact duplicate | Identical content hash. Group by default while preserving every source-message occurrence. |
| Same filename, different content | Potential revision or unrelated reuse. Never collapse automatically. |
| Probable version family | Filename, sender/thread, date, and text similarity suggest versions. Present comparison and confidence. |
| Forwarded/requoted occurrence | Same attachment appears through forwarding. Preserve provenance chain. |


## 8.4 Extraction failures

A failed extraction must not make a file disappear. It remains discoverable by filename, type, date, source message, people, and available metadata. The UI shows failure reason, last attempt, extractor version, retry status, and Open in Data Health.


---


# 9. Message and Thread Reader

The Message and Thread Reader is the authoritative reading interface. It prioritizes safe rendering, clear chronology, quoted-text control, source metadata, attachment relationships, and retrieval highlights.


## 9.1 Thread header

- Subject, date span, participants, message count, attachment count, mailboxes, topic chips, and thread-quality status.
- Actions: Pin, Open in Chronicle, Summarize, Search within thread, Export, View raw headers, and report threading error.
- Optional generated overview: short summary, decisions, open questions, referenced dates, participant changes, and attachments. Every statement is cited.


## 9.2 Message rendering

**Table 26. Message reader behavior**

| Element | Behavior |
| --- | --- |
| Envelope | Sender, recipients, cc/bcc when available, exact timestamp, timezone, account/mailbox, and stable source ID. |
| Body | Sanitized HTML or plain text. Retrieval hits highlighted. Link targets visible before opening. |
| Quoted text | Collapsed by default with count and source attribution when known. Search hits inside quoted text expand automatically. |
| Signature | Collapsible and separately indexed when extraction supports it. |
| Attachments | Cards linked to preview, extraction status, duplicate group, and download. |
| Source modes | Clean reading, raw extracted text, original source, and clean/raw diff when needed. |


## 9.3 Rendering security

> **Mandatory safety controls:** Sanitize email HTML; block scripts, forms, remote styles, remote images, tracking pixels, active SVG, and embedded active content. Remote images load only after an explicit user action and preferably through a privacy-preserving proxy.


## 9.4 Thread correction

Thread reconstruction may be imperfect across decades and providers. The user can split a thread, merge related threads, mark a missing parent, or exclude quoted duplicates. Corrections create a versioned analyst threading layer and do not alter source headers.


---


# 10. Workspaces and case files

A workspace is a persistent investigation. It combines a live working set with pinned evidence, analyst notes, generated answers, confirmed events, and export history. The visual metaphor is a professional case file or research notebook.


## 10.1 Workspace contents

**Table 27. Workspace object model**

| Object | Behavior |
| --- | --- |
| Live query | Re-evaluates against the archive; shows added/removed counts since last review. |
| Pinned source | Stable reference to a message, thread, attachment, passage, person, topic, or event. |
| Snapshot set | Optional immutable list of source IDs for reproducible analysis at a point in time. |
| Note | Rich text or Markdown-like note with source citations and optional date placement in Chronicle. |
| Answer | Stored answer, retrieval set, citations, model policy, and generation timestamp. |
| Workspace conversation | Assistant constrained to workspace scope unless the user explicitly broadens it. |
| Custom timeline | Workspace-specific Chronicle configuration and confirmed events. |
| Export record | Format, time, source manifest fingerprint, redaction policy, and user identity. |


## 10.2 Layouts

**Table 28. Workspace layouts**

| Layout | Default use |
| --- | --- |
| Notebook - default | Ordered analytical document containing questions, notes, sources, answers, findings, and conclusions. Best for review and export. |
| Board | Columns or groups of sources and notes for categorization, triage, or evidence status. |
| Chronicle | Workspace-scoped timeline with custom lanes and confirmed events. |


## 10.3 Notebook block types

- Heading, paragraph, checklist, analyst finding, question, and conclusion.
- Pinned message, thread, attachment, passage, person, topic, event, Chronicle range, and result set.
- Grounded answer with citations and retrieval metadata.
- Comparison block for two periods, topics, people, files, or source sets.
- Source manifest and export note.


## 10.4 Exports

**Table 29. Workspace export contract**

| Format | Contents |
| --- | --- |
| Markdown | Notebook content, source links/IDs, citations, and manifest. |
| PDF | Fixed-layout report with citations and appendix manifest. |
| JSON | Workspace structure, queries, stable IDs, annotations, and derivation metadata. |
| CSV manifest | One row per source with type, date, sender, filename, thread ID, passage location, and hash where available. |
| ZIP evidence package | Selected originals, redacted copies when requested, manifest, and workspace export. |

> **Reproducibility:** Exports that include generated answers or events MUST include the source-set fingerprint, model/process version, generation time, and citation manifest.


---


# 11. Data Health, settings, and administration


## 11.1 Data Health dashboard

Data Health is not a vanity dashboard. It is the operational trust interface for archive coverage and processing quality.

**Table 30. Data Health scope**

| Area | Required information |
| --- | --- |
| Archive coverage | Mailboxes/accounts, date range, message count, thread count, attachment count, people count, and source gaps. |
| Threading | Unthreaded messages, probable duplicate threads, corrected threads, and provider-specific issues. |
| Extraction | Success/failure by file type, OCR status, last attempt, error reason, and retry queue. |
| Embeddings | Coverage, model/version, vector dimension, failed records, stale versions, and regeneration queue. |
| Topics/entities/events | Generation version, coverage, pending reviews, hidden/dismissed counts, and user corrections. |
| Jobs | Current and historical jobs, progress, throughput, failure logs, cancellation, and retry. |
| Audit | Exports, external model calls, identity/topic edits, event confirmations, and destructive actions. |


## 11.2 Settings groups

- Appearance: dark/light, compact/comfortable, typography scale, panel widths, motion, and default lane configuration.
- Chronicle: initial range behavior, archive display timezone, default aggregation, visible lanes, event visibility, and date precision rules.
- Search: default retrieval mode, quoted-text inclusion, duplicate suppression, result grouping, and facet behavior.
- AI and models: local/external routes, provider policy, model allowlist, source limits, logging, retention statement, and per-action confirmation.
- Privacy: remote image policy, export redaction defaults, session timeout, audit retention, and clipboard warnings.
- Archive: owner identities, mailboxes, excluded folders, extraction and indexing preferences.
- Keyboard: shortcut map and conflict resolution.


---


# 12. AI behavior, provenance, and trust

AI is an analyst-assistance layer, not the archive authority. The product must make scope, retrieval, source evidence, derivation, uncertainty, and correction visible. Search and source browsing remain useful when AI is disabled.


## 12.1 Assistant scope

- Every assistant action operates within a visible scope: current viewport, working set, selected sources, topic, person, file, thread, or workspace.
- The assistant states whether it searched the broader archive or only the selected evidence.
- Source-type inclusion is visible: message bodies, quoted text, attachment text, headers, topics, identities, and generated events.
- The user can lock the scope, broaden it, or exclude specific sources before regeneration.


## 12.2 Citation contract

**Table 31. Evidence citation contract**

| Citation field | Requirement |
| --- | --- |
| Stable source ID | References the immutable message or attachment object. |
| Passage location | Character offsets plus an excerpt hash for text; page/sheet/cell or section when available. |
| Context | Enough surrounding text to evaluate the claim without opening a separate screen. |
| Source metadata | Date, sender, recipients, subject/filename, thread, and mailbox where applicable. |
| Freshness check | If extraction changed, mark the citation stale and resolve against the new version before presenting it as current. |
| Action | Open precise passage, open full source, pin, compare, or report irrelevant. |


## 12.3 Answer states

**Table 32. Assistant answer status model**

| State | Presentation |
| --- | --- |
| Directly supported | Claim has one or more direct source passages. Use neutral language and citations. |
| Supported inference | Inference is clearly labeled and supporting evidence is cited. |
| Conflicting | Present competing claims with dates and citations. Do not silently choose one. |
| Incomplete | Explain missing period, source type, extraction, or indirectness. |
| No evidence | State that no reliable answer was found and show the search/retrieval set. |
| Potentially stale | Derived object or citation was created against an older extraction, embedding, topic, or model version. |


## 12.4 Generated objects

Automatic topics, summaries, entity matches, and inferred events use one consistent generated-object badge. Details expose generation date, process/model version, evidence set, input scope, user edits, and current status. A user correction must not be silently overwritten by regeneration.


## 12.5 Prompt-injection defenses

> **Untrusted archive content:** Messages and attachments may contain instructions intended for people or models. The model gateway MUST treat source text as quoted evidence, not as executable instructions. Source content cannot modify system policy, request external actions, or expand scope.

- Separate system instructions, user request, retrieval metadata, and source content using structured message boundaries.
- Strip or neutralize active markup before model submission. Preserve text for evidence but not execution.
- Allowlist tool actions and require server-side authorization independent of model output.
- Never let a source document decide which additional records, URLs, files, or external services to access.
- Log model calls with source IDs and policy version without placing full private content into general telemetry.


## 12.6 Feedback and correction

- Unsupported answer, irrelevant citation, important source omitted, topic incorrect, identity incorrect, event incorrect, and extraction incorrect are distinct feedback types.
- Feedback is attached to stable objects and can influence retrieval or curation logic where safe.
- The user can inspect and reverse corrections. No correction silently changes immutable source records.


---


# 13. Visual and interaction design system

The product should look and behave like an analyst workstation: dark graphite surfaces, restrained accents, compact information architecture, tabular alignment, minimal motion, and clear status. The visual design should evoke professional research, intelligence analysis, and data tooling rather than a consumer inbox or futuristic demo.


## 13.1 Default theme

**Table 33. Dark analyst-workstation color tokens**

| Token | Value | Use |
| --- | --- | --- |
| Graphite 950 | #0D1117 | Application canvas and deepest background. |
| Graphite 900 | #151B23 | Primary panels, rails, cards. |
| Graphite 800 | #1C2430 | Raised controls and selected containers. |
| Steel border | #344050 | Panel divisions, axes, table lines, inactive outlines. |
| Primary text | #E6EDF3 | Titles and source content. |
| Muted text | #91A0B5 | Metadata, labels, secondary status. |
| Action blue | #5AA7FF | Primary actions, selected route, message/source emphasis. |
| Event amber | #E0A84A | Chronicle events, warnings, date anchors. |
| Attachment green | #55C2A3 | Attachments, confirmed evidence, healthy status. |
| Topic purple | #A78BFA | Topics and other derived analytical structures. |
| People cyan | #56D4DD | People and organization activity. |
| Conflict red | #F07470 | Errors, contradictions, destructive actions. |

> **Accessibility rule:** Color reinforces meaning but never carries meaning alone. Every source type, event origin, confidence state, and error state also uses text, icon, shape, or pattern.


## 13.2 Typography

**Table 34. Typography system**

| Role | Recommendation |
| --- | --- |
| Interface sans | Inter or a metrically compatible system sans. Use 12-14 px default UI text and 11-12 px compact metadata. |
| Display headings | Inter Display or the same family at stronger weight. Avoid oversized consumer-style headings inside the workstation. |
| Monospace | A legible mono family for timestamps, IDs, query syntax, hashes, and tabular numerals. |
| Numerals | Use tabular numerals for timestamps, counts, axes, and aligned metadata. |
| Line length | Inspector and prose panels target 55-80 characters. Dense tables may use shorter labels and tooltips. |


## 13.3 Density and spacing

- Compact is the default desktop density. Comfortable is available in settings.
- Use a 4 px base spacing system with common steps of 4, 8, 12, 16, 24, and 32 px.
- Result rows and source tables target 36-56 px depending on content. Avoid large decorative cards for routine records.
- Panels use 1 px borders and restrained 6-10 px radii. Avoid heavy shadows; hierarchy comes from surface, border, and typography.
- Resizable panels snap to useful minimums and persist per device.


## 13.4 Icons, charts, and motion

- Use a consistent line-icon set with text labels for unfamiliar actions. Do not rely on decorative symbols alone.
- Chronicle marks use stable shapes: message circle/line, attachment square, inferred event diamond, analyst note hexagon or annotated marker.
- Charts expose exact values on focus, support keyboard navigation, and provide table alternatives.
- Motion is limited to 120-180 ms state transitions and streaming indicators. Pan/zoom should feel direct; avoid ornamental parallax or animated backgrounds.
- Reduced-motion mode removes smooth zoom and nonessential transitions.


## 13.5 Tone and microcopy

**Table 35. Professional interface voice**

| Prefer | Avoid |
| --- | --- |
| No reliable evidence found | I could not discover your memory |
| Inferred event - unreviewed | We know this happened |
| Evidence strength: high | Confidence: 98% unless calibrated |
| Open supporting sources | See why AI thinks this |
| Partial index: 73% of attachments complete | Almost there! |
| Export contains 28 sources and 4 redactions | Your story is ready |


---


# 14. Accessibility, responsive behavior, and keyboard control


## 14.1 Accessibility target

Target WCAG 2.2 AA. Accessibility is part of the component acceptance criteria, not a later audit item.

- Full keyboard access with visible focus, logical order, skip links, and no keyboard traps in canvas, timeline, map, preview, or modal controls.
- Screen-reader labels and descriptions for timeline marks, chart series, filters, origin badges, confidence/evidence states, and generated objects.
- A list or table equivalent for Chronicle selections, semantic map, topic river, matrices, and relationship graph.
- Sufficient contrast, 200% text zoom support, user-adjustable density, and no content loss when inspector or navigation is collapsed.
- Reduced-motion support and no meaning conveyed only through animation.


## 14.2 Keyboard map

**Table 36. Default keyboard controls**

| Shortcut | Action |
| --- | --- |
| / | Focus universal query. |
| Ctrl/Cmd+K | Open command palette. |
| J / K | Move to next or previous result, event, or source mark in the active list. |
| Enter | Open selected object or enter Chronicle Focus mode. |
| Space | Preview selected object in the inspector. |
| P | Pin to workspace. |
| F | Open lens filters. |
| T | Open or return to Chronicle. |
| R | Open Research Desk with current scope. |
| M | Open Topic Atlas map with current scope. |
| G | Open person/topic graph when available. |
| [ / ] | Zoom Chronicle out or in. |
| Shift+C | Start or exit compare mode. |
| Esc | Close transient UI, clear most recent selection, or exit focus mode. |
| ? | Open contextual shortcut reference. |


---


## 14.3 Responsive behavior

**Table 37. Responsive layout rules**

| Width | Behavior |
| --- | --- |
| 1600 px and above | Expanded navigation, configuration rail, canvas, and evidence inspector can all remain visible. Inspector may detach or split. |
| 1280-1599 px | Standard desktop. Collapsible navigation; configuration rail and inspector remain resizable. |
| 1024-1279 px | One side rail visible at a time. Inspector becomes an overlay or bottom panel. Chronicle stays primary. |
| 768-1023 px | Tablet reading and focused analysis. Replace global maps/graphs with list equivalents; retain Chronicle range navigation. |
| Below 768 px | Prioritize search, source reading, saved workspaces, and simplified Chronicle. Full map/graph editing is not required. |


---


# 15. Privacy and security requirements

A lifetime email archive contains highly sensitive personal, professional, financial, health, and relationship information. Privacy and security are first-order product features.


## 15.1 Authentication and authorization

- Strong authentication with passkey or hardware-key support where deployment permits.
- Configurable session timeout and reauthentication for bulk export, deletion of derived data, model-provider changes, and security settings.
- Least-privilege database and file-store credentials. Do not expose database access directly to the browser.
- Design authorization boundaries so a future multi-user mode is possible even if the first release is single-user.
- Audit exports, external model calls, identity/topic/event edits, and administrative actions.


## 15.2 Content security

- Strict Content Security Policy; no inline script exceptions in source rendering.
- Sanitize all email HTML and extracted rich content. Treat SVG and office preview output as untrusted.
- Do not automatically fetch remote images, links, fonts, styles, or tracking resources.
- Sandbox previews. Never execute macros, embedded scripts, forms, or active media.
- Use parameterized queries and server-side validation for all filter, search, and metadata operations.
- Protect against CSRF, XSS, path traversal, insecure direct object reference, and archive-ID enumeration.


## 15.3 Model and network transparency

**Table 38. External model control requirements**

| Requirement | Behavior |
| --- | --- |
| Visible route | Show local or external provider and model class before submission. |
| Explicit scope | Show the source count and types that will be sent. |
| Configurable policy | Allow model services to be disabled globally or per action. |
| Retention statement | Display configured provider retention/training policy in settings and the confirmation surface. |
| Audit | Record provider, model, policy version, source IDs, time, user, and response status. Avoid duplicating full content in general logs. |
| No silent fallback | Do not silently send content to a different provider when a configured model fails. |


## 15.4 Redaction and export

- Offer export-time detection and review for email addresses, phone numbers, street addresses, account numbers, health identifiers, and user-defined terms.
- Redacted copies never overwrite originals. The export manifest states which files and passages were altered.
- Large exports show an inventory and estimated size before creation and require reauthentication.
- ZIP evidence packages include checksums and a manifest. Optional encryption is supported for exported packages.


---


# 16. Performance and scale requirements

The browser must never attempt to load or render the full archive. All high-cardinality operations use server-side filtering, aggregation, cursor pagination, and level-of-detail representations.


## 16.1 Required implementation patterns

- Cursor-based pagination for source and file lists; virtualized rendering for visible rows.
- Server-side facet counts with progressive loading and cancellation of obsolete requests.
- Precomputed or cached time buckets for common resolutions and scopes.
- Level-of-detail aggregation for Chronicle, semantic maps, topic rivers, matrices, and relationship graphs.
- Streaming for grounded answers and long-running exports; visible cancellation and retry.
- Request IDs and abort controllers so panning, filtering, or typing cancels stale client requests.
- Background jobs for extraction, OCR, embeddings, topic/entity/event generation, previews, and exports.
- Stable query fingerprints and cache keys that include archive version, extraction version, and policy-relevant options.


## 16.2 Experience targets

**Table 39. Performance experience targets**

| Operation | Target experience |
| --- | --- |
| Open Chronicle | Shell and cached archive coverage visible under 1 second; first meaningful lanes under 2 seconds for a warm system. |
| Pan or zoom Chronicle | Immediate local feedback; updated aggregate data normally under 1.5 seconds. Preserve prior data until replacement arrives. |
| Metadata/full-text search | First results under 1-2 seconds for indexed queries. |
| Hybrid semantic search | First useful results under 2-3 seconds; additional facets may continue loading. |
| Open indexed source | Inspector preview under 300 ms from cache or under 1 second from server in normal conditions. |
| Ask | Retrieval status appears immediately; answer begins streaming within several seconds. Sources can be inspected before completion. |
| Switch lens | Preserve state immediately and reuse cached scope data; do not recompute unrelated work. |


## 16.3 Scale safeguards

- Facet counts may be approximate while loading but must be labeled and eventually reconcile.
- Map and graph endpoints enforce server-side maximum nodes and return aggregation metadata.
- Select all matching uses a query token or materialized temporary set, not millions of client IDs.
- Long-running exports and regeneration jobs survive browser navigation and are resumable from Data Health.
- The system exposes data-volume and query-cost diagnostics to administrators without exposing private content to telemetry.


---


# 17. Reference architecture and API contracts

The architecture separates immutable archive sources from derived analytical objects and user-authored corrections. A service layer adapts the existing PostgreSQL and pgvector schema to stable application contracts.

![Figure 8: Reference implementation architecture for the Chronicle, retrieval, evidence, model, curation, and export layers.](wireframes/07_architecture.png)

*Figure 8. Reference implementation architecture for the Chronicle, retrieval, evidence, model, curation, and export layers.*


## 17.1 Logical services

**Table 40. Logical application services**

| Service | Responsibilities |
| --- | --- |
| Query service | Natural-language interpretation, structured filtering, hybrid ranking, grouping, facets, deduplication, cursor pagination, query fingerprinting. |
| Chronicle service | Time buckets, lane aggregation, density navigator, event clusters, focus periods, comparisons, date precision, and timezone normalization. |
| Evidence service | Stable source retrieval, passage context, citation validation, source relationships, and stale-citation checks. |
| Topic/identity service | Topic hierarchy and versions, entity resolution, manual curation, merge/split, and graph edge evidence. |
| Model gateway | Grounded prompts, provider routing, source policy, streaming, prompt-injection defenses, audit, and reproducibility metadata. |
| File service | Safe preview, download authorization, extraction text, page/sheet mapping, hashes, duplicates, and version-family comparison. |
| Workspace/export service | Workspaces, notebooks, snapshots, source manifests, redaction, package generation, and export audit. |
| Job service | Extraction, OCR, embedding, topic/entity/event generation, preview generation, retry, progress, and failure handling. |


## 17.2 Domain object contracts


### QueryScope

```json
{
  "version": 1,
  "query": "roof material decision",
  "mode": "hybrid",
  "date": {"from": "2014-01-01", "to": "2018-12-31"},
  "people": ["person:alice-chen"],
  "topics": ["topic:house-renovation"],
  "mailboxes": [],
  "source_types": ["message", "attachment"],
  "file_types": ["pdf"],
  "include_quoted_text": false,
  "exclusions": [{"kind": "topic", "id": "topic:newsletter"}],
  "workspace_id": null
}
```


### ChronicleRequest

```json
{
  "scope": {"fingerprint": "qs_01H..."},
  "viewport": {"from": "2012-01-01", "to": "2022-12-31"},
  "pixel_width": 920,
  "aggregation": "auto",
  "group_by": "source_type",
  "lanes": ["topics", "events", "messages", "attachments", "people"],
  "comparison": null,
  "timezone": "archive-default"
}
```


### EvidenceCitation

```json
{
  "citation_id": "cit_01H...",
  "source_id": "msg_123456",
  "source_type": "message",
  "thread_id": "thr_8877",
  "attachment_id": null,
  "extraction_version": "ext_v4",
  "location": {"char_start": 412, "char_end": 598},
  "excerpt_hash": "sha256:...",
  "display": {
    "date": "2015-06-17T16:45:00-04:00",
    "sender": "Alice Chen",
    "subject": "Re: roof material and warranty"
  }
}
```


### ChronicleEvent

```json
{
  "event_id": "evt_01H...",
  "title": "Standing-seam metal roof selected",
  "time": {"start": "2015-06-17", "precision": "day"},
  "origin": "automatic",
  "type": "decision",
  "status": "confirmed",
  "evidence_strength": "high",
  "claims": [
    {"text": "Metal roof selected", "status": "direct", "citations": ["cit_1", "cit_3"]}
  ],
  "derivation": {
    "process_version": "event-v3",
    "model_route": "configured-model-class",
    "scope_fingerprint": "qs_01H...",
    "generated_at": "2026-07-13T13:00:00Z"
  }
}
```


## 17.3 Endpoint surface

**Table 41. Recommended API surface**

| Method and path | Purpose |
| --- | --- |
| GET /api/archive/summary | Archive coverage, indexed ranges, counts, versions, and status. |
| POST /api/query/interpret | Convert natural language into a proposed QueryScope with editable tokens. |
| POST /api/search | Ranked sources, groups, facets, cursor, match explanations, and query fingerprint. |
| POST /api/ask | Server-sent event or equivalent stream for retrieval status, answer tokens, citations, warnings, and completion metadata. |
| POST /api/chronicle/buckets | Return lane aggregates and density data for scope, viewport, and pixel width. |
| POST /api/chronicle/focus | Return source chronology, clusters, and event candidates for a selected period or mark. |
| POST /api/chronicle/compare | Return aligned or small-multiple comparison datasets. |
| GET /api/sources/:id | Authoritative source metadata and safe body/preview descriptor. |
| GET /api/sources/:id/context | Passage plus surrounding text and relationship metadata. |
| GET /api/threads/:id | Thread source list, summary metadata, and correction layer. |
| GET /api/attachments/:id/preview | Authorized safe preview descriptor or stream. |
| GET/PATCH /api/topics/:id | Topic detail, history, curation, merge/split, membership overrides. |
| GET/PATCH /api/people/:id | Identity detail, aliases, merge/split, organization, notes. |
| GET/POST/PATCH /api/workspaces | Workspace CRUD, blocks, pins, snapshots, and conversation scope. |
| POST /api/exports | Start asynchronous export; returns job ID and inventory. |
| GET /api/jobs/:id | Job status, progress, failures, output, and retry controls. |


## 17.4 API behavior requirements

- All list endpoints use opaque cursor pagination and return stable object IDs.
- Every analytical response includes a scope or query fingerprint and the data/derivation versions used.
- Chronicle endpoints accept pixel width so the server can choose an appropriate aggregation density.
- Long-running requests are cancellable and identified by request ID. Obsolete client responses are ignored.
- All mutations use optimistic concurrency or version checks to prevent overwriting newer analyst corrections.
- Source content is never returned through a generated-object endpoint without stable source authorization checks.


## 17.5 Frontend implementation guidance

- Use a component architecture that separates shell state, working-set state, lens state, inspector state, and selection state.
- Use URL state for shareable analytical state and a client store for transient interaction. Avoid putting large source-ID arrays in the URL.
- Use WebGL or Canvas only for high-density Chronicle/map rendering where necessary; maintain an accessible DOM/table representation for selected or visible data.
- Use virtualization for tables and lists. Maintain stable row heights where possible and expose screen-reader-friendly nonvirtualized details for selection.
- Persist panel dimensions, theme, density, and keyboard preferences locally; persist saved views and workspaces on the server.


---


# 18. Functional requirements register

**Table 42. Normative functional requirements**

| ID | Priority | Requirement |
| --- | --- | --- |
| G-001 | MUST | The root route opens Life Chronicle. |
| G-002 | MUST | All primary lenses share one visible working set. |
| G-003 | MUST | Working-set state is serializable and restorable from a stable URL or saved object. |
| G-004 | MUST | The shell provides universal Search, Ask, and command-palette access. |
| G-005 | MUST | The evidence inspector opens without destroying lens state. |
| G-006 | MUST | Any source, derived object, or answer can be pinned to a workspace. |
| G-007 | MUST | Source objects and generated objects are visually and semantically distinct. |
| G-008 | SHOULD | Panel widths, density, theme, and lane configuration persist per device. |
| LC-001 | MUST | Chronicle supports decade-to-source zoom using changing aggregation levels. |
| LC-002 | MUST | Chronicle includes a full-scope density navigator. |
| LC-003 | MUST | Default lanes include topics, inferred events, messages, attachments, and people. |
| LC-004 | MUST | Lanes are hideable, reorderable, resizable, and saved with the view. |
| LC-005 | MUST | Selecting a mark opens an evidence-backed inspector. |
| LC-006 | MUST | Generated events expose origin, evidence, derivation, status, and correction controls. |
| LC-007 | MUST | Dense periods aggregate and provide Open as list. |
| LC-008 | MUST | Focus mode returns to the exact prior viewport and selection. |
| LC-009 | SHOULD | Compare mode supports periods, topics, people, and before/after event windows. |
| LC-010 | MUST | Chronicle remains functional when model services are unavailable. |
| RD-001 | MUST | Research Desk supports Hybrid, Exact, and Semantic retrieval. |
| RD-002 | MUST | Message and attachment text are searchable from the same query. |
| RD-003 | MUST | Natural-language constraints become editable structured filters. |
| RD-004 | MUST | Ask answers retain a visible ranked source list. |
| RD-005 | MUST | Every factual answer claim is cited or labeled unsupported/inferred. |
| RD-006 | MUST | Contradictory evidence is presented rather than silently resolved. |
| RD-007 | SHOULD | Results can group by thread, person, topic, time, mailbox, organization, file type, or version family. |
| TA-001 | MUST | Topic hierarchy is the default Topic Atlas view. |
| TA-002 | MUST | Topic origin and curation status are always visible. |
| TA-003 | MUST | Semantic projection uses level-of-detail aggregation. |
| TA-004 | MUST | Every visual selection opens a definitive source list. |
| TA-005 | MUST | Manual topic corrections are preserved across regeneration. |
| PE-001 | MUST | People can be merged and split with versioned analyst corrections. |
| PE-002 | MUST | Owner addresses, shared mailboxes, and role accounts are distinguishable. |
| PE-003 | MUST | Graph edges expose the exact source relationship supporting them. |
| PE-004 | MUST | The interface does not infer relationship quality from volume. |
| FI-001 | MUST | Attachment hits link to source messages. |
| FI-002 | MUST | Failed extractions remain discoverable by metadata. |
| FI-003 | MUST | Preview is sandboxed and never executes active content. |
| FI-004 | SHOULD | Exact duplicates and probable version families are grouped with provenance preserved. |
| TR-001 | MUST | Email HTML is sanitized and remote content is blocked by default. |
| TR-002 | MUST | Quoted content is collapsible and expands around search hits. |
| TR-003 | SHOULD | Thread merge/split corrections are supported without altering source headers. |
| WS-001 | MUST | Workspaces store a live query and pinned evidence. |
| WS-002 | MUST | Workspaces support notebook layout and source-aware export. |
| WS-003 | MUST | Exports include a provenance manifest. |
| WS-004 | SHOULD | Workspaces support reproducible snapshot source sets. |
| AI-001 | MUST | Every AI action displays its scope and source-type inclusion. |
| AI-002 | MUST | External model routing is visible and configurable. |
| AI-003 | MUST | Generated objects carry process/model version and evidence metadata. |
| AI-004 | MUST | Prompt-injection content in the archive cannot change tool policy or scope. |
| AI-005 | MUST | User corrections are not silently overwritten. |
| DH-001 | MUST | Data Health reports archive coverage, extraction, embedding, and job failures. |
| DH-002 | MUST | Failed records can be opened and retried where appropriate. |
| SEC-001 | MUST | Authentication, session control, least privilege, and export audit are implemented. |
| SEC-002 | MUST | Remote images, scripts, styles, and tracking resources are blocked by default. |
| SEC-003 | MUST | Redacted exports never overwrite original sources. |
| PERF-001 | MUST | Lists use cursor pagination and virtualization. |
| PERF-002 | MUST | Chronicle/map/graph use server-side aggregation and level of detail. |
| PERF-003 | MUST | Obsolete requests can be cancelled and stale responses ignored. |
| A11Y-001 | MUST | Target WCAG 2.2 AA with full keyboard access. |
| A11Y-002 | MUST | Every chart, map, and graph has a list or table equivalent. |
| A11Y-003 | MUST | No state is represented by color alone. |


---


# 19. Acceptance workflows and definition of done


## 19.1 Workflow A - reconstruct a decision

1. The user opens Chronicle, searches or spots the renovation topic, and brushes June 2015.
2. Chronicle zooms to thread/file clusters and shows an inferred decision event.
3. Selecting the event opens evidence strength, status, and source chain in the inspector.
4. Open reconstruction shows atomic claims, direct/supporting/conflicting status, and citations.
5. The user opens the original message and PDF, returns to the exact Chronicle viewport, confirms or edits the event, and pins it to a workspace.

Pass condition: no claim is accepted without accessible supporting sources; the user never loses the selected period while inspecting evidence.


## 19.2 Workflow B - explore an unknown period

1. The user starts from the full archive and notices an activity burst in 2007.
2. Double-clicking the burst opens Focus mode with threads, files, topics, people, and events for the period.
3. The user opens Topic Atlas from the current scope and sees the dominant topics without rebuilding the range.
4. The user selects a topic cluster, returns to Chronicle, and saves the period as a workspace.

Pass condition: the working set remains identical across lens changes and each aggregated visual has an authoritative source list.


## 19.3 Workflow C - find a vaguely remembered file

1. From Chronicle or Research Desk, the user asks for the spreadsheet Bob sent with projected expenses around 2012.
2. The interface displays interpreted person, file type, date, and semantic constraints as editable filters.
3. File results show filename, source message, sheet/cell hit where available, extraction status, and probable versions.
4. The user previews the spreadsheet, opens the source message, and compares a later version.

Pass condition: the file remains discoverable even if extraction is partial, and source-message provenance is never lost.


## 19.4 Workflow D - investigate a person

1. The user opens a person from a Chronicle lane or command palette.
2. The profile shows resolved aliases, archive span, activity over time, topics, co-participants, files, and events.
3. The user corrects an incorrectly merged address and sees the profile and Chronicle counts update.
4. The ego graph displays only evidence-backed edges; selecting an edge opens the exact threads and date range.

Pass condition: identity corrections are versioned, reversible, and preserved across automatic reprocessing.


## 19.5 Workflow E - produce a defensible case file

1. The user creates a workspace from a Chronicle period and pins messages, attachments, an event, and a grounded answer.
2. The user adds notes and a conclusion, then creates a snapshot source set.
3. The user exports a PDF and CSV manifest with redaction review.
4. The export record stores source fingerprint, derivation versions, redaction policy, and checksums.

Pass condition: a reviewer can trace every exported claim to a listed source and reproduce the source set.


## 19.6 Release definition of done

**Table 43. Release definition of done**

| Area | Release gate |
| --- | --- |
| Product | Chronicle is the root route and all secondary tools inherit its scope. |
| Evidence | Every generated claim and event exposes citations and complete source access. |
| Safety | Email and attachment rendering passes security review; remote content is blocked. |
| Scale | Representative archive-scale tests demonstrate pagination, aggregation, cancellation, and stable memory usage. |
| Accessibility | Keyboard, screen-reader, contrast, zoom, and non-graphical alternatives pass the agreed WCAG 2.2 AA review. |
| Privacy | External model routing, audit, export controls, and redaction behavior are verified. |
| Data quality | Extraction failures, stale derived data, and indexing coverage are visible in Data Health. |
| State | Back/forward, deep links, saved views, and workspace restoration recover the intended analytical state. |


---


# 20. Delivery phases and agent implementation brief


## 20.1 Recommended delivery sequence

**Table 44. Implementation phases**

| Phase | Scope | Exit criterion |
| --- | --- | --- |
| 0 - Archive adapter | Map existing PostgreSQL, pgvector, and file storage into stable source contracts; implement auth and Data Health basics. | Source IDs, archive coverage, safe retrieval, and extraction/embedding status are reliable. |
| 1 - Chronicle foundation | Global shell, working set, timeline buckets, density navigator, default lanes, inspector, source reader, URL state. | User can navigate full archive to individual sources and return without state loss. |
| 2 - Research and evidence | Hybrid search, structured filters, result cards, Ask with citations, attachment browser/preview, workspaces. | User can answer a factual question, inspect evidence, and export a source manifest. |
| 3 - Chronicle intelligence | Event schema, reconstruction, confirmation/edit/dismiss, Focus mode, compare mode, generated summaries. | Generated events are reviewable, evidence-backed, and versioned. |
| 4 - Secondary exploration | Topic hierarchy/map/river/matrix, people/organizations, identity curation, ego graph, duplicates/version families. | Every secondary lens preserves scope and exposes authoritative source lists. |
| 5 - Hardening | Accessibility, security, performance, redaction, audit, job resilience, large-archive testing. | All release gates in Section 19.6 pass. |


## 20.2 Agent mission

> **Goal:** Build a private, desktop-first analyst workstation for a lifelong email and attachment archive. The default and root experience is Life Chronicle, a zoomable time-first interface with source-backed events and an evidence inspector. Research Desk, Topic Atlas, People, Files, Message Reader, Workspaces, and Data Health are secondary application capabilities that operate on the same working set.


### Required implementation posture

- Treat this document as the product and interaction baseline. Do not substitute an inbox, dashboard, chat-first home, or global graph for Chronicle.
- Prioritize source access, state preservation, scale, and trust over decorative visualizations.
- Build against stable domain and API contracts with an adapter to the existing archive schema.
- Implement generated objects as versioned hypotheses with evidence and correction, never as source facts.
- Make all model routing, private-content transmission, and export actions visible and auditable.
- Ship vertical slices that include interface, source evidence, loading/error states, accessibility, and tests rather than isolated mock screens.


### Expected agent deliverables

**Table 45. Agent handoff deliverables**

| Deliverable | Contents |
| --- | --- |
| Architecture note | Existing schema adapter, service boundaries, data contracts, model policy, file-preview strategy, and threat model. |
| Design system | Tokens, component states, typography, density, keyboard behavior, and accessible chart/list equivalents. |
| Interactive prototype | Chronicle default, event reconstruction, Research Desk, Topic Atlas, source reader, files, person profile, and workspace. |
| Implementation plan | Milestones, dependencies, migration/additive tables, test data, performance plan, and security review points. |
| Production code | Frontend, APIs, jobs, migrations/views, tests, documentation, and deployment configuration. |
| Verification report | Acceptance workflows, performance results, accessibility findings, security controls, and known limitations. |


### Agent decision boundaries

The agent may choose the frontend framework, backend language, charting technology, job system, and exact database adaptation strategy. It may not change the root experience from Chronicle, remove source-level provenance, hide generated-object origin, silently send archive content to external providers, or require loading the complete archive into the browser.


---


# Appendix A. Object definitions and query examples


## A.1 Terminology

**Table 46. Shared vocabulary**

| Term | Definition |
| --- | --- |
| Archive | The complete indexed set of messages, threads, attachments, extracted content, and metadata available to the application. |
| Working set | The current server-evaluable scope shared by all lenses. |
| Viewport | The visible time range inside Chronicle; it may be narrower than the working-set date range. |
| Lens | A presentation and interaction mode over the same working set, such as Chronicle, Research Desk, or Topic Atlas. |
| Source | An immutable message, attachment, source metadata record, or extracted source passage. |
| Derived object | A versioned topic, entity link, event, summary, embedding, or other analytical object generated from sources. |
| Analyst object | A user-authored or user-corrected topic, identity, event, note, conclusion, or selection. |
| Evidence chain | Ordered source citations supporting, contradicting, or contextualizing a claim or event. |
| Source-set fingerprint | Stable hash or identifier representing a reproducible set of sources plus relevant extraction versions. |


## A.2 Example user queries

**Table 47. Representative archive interactions**

| Intent | Example |
| --- | --- |
| Chronicle orientation | Show the years when photography was most active and mark major equipment purchases. |
| Decision reconstruction | What did we finally decide about the roof material, and when did the decision change? |
| Person history | Show my interactions with Alice before and after the 2015 renovation. |
| Attachment retrieval | The spreadsheet Bob sent with projected expenses, probably around 2012. |
| Contradiction review | Find every stated total for the roofing project and show where they disagree. |
| Topic discovery | What other topics are most closely associated with the house renovation period? |
| Workspace analysis | Using only this workspace, summarize decisions that remain unresolved. |
| File lineage | Compare every version of Final_Roof_Estimate.pdf and identify changed amounts. |


## A.3 Final product summary

The finished application should open as a professional chronological analysis station. The owner first sees the shape of a life through time, then moves into ranked evidence, semantic topics, people, files, and case work without losing scope. The system earns trust by keeping original sources immutable, generated interpretations visible and correctable, and every conclusion traceable to evidence.
