# RAGFlow — API Specification

**Last updated:** 2026-11-12. Phase 2A implements upload and storage. Phase 2B implements the PostgreSQL job worker, ingestion traces, automatic retries, and user retry via `force` (replacement only) or `POST /documents/{id}/reprocess`. Phase 2C.1 extracts TXT and Markdown and serves canonical blocks. Other format extractors, chunking, and activation are not implemented yet.
**Base path:** `/api/v1`

JSON uses `snake_case`. Identifiers are UUID strings. Timestamps are ISO-8601 in UTC. Authenticated routes expect `Authorization: Bearer <access_token>`.

## Error envelope

```json
{
  "error": {
    "code": "forbidden",
    "message": "You do not have access to this knowledge base.",
    "details": {}
  }
}
```

| HTTP | `error.code` | When |
| --- | --- | --- |
| 400 | `validation_error` | Body or query fails schema. `details.fields` lists issues. |
| 401 | `unauthenticated` | Missing or expired access token. |
| 403 | `forbidden` | Authenticated, not allowed. |
| 404 | `not_found` | Missing or soft-deleted resource the caller cannot see. |
| 409 | `conflict` | Duplicate email. |
| 409 | `ingest_in_progress` | This document already has a `pending_run_id`. `details.job_id` is the live job. |
| 409 | `duplicate_content` | Upload bytes already exist on another document in the knowledge base. `details.document_id` is the owner. |
| 409 | `version_retired` | Rollback target has `retired_at` set. |
| 413 | `file_too_large` | Over 25 MB. |
| 415 | `unsupported_type` | Extension or magic bytes rejected. |
| 422 | `unprocessable` | Well-formed but impossible, such as compare with one document or reprocess without a serving version. |
| 422 | `retry_limit_reached` | User retry budget exhausted for this version (`details.user_retry_limit`). |
| 429 | `rate_limited` | Login or generation limit. |
| 503 | `llm_not_configured` | Generation requested and `GEMINI_API_KEY` is unset. Retrieval routes still return 200. |

## Pagination

List routes accept `limit` (default 20, max 100) and `cursor` (opaque). Responses:

```json
{
  "items": [],
  "next_cursor": null
}
```

## Shared schemas

These names are the Pydantic models to implement. Fields marked optional may be omitted or null as specified.

### `UserRead`

```json
{
  "id": "uuid",
  "email": "ada@example.com",
  "display_name": "Ada"
}
```

### `TokenPair`

```json
{
  "access_token": "jwt",
  "refresh_token": "opaque",
  "token_type": "bearer",
  "expires_in": 900
}
```

Access lifetime 15 minutes. Refresh lifetime 14 days. Refresh rotation revokes the presented token.

### `SourceLocator`

See `DATABASE_DESIGN.md`. The same object is a Pydantic model with all locator fields optional except `source_type`, `char_start`, and `char_end`.

### `RetrievalConfig`

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

### `ChunkConfig`

```json
{
  "strategy": "structure_aware",
  "target_tokens": 400,
  "overlap_tokens": 60,
  "split_oversized_blocks": true
}
```

### `KnowledgeBaseRead`

```json
{
  "id": "uuid",
  "name": "Research",
  "description": "",
  "role": "owner",
  "document_count": 3,
  "ready_count": 2,
  "retrieval_config": {},
  "chunk_config": {},
  "created_at": "2026-11-12T00:00:00Z",
  "updated_at": "2026-11-12T00:00:00Z"
}
```

### `DocumentRead`

```json
{
  "id": "uuid",
  "knowledge_base_id": "uuid",
  "title": "design.pdf",
  "source_type": "pdf",
  "source_uri": null,
  "status": "ready",
  "error_code": null,
  "error_detail": null,
  "current_version_id": "uuid",
  "current_version_number": 1,
  "preview_version_id": "uuid",
  "pending_version_id": null,
  "pending_run_id": null,
  "pending_status": null,
  "byte_size": 120034,
  "chunk_count": 42,
  "created_at": "2026-11-12T00:00:00Z",
  "updated_at": "2026-11-12T00:00:00Z"
}
```

### `ChunkRead`

