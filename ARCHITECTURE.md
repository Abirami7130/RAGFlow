# RAGFlow — Architecture

**Last updated:** 2026-11-12
**Status:** Phase 2A upload and durable storage are implemented under `app/modules/ingestion/`. Phase 2B implements the PostgreSQL worker (`python -m app.worker`), job claiming, ingestion traces, and retry semantics. Phase 2C.1 extracts TXT and Markdown into canonical blocks. Other formats still fail with `extractor_not_implemented`. Chunking, indexing, and activation are not implemented.

This document is the structural source of truth. Product scope lives in `PROJECT_OVERVIEW.md`. Decisions and rejected options live in `DECISIONS.md`. Tables live in `DATABASE_DESIGN.md`. HTTP lives in `API_SPECIFICATION.md`.

## System shape

RAGFlow is a modular monolith. The browser talks only to the API. The API and a worker share PostgreSQL and a disk volume. Nothing else is required for retrieval. Generation optionally calls Gemini over HTTPS.

```text
Browser (React)
    │  HTTPS / JSON / SSE
    ▼
FastAPI  ───────────────►  Gemini (optional, generation only)
    │
    ├── PostgreSQL   metadata, canonical text, chunks, BM25 postings, traces
    ├── Disk         original files, FAISS index files
    └── Worker       ingestion jobs, purge, index rebuild, later eval and entities
```

Logical modules are Python packages. They may call each other only through the service functions listed below. Extractors are not imported by the query path. The language-model client is not imported by the chunker.

## Planned repository layout

Not created yet. Implementation must follow this shape.

```text
ragflow/
  PROJECT_OVERVIEW.md
  ARCHITECTURE.md
  DATABASE_DESIGN.md
  API_SPECIFICATION.md
  IMPLEMENTATION_ROADMAP.md
  IMPLEMENTATION_LOG.md
  REVIEW_LOG.md
  DECISIONS.md
  UI_UX_GUIDELINES.md
  docker-compose.yml
  apps/api/
    pyproject.toml
    app/
      main.py
      core/                 config, db, security, errors
      modules/
        ingestion/
        normalization/
        chunking/
        embeddings/
        vectorstore/
        lexical/
        retrieval/
        generation/
        citations/
        tracing/
        analytics/
        evaluation/
        integrations/
        jobs/
      domain/               canonical models shared by modules
    tests/
    alembic/
  apps/web/
    src/
      pages/
      components/
      api/
    tests/
  storage/                  gitignored runtime data (STORAGE_DIR: incoming/ + {kb}/{doc}/{ver})
```

## Domain model shared by every module

Extractors do not emit ad-hoc dictionaries. They emit canonical blocks.

```text
SourceLocator
  source_type: pdf | docx | pptx | csv | xlsx | json | txt | md | web | github
  page: int | null
  bbox: {x0, y0, x1, y1} | null          PDF only, in page points
  heading_path: string[]                 nearest heading ancestry
  slide: int | null
  sheet: string | null
  row: int | null                        1-based
  cell: string | null                    A1 notation where a single cell is meaningful
  json_path: string | null
  url: string | null
  file_path: string | null               path inside a repository
  start_line, end_line: int | null
  char_start, char_end: int              offsets into the block text

CanonicalBlock
  id, document_version_id, sequence
  block_type: heading | paragraph | list_item | table | code | caption
  text
  locator: SourceLocator

Chunk
  id, processing_run_id, sequence
  text, token_count
  block_start_id, block_end_id
  locator_start, locator_end
```

A chunk’s text is the concatenation of one or more whole blocks whenever the blocks fit the token budget. Splitting a block is allowed only when a single block exceeds the budget. Split pieces keep the parent locator and add `char_start` / `char_end`.

## Modules

