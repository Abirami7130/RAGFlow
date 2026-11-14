# RAGFlow — UI/UX Guidelines

**Last updated:** 2026-11-12
**Status:** Binding for every screen. Implementation that drifts toward a generic dashboard is a defect.

RAGFlow should feel like a printed research desk that learned to run a retrieval lab: warm paper, sharp type, quiet rules, and technical detail set in mono when the detail is a measurement. Density is welcome. Decoration is not.

## Personality

- Editorial, not playful.
- Precise, not austere in a cold way. The warmth comes from paper colors and serif titles, not from illustrations.
- Confident hierarchy. One thing on the screen is clearly primary.
- Engineering surfaces (playground, search scores, traces) are part of the same product, not a separate “admin theme.”

## Color

Use these tokens and no additional hues, except data-viz opacity of the same inks.

| Token | Hex | Use |
| --- | --- | --- |
| `ivory` | `#F5F0E7` | Application background. |
| `cream` | `#FBF8F2` | Reading panes, inspector panels, composer. |
| `beige` | `#EAE1D3` | Sidebar, table header, sunken wells, hover on rows. |
| `charcoal` | `#29251F` | Primary text, primary buttons, pipeline “done” ink. |
| `taupe` | `#82796D` | Secondary text, axis labels, inactive stages. |
| `line` | `#D8CDBD` | Hairline borders, rules, dividers. |
| `olive` | `#3E4A38` | Selection, links, focus, active nav, chart ink. |
| `olive-wash` | `#E4E6D8` | Selected row, selected pipeline node fill. |
| `terracotta` | `#B85C38` | Citation labels, a single highlight, chart comparison. |
| `dried-red` | `#8C3A2F` | Destructive actions and hard errors only. |

Do not use pure white, pure black, purple, electric blue, or gradient meshes. Chart series are olive first, taupe second, terracotta third. No rainbow categories.

Map shadcn variables onto these tokens (`--background`, `--foreground`, `--card`, `--border`, `--primary`, `--muted`, `--accent`, `--ring`, `--destructive`). `--primary` is charcoal with cream text. `--ring` is olive. `--destructive` is dried red.

## Typography

Self-host the files. Do not load fonts from a third-party stylesheet at runtime.

| Role | Family | Notes |
| --- | --- | --- |
| Display | Instrument Serif | Page titles, knowledge-base name in the header, document title in the preview. Not for buttons, tables, or long answers. |
| Heading | Space Grotesk | Navigation, section titles, buttons, table headers. |
| Body | DM Sans | Paragraphs, chat answers, form fields, table cells. |
| Mono | IBM Plex Mono | Scores, ids, stages, prompts, locators, token counts. Use `tabular-nums` for scores. |

Why this set: Instrument Serif and Space Grotesk split “journal” from “instrument.” DM Sans holds dense UI without looking like Inter. IBM Plex Mono marks anything that is a measurement or a trace. Playfair Display was rejected as too ornamental. Manrope was rejected as too close to a default geometric SaaS face.

### Scale

| Style | Face | Size / line-height | Tracking |
| --- | --- | --- | --- |
| Display | Instrument Serif | 40px / 1.05 | -0.02em |
| Title | Space Grotesk | 22px / 1.25 | -0.02em |
| Section | Space Grotesk | 15px / 1.3 | 0 |
| Body | DM Sans | 15px / 1.6 | 0 |
| Small | DM Sans | 13px / 1.5 | 0 |
| Meta | DM Sans | 12px / 1.4, taupe | 0.01em |
| Mono | IBM Plex Mono | 12.5px / 1.5 | 0 |

Page titles are display size only on Dashboard, Knowledge Bases, and the playground header. Inner tools use Title, so the product does not shout on every route.

## Layout

- Desktop first. Minimum supported width 1180px for the playground. Other pages reflow down to 880px. A compact stack below that is acceptable; a separate mobile product is not a v1 goal.
- App frame: left rail 216px, ivory/beige, then a cream or ivory canvas.
- Rail contains the wordmark “RAGFlow” in Space Grotesk, the knowledge-base switcher as a text control, and nine text links. The active link is olive with a 2px olive bar, not a filled pill.
- Page padding 32px. Column gap 24px. Section gap 28px.
- Reading measure for chat answers: 68ch centered in the transcript column. The citation rail sits outside that measure.
- Hairline borders (`1px solid var(--line)`) separate regions. Prefer a border to a card.
- Radius: 2px on buttons and inputs, 4px on popovers. Tables, the pipeline canvas, and the page frame are square.
- Shadow: none on panels. Popovers may use `0 12px 32px rgba(41, 37, 31, 0.08)`.