```json
{
  "id": "uuid",
  "sequence": 7,
  "text": "Retrieval\n\nHybrid search combines…",
  "token_count": 380,
  "locator_start": {},
  "locator_end": {},
  "metadata": {"heading_prefix": true},
  "embedding": {
    "model": "BAAI/bge-small-en-v1.5",
    "revision": "recorded-at-runtime",
    "dimension": 384,
    "vector_id": 18,
    "l2_norm": 1.0,
    "preview": [0.012, -0.034]
  }
}
```

`embedding` is null before indexing. `preview` is the first eight components. The full vector is omitted unless `include_vector=true`.

### `CitationRead`

```json
{
  "id": "uuid",
  "label": "S1",
  "document_id": "uuid",
  "document_title": "design.pdf",
  "chunk_id": "uuid",
  "excerpt": "Hybrid search combines…",
  "locator": {},
  "scores": {
    "vector": 0.81,
    "vector_rank": 2,
    "bm25": 4.2,
    "bm25_rank": 1,
    "rrf": 0.032,
    "rerank": 2.4
  },
  "source_deleted": false
}
```

### `TraceEventRead`

```json
{
  "id": "uuid",
  "stage": "rerank",
  "sequence": 5,
  "attempt": 1,
  "status": "succeeded",
  "skip_reason": null,
  "started_at": "2026-11-12T00:00:00Z",
  "ended_at": "2026-11-12T00:00:01Z",
  "duration_ms": 840,
  "params": {"model": "cross-encoder/ms-marco-MiniLM-L-6-v2"},
  "input_preview": "20 passages",
  "output_preview": "top score 2.4",
  "error_code": null
}
```

`TraceEventDetail` adds full `input` and `output` objects. A skipped stage uses `status: "skipped"`, a `skip_reason`, and `duration_ms: 0`. `GET /traces/{id}` returns every pipeline stage, including skipped ones and earlier failed attempts.

## Authentication

### `POST /auth/register`

Request `UserCreate`: `{ "email", "password", "display_name" }`. Password at least 10 characters. Response `201` `UserRead`. Does not sign in automatically.

### `POST /auth/login`

Request `{ "email", "password" }`. Response `200` `TokenPair`.

### `POST /auth/refresh`

Request `{ "refresh_token" }`. Response `200` `TokenPair`. Reuse of a revoked token revokes the rest of that user's live refresh tokens.

### `POST /auth/logout`

Request `{ "refresh_token" }`. Response `204`.

### `GET /auth/me`

Response `200` `UserRead`.

## Knowledge bases

### `GET /knowledge-bases`

Response `200` page of `KnowledgeBaseRead` for bases where the caller is a member.

### `POST /knowledge-bases`

Request `{ "name", "description"? }`. Caller becomes `owner`. Response `201` `KnowledgeBaseRead`.

### `GET /knowledge-bases/{kb_id}`

Response `200` `KnowledgeBaseRead`.

### `PATCH /knowledge-bases/{kb_id}`

Request partial `{ "name"?, "description"?, "retrieval_config"?, "chunk_config"? }`. Editors and owners may patch these fields. Membership and knowledge-base deletion stay owner-only. Response `200`.

### `DELETE /knowledge-bases/{kb_id}`

Owner only. Phase 1 soft-deletes in the request and returns `200`:

```json
{ "id": "uuid", "deleted": true }
```

There is no purge job yet. Phase 2 will return `202` with a `job_id` once documents and the job table exist. A non-member receives `404`. A member who is not an owner receives `403`.

### `GET /knowledge-bases/{kb_id}/members`

### `PUT /knowledge-bases/{kb_id}/members`

Owner only. Request `{ "email", "role" }`. Response `200` `{ "user_id", "email", "role" }`.

### `DELETE /knowledge-bases/{kb_id}/members/{user_id}`

Owner only. Cannot remove the last owner. Response `204`.

## Documents and ingestion

### `POST /knowledge-bases/{kb_id}/documents`

`multipart/form-data` field `file`. Optional form fields `title` and `force` (boolean, default false). Editor or owner. Response `201` `DocumentUploadResult` for a new stored version. Response `200` with `reused: true` when the same bytes already exist on another document in the knowledge base and the concurrent upload lost the dedup race. Response `409 duplicate_content` when those bytes are already owned by a different document in the same knowledge base. The first version sets serving `status` to `queued`, creates `pending_version_id` and `pending_run_id`, enqueues `ingest_document`, and does not set `current_version_id` or `preview_version_id`. Maximum size 25 MiB. Magic-byte validation applies to PDF and ZIP-based office types.