| Module | Responsibility | Does not |
| --- | --- | --- |
| Ingestion | Accept uploads and source requests, store bytes, create document and version rows, enqueue jobs. | Chunk or embed inline in the request. |
| Document normalization | Turn extractor output into clean blocks: whitespace, control characters, heading levels, table text. | Fetch the network. |
| Chunking | Build chunks from blocks using a versioned strategy. | Call a model. |
| Embeddings | Batch-embed chunk text; report model identity and dimension. | Write FAISS files itself. |
| Vector storage | Own FAISS files and id assignment. | Know about HTTP or users. |
| Lexical | Tokenize, write postings, run BM25. | Call the embedder. |
| Retrieval | Vector search, lexical search, RRF, rerank, context packing. Orchestrates query-time stages. | Parse office files. |
| Generation | Fill the prompt template and stream tokens from `LanguageModel`. | Score chunks. |
| Citations | Parse `[S#]` labels, attach locators and excerpts. | Add citations the model did not emit. |
| Pipeline tracing | Append `trace_events` for ingest and query. | Change stage results. |
| Analytics | Read-only aggregates over documents, traces, citations. | Write ingestion state. |
| Evaluation | Golden cases, runs, Hit@k and MRR. | Mutate the active index. |
| Integrations | URL fetch and GitHub clone adapters used by ingestion. | Bypass SSRF or size caps. |
| Jobs | Claim, retry, and fail background work. | Contain business rules beyond dispatch. |

## Provider protocols

```text
class EmbeddingProvider:
    model_id: str
    revision: str
    dimension: int
    def embed_documents(self, texts: list[str]) -> list[list[float]]
    def embed_query(self, text: str) -> list[float]

class Reranker:
    model_id: str
    def rerank(self, query: str, passages: list[str]) -> list[float]

class LanguageModel:
    provider: str
    model_id: str
    def generate(self, prompt: str) -> Iterator[str]

class VectorIndex:
    def add(self, ids: list[int], vectors: list[list[float]]) -> None
    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]
    def remove(self, ids: list[int]) -> None
    def rebuild(self, items: list[tuple[int, list[float]]]) -> None

class LexicalIndex:
    def upsert_chunk(self, chunk_id, kb_id, tokens: list[str]) -> None
    def remove_chunk(self, chunk_id, kb_id) -> None
    def search(self, kb_id, query_tokens: list[str], k: int) -> list[ScoredChunk]
```

Adapters live next to the protocol. Callers depend on the protocol.

## Ingestion data flow

```text
Client
  │  multipart file, or URL, or GitHub coordinates
  ▼
Ingestion API
  │  validate type, size, ACL
  │  write blob to STORAGE_DIR/incoming/{upload_id} (fsync file + directory)
  │  insert document + document_version with storage_state=incoming (path must exist before commit)
  │  rename atomically to {kb_id}/{document_id}/{version_id}, fsync both directories
  │  commit content_hash, dedup_hash, storage_state=stored (upload dedup_hash = content_hash)
  │  insert processing_run, query_trace, job(ingest_document); set pending pointers only
  │  never set current_version_id or preview_version_id in the upload handler
  ▼
Worker
  1. extract        format adapter → raw elements
  2. normalize      → canonical_blocks
  3. chunk          → chunks (text + locators)
  4. embed          → fsynced vector sidecar, model metadata on the run
  5. vector_index   → publish FAISS, then mark mappings committed
  6. lexical_index  → postings + chunk stats (run still inactive)
  7. activate       → one transaction switches the serving version
```

Each step appends a `trace_event`, including steps that are skipped. A failure marks that run `failed` and leaves the document’s serving version in place. See “Index publish”, “Atomic activation”, and “Jobs”.

Reprocess creates a new `processing_run` on the serving `document_version`. A new upload of changed bytes creates a new version and a run, and sets `documents.pending_version_id` without changing `current_version_id`. Identical bytes return the existing version unless `force=true`. A second ingest while `pending_run_id` is set returns `409 ingest_in_progress`.

### Format adapters