No sticky marketing hero, no empty state illustration pack, no three-column feature grid.

## Motion

Transitions 160ms, ease `cubic-bezier(0.2, 0, 0, 1)`. Use them for row hover, inspector open, and streaming caret. Do not bounce, spring, or autoplay the pipeline. Nodes appear or change status when a trace event arrives.

## Components

### Buttons

- Primary: charcoal fill, cream label, Space Grotesk 13px, height 34px, padding 0 14px.
- Secondary: transparent, 1px line border, charcoal text.
- Quiet: text only, olive or charcoal, underline on hover.
- Danger: dried-red text, line border, never a large red fill.

One primary button per region.

### Text inputs

Cream fill, 1px line, 2px radius, 36px height. Focus: 2px olive outline, no glow. Labels above the field in meta style, not floating placeholders. Placeholder text is an example, taupe, and not the only label.

### Tables and indexes

Full width. Header row beige, Space Grotesk 12px, taupe, no uppercase shouting except short meta labels if needed. Body DM Sans 14px. Numeric columns IBM Plex Mono, right-aligned. Row hover beige. Selected row olive-wash. Row dividers are the line color. No zebra striping stronger than a 50% beige. No card wrapped around the table.

### Status

A word, not a badge farm. `Ready` in olive, `Failed` in dried red, in-progress stages in charcoal, queued in taupe. Optional 6px square of the same color before the word. Progress for ingestion is a horizontal list of stage names, the current one underlined. Not a spinner occupying a card.

### Citations

Inline `[S1]` in IBM Plex Mono, terracotta. Clicking scrolls the citation rail and flashes the row with olive-wash for one second. The rail item shows document title (DM Sans), locator in mono (`p. 4`, `§ Methods / Retrieval`, `slide 3`, `sheet Scores · row 12`, `$.items[2].name`, `src/retriever.py:40–58`), and a two-line excerpt. Deleted sources stay readable and append “source removed” in taupe.

### Pipeline nodes

Rectangle, cream fill, 1px line, width about 180px, square corners. A 3px left edge encodes state: taupe skipped or waiting, charcoal running, olive succeeded, dried red failed. Stage name in Space Grotesk. Timing in mono underneath. Selected node: olive-wash fill and olive edge. Edges are 1px taupe orthogonal lines, not curves and not animated dashes.

### Code and prompts

Inspector blocks: cream surface, inset 16px, IBM Plex Mono 12.5px, charcoal. Long prompts scroll inside the panel. Do not syntax-highlight with a rainbow theme. A single olive color for `[S1]` labels inside the prompt is enough.

## Accessibility

- Text contrast: charcoal on ivory/cream, taupe only above 12px and not for essential instructions.
- Focus visible on every control, olive 2px.
- Pipeline status is text, not color alone.
- Hit targets at least 32px.
- Respect `prefers-reduced-motion` by dropping the caret blink and the citation flash.

## Do not

- Purple or blue AI gradients, glass blur, neon borders.
- Inter, or a single font for everything.
- Four identical metric cards across the dashboard top.
- Chat bubbles, bot avatars, or a centered “How can I help?” hero.
- Oversized empty states with generic illustrations.
- Fully rounded cards, heavy shadows, or icon-only navigation.
- Fake pipeline playback that is not tied to trace events.
- Donut-chart grids.

## Page plans

### 1. Dashboard

**Purpose.** Show whether the active knowledge base is healthy and where to go next.

**Layout.** Header: knowledge-base name in Instrument Serif, one-line description in body, quiet actions “Add document” and “Ask”. Body is two columns, 1.4fr / 1fr, not a card grid.

**Left.** “Library” section title and a ruled list of the eight most recent documents: title, source type in mono, status word, updated time in meta. Click opens Documents with that id selected.

**Right.** “Latest inquiry” shows the last question in Space Grotesk and three lines of the answer in DM Sans, plus citation labels. If none exist, the column says “No questions yet” and a text link to AI Chat. Under it, a single 14-day query sparkline, 72px tall, olive stroke, no grid explosion, labeled “Questions”.