### `POST /documents/{document_id}/versions`

Replacement upload for an existing document. Same multipart fields as above. Returns `201` when a new version is stored, `200` with `reused: true` when the bytes match an existing version on the same document and `force` is false. A document that is already `ready` keeps `status` as `ready` and leaves `current_version_id` unchanged while setting pending pointers on the new version. `409 ingest_in_progress` when `pending_run_id` is set.

### `GET /documents/{document_id}`

Response `200` `DocumentRead`. Any knowledge-base member.

### `DocumentUploadResult`

```json
{
  "document": {},
  "reused": false,
  "job_id": "uuid"
}
```

`job_id` is null when `reused` is true and no new job was enqueued.

### `POST /knowledge-bases/{kb_id}/sources/url`

Editor. Request:

```json
{
  "url": "https://example.com/docs",
  "title": "Example docs",
  "max_pages": 20,
  "depth": 1
}
```

`max_pages` cannot exceed 20. `depth` cannot exceed 1. Response `201` `DocumentRead` (`source_type: web`) when the URL is syntactically allowed. One document represents the fetch; pages become blocks with their own `url` locators.

The request rejects, without creating a document, when the URL has userinfo, a non-HTTP scheme, or a port other than 80 or 443 (`400 validation_error`). Addresses are not trusted from the client. The worker resolves DNS and applies the private, loopback, link-local, and redirect rules in `ARCHITECTURE.md`. A blocked target fails the run with `ssrf_blocked` and does not replace a serving version. `GET /documents/{id}/status` then shows `pending_status: failed` and `error_code: ssrf_blocked` while `status` stays `ready` if a previous version is serving.

### `POST /knowledge-bases/{kb_id}/sources/github`

Editor. Request:

```json
{
  "repository_url": "https://github.com/org/repo",
  "git_ref": "main",
  "title": "org/repo"
}
```

Public HTTPS only. Response `201` `DocumentRead` (`source_type: github`).

### `GET /knowledge-bases/{kb_id}/documents`

Query: `status`, `source_type`, `q` (title substring), pagination. Response page of `DocumentRead`.

### `GET /documents/{document_id}`

Response `200` `DocumentRead`.

### `GET /documents/{document_id}/versions`

Response `200` `{ "items": [ { "id", "version_number", "content_hash", "byte_size", "mime_type", "original_filename", "created_at" } ] }`.

### `GET /documents/{document_id}/status`

Lightweight poll payload:

```json
{
  "document_id": "uuid",
  "status": "ready",
  "current_version_id": "uuid",
  "pending_status": "embedding",
  "pending_run_id": "uuid",
  "error_code": null,
  "error_detail": null,
  "trace_id": "uuid",
  "stages": [
    {"stage": "extract", "status": "succeeded", "attempt": 1, "duration_ms": 120, "skip_reason": null},
    {"stage": "embed", "status": "running", "attempt": 1, "duration_ms": null, "skip_reason": null}
  ]
}
```

`status` is what search uses. `pending_status` is the in-flight run and is null when no run is pending. While a run is in progress, `stages` contains only rows that have been opened. When the run reaches `ready` or `failed`, every ingestion stage is present. A failed stage is followed by later stages with `status: skipped` and `skip_reason: upstream_failed`. A running stage has no `skip_reason`.

### `GET /document-versions/{version_id}/blocks`

Any knowledge-base member. Query: `limit` (default 50, maximum 100) and `cursor`. Items are ordered by `sequence`: `{ "id", "sequence", "block_type", "text", "locator", "metadata" }`. Unknown versions and non-members return `404`. An invalid cursor returns `400`.

### `GET /processing-runs/{run_id}`

```json
{
  "id": "uuid",
  "document_version_id": "uuid",
  "status": "ready",
  "is_active": true,
  "superseded_at": null,
  "retired_at": null,
  "chunk_strategy": {},
  "embedding_model": "BAAI/bge-small-en-v1.5",
  "embedding_revision": "…",
  "embedding_dimension": 384,
  "chunk_count": 42,
  "trace_id": "uuid",
  "error_code": null,
  "started_at": "…",
  "finished_at": "…"
}
```