| Type | Library | Locator highlights |
| --- | --- | --- |
| PDF | PyMuPDF | `page`, `bbox`, heading path guessed from font size when the PDF has no outline |
| DOCX | python-docx | `heading_path` from heading styles |
| TXT | stdlib | line range |
| Markdown | stdlib parser in-repo (headings, fences, paragraphs) | `heading_path`, line range |
| PPTX | python-pptx | `slide` |
| CSV | stdlib `csv` | `sheet = filename`, `row` |
| XLSX | openpyxl | `sheet`, `row`; `cell` only for single-cell blocks; rows serialize as `header: value` lines |
| JSON | stdlib json | `json_path` |
| Web | httpx + BeautifulSoup | `url`, `heading_path` |
| GitHub | git CLI, depth 1 | `file_path`, line range |

Markdown parsing stays in-repo so heading locators are not an accidental dependency. Milestone 2C.1 handles ATX and setext headings, paragraphs, list items, fenced code, block quotes, and pipe-table body rows.

Encrypted or image-only PDFs fail with `pdf_unreadable`. The v1 pipeline does not OCR.

## Index publish and recovery

PostgreSQL decides which vectors are searchable. The FAISS file is a projection that can be rebuilt. Floats live in a per-run sidecar, not in a table.

Paths:

- Live index: `INDEX_DIR/{kb_id}/{index_id}/index.faiss`
- Stamp: `INDEX_DIR/{kb_id}/{index_id}/generation`
- Sidecar: `INDEX_DIR/{kb_id}/{index_id}/sidecars/{processing_run_id}.npz` with `ids` and L2-normalized `vectors`

`vector_mappings.state` is `staged`, `committed`, or `retired`. `vector_indexes.generation` is the published generation. Vector ids are allocated in Postgres and never reused.

Publish for one run:

1. Insert mappings as `staged` in a committed transaction, together with the chosen vector ids. Bump `next_vector_id` in that same transaction. Retrieval ignores `staged`.
2. Embed any chunk that is not already in a complete sidecar. Write the `.npz` to a temp file, fsync it, then rename it into `sidecars/`.
3. Load the live index (or an empty `IndexIDMap2` / `IndexFlatIP`). Add only staged ids that are not already present. Write `index.faiss.tmp`, fsync, and rename it over `index.faiss`.
4. Write the new generation stamp the same way.
5. In one transaction, set this run’s `staged` mappings to `committed` and set `vector_indexes.generation` to the stamp value.

Step 5 is the commit point. Until it commits, search still uses the previous generation’s committed mappings.

Query path, every time:

- Resolve the knowledge base’s index row and open the FAISS file for `generation`. If the stamp file disagrees with `generation`, rebuild the file from committed sidecars before searching.
- Take neighbors from FAISS, then keep a hit only when its mapping is `committed`, its processing run `is_active`, its document `deleted_at` is null, and its version is `documents.current_version_id`.

Failure and recovery:

| Point of failure | What remains true | What recovery does |
| --- | --- | --- |
| Crash during embed, sidecar not renamed | Serving version unchanged. Mappings may be `staged`. | Retry writes the sidecar. Existing staged ids are reused. |
| Crash after sidecar rename, before FAISS rename | Live index still matches the last committed generation. | Retry adds staged ids and publishes again. |
| Crash after FAISS rename, before the mapping transaction | DB generation is still the old value, so the new ids are not searchable. | Startup rebuilds `index.faiss` from **committed** sidecars only and rewrites the stamp to the DB generation. Retry publishes the staged run again. |
| `index_failed` after max attempts | Serving version unchanged. Run is `failed`. | Delete this run’s `staged` rows. Leave committed mappings untouched. The next manual reprocess allocates new ids. |
| Live FAISS file missing | DB generation and committed mappings remain. | Rebuild the file from committed sidecars. |
| Sidecar missing for a committed run | That run’s vectors cannot be rebuilt from disk. | Enqueue `reembed_run`. Do not clear `current_version_id`. If the damaged run is the serving run, lexical search still uses its postings; vector search omits those chunks until re-embed commits new mappings. |