**Interactions.** Switching knowledge base in the rail updates every region. Failed documents, if any, are a single line under the library: “1 document failed — view” in dried red.

### 2. Knowledge Bases

**Purpose.** Create, rename, and enter a library.

**Layout.** Display title “Knowledge bases”. A table-of-contents list: name in Instrument Serif 28px, description, counts in mono (`12 documents · 840 chunks`). Row click enters and sets it active. “New knowledge base” opens a right-hand sheet (cream, 400px) with name and description, not a modal stacked on a blurred page.

**Phase 1 exception.** Chunk tables do not exist yet. Until Phase 3, the list shows `{document_count} documents · {ready_count} ready` in mono instead of a chunk total. After chunks ship, switch the second number to indexed chunk count per the line above.

**Interactions.** Delete sits inside the sheet as a quiet danger action with a typed confirmation of the name. Empty account: one sentence and the same sheet, no illustration.

### 3. Documents

**Purpose.** Ingest, monitor, preview, reprocess, and delete.

**Layout.** Split 38% / 62%. Left is the document index (title, type, status, version). A top tool row: upload button, URL, GitHub. The drop target is the list area itself with a one-line hint, border only when a file is dragged — not a permanent dashed box.

**Right preview.** Document title in Instrument Serif. Meta row in mono: type, version, bytes, chunk count, embedding model. Stage strip for the active run. Then the canonical blocks in reading order on cream, with a gutter of locators in taupe mono (`p. 2`, `L14`). Tables stay monospaced if they are cell data.

**Interactions.** Selecting a row loads blocks for the current version. “Reprocess” asks for confirmation and uses the knowledge-base chunk config. “Versions” is a short list in the preview header, not a separate page. Poll status every 2 seconds while not `ready` or `failed`, using the status endpoint. Failed runs show `error_code` and the ingestion trace link.

### 4. AI Chat

**Purpose.** Ask across the knowledge base and check citations.

**Layout.** Three columns: conversation list 240px, transcript flexible, citation rail 300px. Transcript is cream. Messages are not bubbles. A meta label “You” or “RAGFlow” in Space Grotesk 12px sits above the text. User text is DM Sans. Assistant text is DM Sans at 16px / 1.65. Citations render inline.

**Composer.** A single ruled field at the bottom of the transcript, plus Send. Mode control is a quiet segmented text control: Answer, Summarize, Compare. Summarize and Compare reveal a document multi-select drawn from the index, not a second page.

**Interactions.** Submit calls `POST /messages`, then reads SSE. Tokens append. On `citation` events, the rail fills. Clicking `[S1]` or a rail row selects that source. “Open trace” links to the playground with `trace_id`. Errors render as a dried-red line in the transcript, including `llm_not_configured`, and the trace link remains.

### 5. Search

**Purpose.** Inspect retrieval without a generated answer.

**Layout.** Query field at the top, full width of the canvas, large but not a hero (Space Grotesk 22px input). Under it, a line of toggles in text: Hybrid, Rerank, and a document filter. Results are rows: rank in mono, passage (three lines), document title, locator, and a score stack in mono (`vec #2 · bm25 #4 · rrf .031 · rerank 1.8`). A thin olive bar shows relative rerank strength, max 64px wide.

**Interactions.** Submit runs `POST /search` and stores `trace_id`. “Open in playground” deep-links that trace. Empty query does not fire. No results: one sentence, “Nothing retrieved,” plus the trace link so a failed index is still inspectable.

### 6. RAG Playground

**Purpose.** Watch a real run and inspect every stage. This is the most important screen.

**Layout.**

- **Control strip** (56px, beige): question field, Run, Hybrid, Rerank, Generate, context k as a small numeric field, and a trace picker labeled “Replay”.
- **Canvas** (remaining height, about 58%): React Flow, ivory background, no dots grid or a grid so faint it is barely a paper texture (`line` at 8% opacity). Nodes in a fixed layout, left to right:

```text
query_prepare → embed_query → ┬ vector_search ┐
                               └ lexical_search ┴→ fuse → rerank → build_context → build_prompt → generate → map_citations
```

Skipped stages stay on the canvas, taupe edge, status “skipped”. They are not hidden. That is how a person sees what the config turned off.