### `GET /processing-runs/{run_id}/chunks`

Query `include_vector` boolean, default false. Page of `ChunkRead`.

### `GET /chunks/{chunk_id}`

Query `include_vector`. Response `ChunkRead`.

### `POST /documents/{document_id}/reprocess`

Editor. Optional body `ChunkConfig`. Creates a processing run on the serving version and sets `pending_run_id`. Response `202` `{ "processing_run_id", "trace_id", "job_id" }`. Returns `409 ingest_in_progress` when a pending run exists. Unlike `force` on replacement upload, reprocess **may** target a serving version that is already extracted (for example after chunk-config changes). The serving version stays active until `activate` commits.

### `POST /documents/{document_id}/rollback`

Editor. Reactivates the immediately previous ready run when `retired_at` is null, using the activation transaction. Response `200` `DocumentRead`. Response `409 version_retired` when that run was already retired. Response `422 unprocessable` when there is no previous ready run.

### `DELETE /documents/{document_id}`

Editor. Response `202` `{ "job_id" }`. Document disappears from lists immediately.

## Conversations and chat

### `GET /knowledge-bases/{kb_id}/conversations`

Page of `{ "id", "title", "updated_at" }`.

### `POST /knowledge-bases/{kb_id}/conversations`

Request `{ "title"? }`. Response `201`.

### `GET /conversations/{conversation_id}`

`{ "id", "knowledge_base_id", "title", "created_at", "updated_at" }`.

### `PATCH /conversations/{conversation_id}`

`{ "title" }`.

### `DELETE /conversations/{conversation_id}`

`204`. Soft delete.

### `GET /conversations/{conversation_id}/messages`

Page, ascending by `created_at`. Message:

```json
{
  "id": "uuid",
  "role": "assistant",
  "content": "…",
  "status": "complete",
  "trace_id": "uuid",
  "citations": [],
  "created_at": "…"
}
```

### `POST /conversations/{conversation_id}/messages`

Request:

```json
{
  "content": "How does hybrid search work?",
  "mode": "answer",
  "document_ids": [],
  "retrieval": null
}
```

`mode` is `answer` (default), `summarize`, or `compare`. `summarize` requires one id in `document_ids`. `compare` requires two or more. `retrieval`, when present, overrides knowledge-base defaults for this run only.

Behavior:

1. Insert the user message.
2. Insert an assistant message with `status: pending` and empty content.
3. Create a `query` trace and attach it to the assistant message.
4. Start execution (background task on the API process, not the ingest worker, so streams stay local).

Response `202`:

```json
{
  "user_message_id": "uuid",
  "assistant_message_id": "uuid",
  "trace_id": "uuid"
}
```

If `mode` is `answer` and the model is not configured, still return `202` and fail the assistant message with `llm_not_configured` after retrieval is traced. The client learns this on the stream. Do not skip retrieval.

### `GET /messages/{assistant_message_id}/stream`

SSE. `Content-Type: text/event-stream`.

Events:

```text
event: stage
data: {"stage":"vector_search","status":"succeeded","sequence":3}

event: token
data: {"text":"Hybrid"}

event: citation
data: {"label":"S1","document_id":"…","excerpt":"…","locator":{}}

event: done
data: {"message_id":"…","trace_id":"…","status":"complete"}

event: error
data: {"code":"llm_failed","message":"…"}
```

Reconnecting replays persisted tokens by sending a `snapshot` event first (`content` so far, stages already finished) and then live tokens. Heartbeat comments every 15 seconds.

### `GET /messages/{message_id}/citations`

`{ "items": [ CitationRead ] }`. Every item’s `label` is an exact member of the `build_context` label set on `trace_id`. Rejected labels are not included; they are on `GET /traces/{trace_id}/events/{event_id}` for `map_citations`.

## Search

### `POST /knowledge-bases/{kb_id}/search`

Does not call the language model.

```json
{
  "query": "reciprocal rank fusion",
  "document_ids": [],
  "retrieval": null
}
```