`remove_ids` is used when retiring a run, not as the only crash strategy. A torn publish is discarded by rebuilding from committed sidecars.

A run that reaches `failed` after mappings were already `committed` is retired. `retire_run` removes those ids in a new publish, deletes that run’s postings, and sets `retired_at`. `current_version_id` stays on the previous serving version. The next reprocess creates a new run and new chunk ids, so retired mappings do not collide with it.

Readers cache `(index_id, generation)` and reload when `generation` changes. The worker and the API share the index directory through one volume. Publishing never edits `index.faiss` in place.

## Atomic activation

`documents.current_version_id` is the serving version. `documents.pending_version_id` and `documents.pending_run_id` point at work that is not serving yet. `documents.status` is the serving status (`ready`, `failed` when nothing has ever served, or `queued` before the first success). It stays `ready` while a replacement run is in progress. The status endpoint reports `pending_status` separately.

A new run writes chunks, a sidecar, committed mappings, and lexical postings while `is_active` is false. Those rows are invisible to retrieval because search requires `is_active`.

Activation is one database transaction:

1. `SELECT documents FOR UPDATE` on the document.
2. Require the pending run to have chunks, a sidecar, committed mappings for every chunk, and lexical stats for every chunk.
3. Set the currently active run, if any, to `is_active = false` and `superseded_at = now()`.
4. Set the pending run to `is_active = true` and `status = ready`.
5. Point `current_version_id` at the pending version, clear `pending_version_id` and `pending_run_id`, set `status = ready`, and clear the document error.
6. Recompute `lexical_corpus_stats` from active chunks only.

A partial unique index on `processing_runs(document_id) WHERE is_active` makes two serving runs impossible. If the transaction rolls back, the previous active run and `current_version_id` stay as they were.

The first successful run is activated the same way. There is no previous version to preserve, so a failure leaves `current_version_id` null and `status = failed`.

After a successful activation the serving run and the immediately previous ready run are kept. Older ready runs for that document are retired by a `retire_run` job: mappings become `retired`, their ids are removed in a new FAISS publish, their postings are deleted, and `retired_at` is set. Chunk text remains so old ingestion traces still resolve. A retired run cannot be restored.

`POST /documents/{id}/rollback` runs the same transaction in reverse against the immediate previous ready run when `retired_at` is null. Retrieval moves back in one commit. The run that was serving becomes the retained previous run.

## Jobs

The `jobs` table is the queue. Handlers are idempotent because a crash retries a unit of work that may already have completed its side effects.

Claim:

```text
UPDATE jobs
SET status = 'running', locked_by = $worker, locked_at = now(), attempts = attempts + 1
WHERE id = (
  SELECT id FROM jobs
  WHERE (status = 'pending' AND run_after <= now())
     OR (status = 'running' AND locked_at < now() - make_interval(secs => lock_timeout_seconds))
  ORDER BY run_after, created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *
```

The worker updates `locked_at` at least every 30 seconds while a handler runs. `lock_timeout_seconds` defaults to 900. A process kill looks like an expired lock and is claimed again.

Enqueue inserts `(job_type, idempotency_key)`. A unique partial index on that pair where `status in ('pending','running')` rejects duplicates. The API returns the existing job id.

| Job | Idempotency key | Handler rule |
| --- | --- | --- |
| `ingest_document` | `processing_run:{run_id}` | Continue the same run. Reuse blocks, chunks, staged ids, and a complete sidecar. |
| `purge_document` | `purge:{document_id}` | Delete by primary key. Missing files are success. |
| `retire_run` | `retire:{run_id}` | Retired mappings and absent postings make a second run a no-op. |
| `rebuild_index` | `rebuild:{index_id}` | Rebuild from committed sidecars and bump generation once. |
| `reembed_run` | `reembed:{run_id}` | New sidecar and new vector ids only for chunks whose sidecar is missing. |
| `run_evaluation` | `eval_run:{eval_run_id}` | Skip cases that already have a result row. |
| `extract_entities` | `entities:{run_id}` | Replace mentions for that run inside one transaction. |