- **Inspector** (bottom 42%, cream, top hairline): left list of stages with duration in mono; right pane is the selected stage.

**Inspector content by stage.**

| Stage | What the pane shows |
| --- | --- |
| query_prepare | Exact retrieval string. |
| embed_query | Model, revision, dimension, L2 norm, eight-value preview. |
| vector_search | Table of hits: rank, score, chunk id, locator, text excerpt. |
| lexical_search | Same, plus matched terms with tf and idf. |
| fuse | Both ranks and the RRF score, formula reminder `1/(k+rank)` with the k used. |
| rerank | Cross-encoder model and scores before/after. |
| build_context | Packed passages with `[S1]` labels and the token budget used. |
| build_prompt | Full prompt, template version. |
| generate | Streamed text or the recorded output, plus error code if the provider failed. |
| map_citations | Parsed labels, dropped unknown labels, locators. |

Chunk text in the inspector is DM Sans. Numbers, ids, and the prompt are IBM Plex Mono.

**Interactions.** Run posts to `/pipeline/runs` and subscribes to the stream. Node status updates only from `stage` events. Selecting a node loads event detail. Replay fetches `GET /traces/{id}` and does not invent timing. Dragging nodes is disabled so the layout stays legible; pan and zoom are allowed. A link “Show chunk” opens the chunk endpoint in the inspector, including embedding metadata.

**Honesty rule.** If the stream has not produced an event, the node reads “waiting” and the inspector is empty. No sample chunks, no placeholder scores, no demo prompt.

### 7. Analytics

**Purpose.** Explain usage and ingestion quality with a few precise views.

**Layout.** Title “Analytics”. First row is a sentence, not tiles: “10 ready documents, 840 chunks, 18 questions in 7 days, median 940 ms.” The numbers are Space Grotesk, the words DM Sans.

Then a two-column layout. Left: 14-day questions and ingestions, one chart, olive and taupe lines, height 220px. Right: most-cited documents as a table (title, citations). Below, full width: recent ingestion failures as a table (document, code, when) and a latency chart (median and p95).

**Interactions.** Charts use the analytics endpoints. No client-side fake series when the API returns an empty array; the chart area says “No queries in this window.”

### 8. Evaluations

**Purpose.** Compare retrieval experiments on a fixed question set.

**Layout.** Left: evaluation sets as a short index. Main: cases as a ruled list (question, expected document titles). Top action “Run experiment” opens a sheet for a label and the retrieval toggles.

Results are a comparison table: one row per run, columns Label, Hit@5, MRR, Median ms, Cases. The best Hit@5 in the set is olive. A selected run expands per-case rows: question, hit or miss, reciprocal rank, top retrieved document. Misses use dried red for the word “miss” only.

**Interactions.** Runs poll until `succeeded` or `failed`. Generation is off by default and labeled “Also generate answers,” because Hit@k does not need Gemini.

### 9. Settings

**Purpose.** Show who is signed in and whether models are actually available.

**Layout.** Narrow column, 640px, like a preferences page in a book. Sections separated by hairlines:

- Profile: display name, email.
- Language model: provider name, model id, ready or not ready. No key field. Copy explains that the key is an environment variable.
- Local models: embedding and reranker ids and ready state.
- Defaults: link to the active knowledge base retrieval config, edited here as a simple form of the `RetrievalConfig` fields, saved with `PATCH`.

**Interactions.** Readiness comes from `GET /settings/providers`. Changing defaults does not reindex existing documents; the form says that reprocess is required after chunk-config changes. Chunk config is a second small form.

## Frontend structure (when implementation starts)

- Routes in React Router for the nine pages, plus `/login`.
- TanStack Query for server state. Status polls use `refetchInterval` only while a run is unfinished.
- React Flow only on the playground.
- Recharts only on analytics and only with the palette above.
- A single `lib/api.ts` client that attaches the bearer token and parses the error envelope.
- Visual primitives in `components/ui`, restyled. Product components (`DocumentIndex`, `CitationRail`, `PipelineCanvas`, `StageInspector`) are hand-built.

## Visual QA checklist

Before a UI phase is called done:

- No default shadcn slate screens remain.
- Fonts match the four roles.
- A reviewer can name the primary action on each page.
- Playground nodes correspond to trace stages, including skipped ones.
- Chat has a citation rail and no bubbles.
- Dashboard is not a row of metric cards.