Response `200`:

```json
{
  "trace_id": "uuid",
  "lexical_corpus": {"n": 840, "avgdl": 312.4, "k1": 1.5, "b": 0.75},
  "hits": [
    {
      "chunk_id": "uuid",
      "document_id": "uuid",
      "document_title": "design.pdf",
      "text": "…",
      "locator_start": {},
      "locator_end": {},
      "scores": {
        "vector": 0.77,
        "vector_rank": 1,
        "bm25": 3.1,
        "bm25_rank": 4,
        "rrf": 0.031,
        "rerank": 1.8,
        "rerank_rank": 1
      }
    }
  ]
}
```

Order is the final stage that ran (rerank, else RRF, else vector). `lexical_corpus` is null when `lexical_search` was skipped. When it is present, `n` and `avgdl` are the active-chunk statistics from that trace event.

## Pipeline playground

Playground runs use the same services as chat. This route exists so a run can be started without creating a conversation.

### `POST /knowledge-bases/{kb_id}/pipeline/runs`

```json
{
  "question": "Where is BM25 defined?",
  "generate": true,
  "document_ids": [],
  "retrieval": {
    "hybrid_enabled": true,
    "rerank_enabled": true,
    "context_k": 5
  }
}
```

Response `202` `{ "trace_id", "assistant_message_id": null }`. When `generate` is true and a throwaway transcript is useful, the server may omit conversation persistence. Generation output is stored on the trace event `generate.output.text` either way, so the inspector does not depend on a conversation.

### `GET /pipeline/runs/{trace_id}/stream`

Same SSE shape as message stream. `token` events appear only when generation ran.

## Traces

### `GET /knowledge-bases/{kb_id}/traces`

Query `kind=ingestion|query`. Page of:

```json
{
  "id": "uuid",
  "kind": "query",
  "question": "…",
  "status": "complete",
  "started_at": "…",
  "finished_at": "…"
}
```

### `GET /traces/{trace_id}`

Header plus `events: TraceEventRead[]` (previews only) and immutable `config`. `config` includes the retrieval object, embedding model and revision, reranker id, BM25 `k1` and `b`, and `prompt_template_version`. `error_code` is set when the trace `status` is `failed`.

### `GET /traces/{trace_id}/events/{event_id}`

`TraceEventDetail` with full `input` and `output`. For `build_context`, `output.labels` is the citation allow-list. For `map_citations`, `output` includes `accepted_labels` and `rejected_labels`. For `lexical_search`, `output` includes `n`, `avgdl`, and per-term `df`. For `vector_index`, `output` includes `generation` and the count of mappings moved to `committed`. A failed event includes `error_code`, `error_detail`, `started_at`, `ended_at`, and `duration_ms`.

## Analytics

All are knowledge-base scoped and read-only.

### `GET /knowledge-bases/{kb_id}/analytics/overview`

```json
{
  "document_count": 12,
  "ready_count": 10,
  "failed_count": 1,
  "chunk_count": 840,
  "conversation_count": 6,
  "query_count_7d": 18,
  "median_query_latency_ms_7d": 940
}
```

### `GET /knowledge-bases/{kb_id}/analytics/usage?days=14`

```json
{
  "points": [
    {"date": "2026-11-01", "queries": 2, "ingestions": 1}
  ]
}
```

### `GET /knowledge-bases/{kb_id}/analytics/citations?limit=10`

```json
{
  "items": [
    {"document_id": "uuid", "title": "design.pdf", "citation_count": 7}
  ]
}
```

### `GET /knowledge-bases/{kb_id}/analytics/ingestion`

```json
{
  "by_status": {"ready": 10, "failed": 1, "queued": 0},
  "by_source_type": {"pdf": 4, "md": 3},
  "recent_failures": [
    {"document_id": "uuid", "title": "scan.pdf", "error_code": "pdf_unreadable"}
  ]
}
```

### `GET /knowledge-bases/{kb_id}/analytics/latency?days=14`

```json
{
  "points": [
    {"date": "2026-11-12", "median_ms": 880, "p95_ms": 1400}
  ]
}
```

## Evaluation

### `POST /knowledge-bases/{kb_id}/evaluations`