Automatic retries: `ingest_document` jobs set `max_attempts` to **4** (one initial claim plus **three** transient retries). `attempts` increments on each claim. After a `TransientWorkerError`, the worker requeues the job when `attempts < max_attempts`, using `run_after` delays of **10s**, **30s**, and **90s** after the first, second, and third failed claims respectively. Permanent errors (`unsupported_type`, `file_too_large`, `mime_mismatch`, `pdf_unreadable`, `ssrf_blocked`, `extractor_not_implemented`, `invalid_encoding`, `nul_in_text`, `empty_content`, …) fail the job on the first claim with no automatic retry. When attempts are exhausted, the processing run is `failed` and pending pointers are cleared only if they still refer to that run. A serving version stays `ready`. An extracted preview stays `extracted`. `failed` on the document is used only when neither exists.

Stage writes check their outputs before doing the work again. Lexical upsert deletes that chunk’s postings and inserts the new ones in one transaction so a retry cannot double term frequency. FAISS add skips ids already in the index.

Query execution stays on the API process so tokens can stream. It is not a `jobs` row. API startup closes query traces left in `running` for more than 10 minutes: the open stage becomes `failed` with `execution_lost`, later stages are inserted as `skipped`, and the assistant message becomes `failed`. The answer is not generated again automatically.

## BM25 corpus

The chunk is the BM25 document. The same tokenizer feeds indexing and queries.

1. Unicode NFKC normalize.
2. Lowercase.
3. Split on apostrophes and on every character that is not a Unicode letter or number. Digits stay, so years and error codes match.
4. Drop empty tokens. There is no stemming and no stopword list in v1. A stopword is a high-document-frequency term with a small idf, which the trace can show.

`chunk_stats.token_count` is this token count (`dl`), which can differ from the embedding token count on `chunks.token_count`.

Corpus statistics count **active** chunks only (`processing_runs.is_active` and the document is not deleted):

- `N` = `lexical_corpus_stats.chunk_count`
- `token_sum` = sum of active `dl`
- `avgdl` = `token_sum / N` when `N > 0`, otherwise search returns no lexical hits
- `df(t)` = number of active chunks whose postings contain `t`

Inactive and deleted chunks keep their posting rows until retire or purge, and they are excluded from `N`, `avgdl`, and `df`. Postings for a not-yet-active run are written before activation and do not affect the corpus until the activation transaction recomputes stats. That transaction is the only moment both the membership flip and the stats change become visible.

```text
idf(t) = ln(1 + (N - df + 0.5) / (df + 0.5))
score = Σ idf(t) * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
```

Defaults: `k1 = 1.5`, `b = 0.75`. A term with `df = 0` adds nothing. The lexical trace event stores `N`, `avgdl`, `k1`, `b`, and per-term `df`, `tf`, and `idf` for the hits. Later corpus changes do not rewrite that event.

Delete sets `deleted_at` and `is_active = false`, then recomputes corpus stats, in one transaction. The document leaves `N`, `df`, and search before files are removed. Purge later deletes that document’s `lexical_postings` and `chunk_stats`. Those rows were already inactive, so purge does not change `N` or `avgdl`.

## Citations

`build_context` assigns labels `S1`, `S2`, … in pack order and persists that list on its trace event:

```json
{
  "labels": [
    {"label": "S1", "chunk_id": "uuid", "excerpt": "…", "locator": {}}
  ]
}
```

The prompt inserts those labels as the exact characters `[S1]`. The parser accepts only the pattern `\[S[1-9][0-9]*\]`. A match is kept when its label string is a member of this event’s label set. `S01` does not match `S1`. A chunk that was retrieved but not packed has no label and cannot be cited. Duplicate labels in the answer become one citation, ordered by first appearance.

Excerpt, locator, chunk id, and scores are copied from the label record. The server does not infer a citation from answer overlap. Labels outside the set are stored on the `map_citations` event as `rejected_labels` and are not inserted into `message_citations`. An empty label set, or an answer with no valid labels, completes the message with zero citations and records `no_citations_emitted`.

## Query data flow

Chat, search, and playground runs share this path. Search stops before generation. Summarize and compare are query modes with different prompt templates, not different pipelines.

```text
question
  │
  ▼
query_prepare          record the exact string to embed and search (D13)
  │
  ├── embed_query      EmbeddingProvider.embed_query
  ├── vector_search    VectorIndex.search → chunk ids via vector_mappings
  └── lexical_search   BM25 over postings
  │
  ▼
fuse                   RRF (skipped if hybrid off; vector order kept)
  │
  ▼
rerank                 cross-encoder (skipped if rerank off or model unavailable)
  │
  ▼
build_context          pack passages under a token budget, assign [S1]…
  │
  ▼
build_prompt           versioned template + context + short history
  │
  ▼
generate               LanguageModel stream (skipped for Search)
  │
  ▼
map_citations          keep labels that appear in the build_context set
```

Default retrieval config, stored on the knowledge base and overridable per playground run:

```json
{
  "top_k_vector": 20,
  "top_k_lexical": 20,
  "rrf_k": 60,
  "rerank_top_n": 20,
  "context_k": 5,
  "context_token_budget": 3000,
  "hybrid_enabled": true,
  "rerank_enabled": true,
  "bm25_k1": 1.5,
  "bm25_b": 0.75
}
```

Chunking defaults for new processing runs:

```json
{
  "strategy": "structure_aware",
  "target_tokens": 400,
  "overlap_tokens": 60,
  "split_oversized_blocks": true
}
```

Overlap is applied only when a block was split. Whole-block chunks do not repeat the previous heading; the heading path already lives on the locator. The chunk text is prefixed with the heading path as plain context lines so the embedder sees the section title. That prefix is marked in chunk metadata (`heading_prefix: true`) so the playground can distinguish it from body text.

Active corpus for a query:

- document `deleted_at` is null
- version is `documents.current_version_id`
- processing run `is_active` and `status = ready`
- vector mapping `state = committed` when the hit came from FAISS
- run’s embedding identity matches the query embedder

If a knowledge base has runs from two embedding models, query uses the model recorded on the active index. Mixed models in one index are rejected.

## Tracing

`query_traces` is the header. `trace_events` are append-only stage attempts. Both ingestion and query use this pair. The header is inserted before the first stage, with an immutable `config` snapshot: retrieval config, BM25 parameters, embedding model and revision, reranker id, prompt template version, and, for ingestion, chunk strategy and source type. `finished_at`, `status`, and `error_code` are set when the pipeline stops. `config` is not edited afterward. Context labels live on the `build_context` event.

Query stages, in order, always produce at least one event:

`query_prepare`, `embed_query`, `vector_search`, `lexical_search`, `fuse`, `rerank`, `build_context`, `build_prompt`, `generate`, `map_citations`.

Ingestion stages, in order:

`extract`, `normalize`, `chunk`, `embed`, `vector_index`, `lexical_index`, `activate`.

A skipped stage is a real row with `status = skipped`, `skip_reason`, and `duration_ms = 0`. Reasons include `config_disabled`, `model_unavailable`, `search_mode`, `empty_candidates`, and `upstream_failed`. Search sets `generate` and `map_citations` to `search_mode`. Hybrid off skips `lexical_search` and records `fuse` as a pass-through of the vector order (`config_disabled` on lexical search). A failed stage causes every stage that has not started to be inserted as `skipped` / `upstream_failed`, so a stored trace always contains the full stage list.