`{ "name" }` → `201` `{ "id", "name", "created_at" }`.

### `GET /knowledge-bases/{kb_id}/evaluations`

Page of those objects plus `case_count`.

### `POST /evaluations/{evaluation_id}/cases`

```json
{
  "question": "What fusion method is used?",
  "expected_document_ids": ["uuid"],
  "expected_chunk_ids": [],
  "reference_answer": null
}
```

### `GET /evaluations/{evaluation_id}`

Evaluation plus cases.

### `POST /evaluations/{evaluation_id}/runs`

```json
{
  "label": "hybrid+rerank",
  "retrieval": {},
  "generate": false
}
```

Response `202` `{ "run_id", "job_id" }`.

### `GET /evaluations/{evaluation_id}/runs`

```json
{
  "items": [
    {
      "id": "uuid",
      "label": "hybrid+rerank",
      "status": "succeeded",
      "metrics": {
        "hit_at_5": 0.8,
        "mrr": 0.62,
        "mean_latency_ms": 410,
        "case_count": 10
      }
    }
  ]
}
```

### `GET /evaluation-runs/{run_id}`

Run header, metrics, and per-case results:

```json
{
  "case_id": "uuid",
  "question": "…",
  "hit": true,
  "reciprocal_rank": 1.0,
  "latency_ms": 220,
  "retrieved_chunk_ids": [],
  "answer": null
}
```

## Settings

### `GET /settings/providers`

```json
{
  "embedding": {
    "model": "BAAI/bge-small-en-v1.5",
    "ready": true
  },
  "reranker": {
    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "ready": true
  },
  "language_model": {
    "provider": "gemini",
    "model": "configured-at-phase-4",
    "ready": false
  }
}
```

Never return API keys or key fragments.

### `GET /health`

Unauthenticated. `{ "status": "ok", "database": "ok" }`.

## Authorization matrix

Enforced in dependencies, not only in the UI.

| Route group | viewer | editor | owner |
| --- | --- | --- | --- |
| Read KB, documents, chunks, chat, search, traces, analytics, evaluations | yes | yes | yes |
| Create chats and search | yes | yes | yes |
| Upload, URL, GitHub, reprocess, rollback, delete document, patch retrieval config | no | yes | yes |
| Members, delete KB | no | no | yes |

Evaluation case writes are editor and owner.

## Streaming and idempotency

`POST /messages` is not idempotent. The client disables the composer until `done` or `error`. A query left `running` is closed as `execution_lost` on API startup and is not regenerated.

Document upload uses the content hash: a repeat upload of the same bytes to the same document returns `200` with the existing version and `reused: true` unless the form field `force=true` on a **replacement** upload (`POST /documents/{id}/versions`), which schedules a new ingest run on the **highest `version_number` with matching `content_hash`** for that document (not necessarily the serving version). `force` on `POST /knowledge-bases/{id}/documents` is rejected with `400`. First upload always creates a document (`201`). `POST /documents/{id}/reprocess` always targets **`current_version_id`** (the serving version).

Ingest jobs use idempotency key `processing_run:{run_id}`. Repeating enqueue while that job is `pending` or `running` returns `409 ingest_in_progress`. User retries (`force` or reprocess) return `422 retry_limit_reached` when the next `user_retry_count` would exceed `user_retry_limit` (default **3** per document version; the initial enqueue uses `user_retry_count = 0`). User retry is rejected when the version is already the extracted preview (`422 unprocessable`) or after `inconsistent_state` (`422 unprocessable`). `inconsistent_state` failures are not user-retryable.

**Automatic worker retries** (transient errors such as `storage_unavailable`, `worker_lost`) do not increment `user_retry_count`. Each `ingest_document` job has `max_attempts = 4`: one initial claim plus three automatic retries with `run_after` delays of **10s**, **30s**, and **90s**. Permanent errors fail on the first claim with no automatic retry. When automatic attempts are exhausted, the processing run is `failed`, pending pointers are cleared, and the serving version is unchanged.

## CORS and cookies

The browser sends the bearer token from memory. Refresh token is also returned in the body for the SPA to store in memory for v1, not in `localStorage`. A later hardening pass may move refresh to an `HttpOnly` cookie. That change updates this spec and D15 together.