Each event stores:

- `stage`, `sequence`, `attempt`
- `status`: `running`, `succeeded`, `failed`, `skipped`
- `started_at` and `ended_at` from database `clock_timestamp()`
- `duration_ms`
- `params` for the stage (k, model ids, template version, BM25 parameters)
- `input` and `output`
- `error_code` and `error_detail` when failed
- `skip_reason` when skipped

The same row moves from `running` to a terminal status. A retry inserts a new row with the same `stage`, a higher `sequence`, and `attempt + 1`. Earlier failures stay in the log. List endpoints return previews. Event detail returns the full prompt, the label set, rejected labels, and hit tables.

Startup recovery closes an event left `running`: `status = failed`, `error_code = execution_lost` or `worker_lost`, `ended_at` set, and missing downstream stages inserted as skipped. The playground renders committed events only.

## Access control

Every knowledge-base route loads membership for the current user.

| Action | owner | editor | viewer |
| --- | --- | --- | --- |
| Read documents, chat, search, traces, analytics | yes | yes | yes |
| Upload, reprocess, delete documents | yes | yes | no |
| Change retrieval defaults | yes | yes | no |
| Manage members, delete knowledge base | yes | no | no |

Traces and conversations are visible to all members of the knowledge base in v1. Private-per-user chats can be added without moving the document tables.

## Error handling

API errors use one envelope (`API_SPECIFICATION.md`). Job errors use `error_code` values:

| Code | Kind | Retry |
| --- | --- | --- |
| `unsupported_type` | permanent | no |
| `file_too_large` | permanent | no |
| `mime_mismatch` | permanent | no |
| `pdf_unreadable` | permanent | no |
| `ssrf_blocked` | permanent | no |
| `fetch_failed` | transient | yes |
| `worker_lost` | transient until attempts are exhausted | yes |
| `execution_lost` | permanent for that message | no |
| `index_unavailable` | transient | yes |
| `clone_failed` | transient | yes |
| `embed_failed` | transient | yes |
| `index_failed` | transient | yes |
| `llm_not_configured` | permanent for that call | no |
| `llm_failed` | transient | yes, once, then surface |

User-facing text is the `message`. `error_code` is stable for the client.

## Safe deletion

1. One transaction sets `documents.deleted_at`, sets that document’s runs to `is_active = false`, clears `pending_run_id`, and recomputes lexical corpus stats.
2. A `purge_document` job deletes the blob, blocks, postings, chunk stats, sidecars, and FAISS ids for every version, then deletes chunk rows.
3. Citations set `source_deleted = true` and keep `excerpt` and `locator`.
4. Deleting a knowledge base runs the same purge for every document and removes the index directory.

The API never deletes by a user-supplied filesystem path.

## Versioning and index identity

```text
documents 1──* document_versions 1──* processing_runs 1──* chunks
knowledge_bases 1──* vector_indexes 1──* vector_mappings *──1 chunks
```

A vector index identity is `(knowledge_base_id, embedding_model, embedding_revision, dimension, metric)`.

`vector_id` is a bigint allocated per index, never reused. A neighbor becomes a chunk only through a `committed` mapping whose run is active. See “Index publish and recovery” and “Atomic activation”.

## Summarize and compare

These are generation modes on the query path:

- **Summarize** retrieves with a fixed query derived from the document title plus “main points”, restricted to one `document_id`, then uses the summarize template. The restriction is a trace param.
- **Compare** requires two or more document ids, retrieves per document, and uses the compare template so each side keeps its own citation labels.

They are not separate services.

## Analytics and evaluation

Analytics queries are SQL aggregates over documents, processing runs, traces, and citations. No warehouse. A daily rollup table is optional and not required for acceptance.

Evaluation runs call the retrieval path (and optionally generation) with a frozen config snapshot. They write results to evaluation tables and do not change `is_active` runs.

## Security boundaries designed in from the start

- Auth on every route except health, register, login, and refresh.
- Argon2 password hashes; refresh tokens stored hashed.
- Allowlisted extensions and magic-byte checks.
- Storage keys are UUIDs.
- URL ingest uses the resolver and redirect rules in “URL ingestion security”.
- HTML from the web is parsed to text. The app never renders fetched HTML.
- Gemini key only in the server environment.
- CORS limited to the web origin.
- Upload size and GitHub/URL caps as in D16 and D18.
- Rate limit on login and on generate endpoints (simple in-process or Postgres counter in Phase 9).

## URL ingestion security

The HTTP client does not follow redirects. The adapter resolves and checks every request, including each redirect hop, then connects to an address it has accepted.

Allowed URLs:

- Scheme `http` or `https`.
- A hostname, with no userinfo and no fragment sent to the server. Fragments are stripped during canonicalization.
- Port 80 or 443, or the scheme default.
- Hostnames are converted to punycode before resolution. `localhost`, names ending in `.localhost`, `metadata.google.internal`, and `metadata.internal` are rejected.

Before connect, resolve A and AAAA records. Reject the name if any answer is, or if the URL literal is:

- Loopback: `127.0.0.0/8`, `::1`
- Private: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`
- Shared address space: `100.64.0.0/10`
- Link-local: `169.254.0.0/16`, `fe80::/10`, including `169.254.169.254`
- Unspecified, multicast, or reserved: `0.0.0.0/8`, `::`, `224.0.0.0/4`, and IPv4-mapped IPv6 such as `::ffff:127.0.0.1`

A name that resolves to both a public address and a blocked address is rejected. The connection uses one accepted address (DNS pinning). The Host header and TLS SNI use the original hostname, and the certificate must match that hostname. The check is repeated for every connect so a rebinding answer cannot be reused from a previous lookup.

Redirects: at most five hops. Each `Location` is canonicalized and run through the same scheme, port, userinfo, DNS, and address checks. A redirect to a blocked address stops the job with `ssrf_blocked` and does not fetch the body. Crawl links (same host, depth at most 1, at most 20 pages) go through the same checks. A link to another host is not fetched.

Limits: connect timeout 5 seconds, read timeout 15 seconds, body at most 5 MB, `Content-Type` `text/html`, `application/xhtml+xml`, or `text/plain`. The ingestion trace stores each hop’s URL, status, and resolved addresses. Response cookies are not reused on a different host.

`ssrf_blocked` is permanent. A DNS or connection timeout is `fetch_failed` and may retry.

## Deployment shape

`docker-compose.yml` services:

| Service | Role |
| --- | --- |
| `postgres` | Application database. |
| `api` | Uvicorn, FastAPI. |
| `worker` | Job loop, embedder loaded here and in the API as in D17. Added when ingestion exists. |
| `web` | Static Vite build served by nginx, or the Vite dev server during development. |

Phase 1 compose runs `postgres`, `api`, and `web` only. The API modules present in Phase 1 are configuration, database, auth, knowledge bases, and provider readiness. Ingestion and the worker are not created yet.

Models download to a shared cache volume on first use so restarts do not re-fetch. Tests do not download `bge-small` unless marked integration. Unit tests use a deterministic fake embedder and an in-memory or fixture FAISS index.

## What is intentionally local

Sentence Transformers, the cross-encoder, spaCy, FAISS, Postgres, and file storage run on the machine. Gemini is the only planned paid-capable network dependency, and only for token generation. No hosted vector database is required.

## Scale assumption

Design target: one machine, dozens of documents, up to roughly 20,000 chunks per knowledge base, a handful of concurrent users. `IndexFlatIP` is a feature at that size because scores are exact and easy to explain. The vector protocol is the exit ramp if a corpus outgrows it.
