# RAGFlow — Database Design

**Last updated:** 2026-11-12. Phase 2 Milestone 1 schema is implemented in migration `0002_phase2_ingestion_foundation`. Phase 2 adds `preview_version_id`, upload `dedup_hash`, and `storage_state` (`incoming` / `stored`). Phase 3 indexing tables below are not migrated yet.
**Engine:** PostgreSQL 16
**Migrations:** Alembic, one migration per phase that changes schema. Phase 1 revision: `apps/api/alembic/versions/0001_phase1_identity.py` (`0001_phase1_identity`). It creates `users`, `refresh_tokens`, `knowledge_bases`, and `knowledge_base_members`.

Original files and FAISS indexes are not stored in PostgreSQL. Chunk text is stored in PostgreSQL so the playground can query it. Embeddings as float arrays are not stored in PostgreSQL; the FAISS file is the vector store, and `vector_mappings` joins integer ids to chunks.

Primary keys are UUIDs (`uuid`, generated in the application) unless noted. `vector_id` is `bigint` because FAISS ids are integers. Timestamps are `timestamptz`, set in the database with `now()` where possible.

Soft delete uses `deleted_at`. Queries that serve product reads add `deleted_at is null` unless the route is an explicit archive.

## Enumerations

Stored as Postgres enum types or as text with a check constraint. Prefer text plus check so Alembic changes stay simple:

- `member_role`: `owner`, `editor`, `viewer`
- `source_type`: `pdf`, `docx`, `txt`, `md`, `pptx`, `csv`, `xlsx`, `json`, `web`, `github`
- `document_status` (serving, on `documents`): `queued`, `extracted`, `ready`, `failed`
- `run_status` (on `processing_runs`): `queued`, `extracting`, `normalizing`, `chunking`, `embedding`, `indexing`, `activating`, `extracted`, `ready`, `failed`
- `block_type`: `heading`, `paragraph`, `list_item`, `table`, `code`, `caption`
- `job_status`: `pending`, `running`, `succeeded`, `failed`
- `message_role`: `user`, `assistant`, `system`
- `message_status`: `pending`, `streaming`, `complete`, `failed`
- `trace_kind`: `ingestion`, `query`
- `stage_status`: `running`, `succeeded`, `failed`, `skipped`

`documents.status` is the serving/preview status. `queued` has no committed blocks. `extracted` means `preview_version_id` is set and `current_version_id` is null. `ready` means a serving version exists. An in-flight replacement is `pending_run_id` plus that run’s `status`. A ready document stays `ready` while a new version is extracting. A failed attempt clears pending pointers only when they still refer to that run. It leaves `current_version_id` and the active serving run unchanged. If `current_version_id` is null but `preview_version_id` is set, the document stays `extracted` and that preview is kept. `failed` is set only when both the serving version and the preview are null.

## Relationships

```text
users 1──* refresh_tokens
users 1──* knowledge_bases          (owner_id)
users *──* knowledge_bases          via knowledge_base_members

knowledge_bases 1──* documents
documents 1──* document_versions
documents.current_version_id → document_versions.id

document_versions 1──* canonical_blocks
document_versions 1──* processing_runs
processing_runs 1──* chunks

knowledge_bases 1──* vector_indexes
vector_indexes 1──* vector_mappings *──1 chunks

knowledge_bases 1──* conversations 1──* messages 1──* message_citations
message_citations.chunk_id → chunks.id   (nullable after purge)

query_traces 1──* trace_events
processing_runs.trace_id → query_traces.id
messages.trace_id → query_traces.id

knowledge_bases 1──* jobs            (optional kb scope)

evaluations 1──* evaluation_cases
evaluations 1──* evaluation_runs 1──* evaluation_run_results

entities 1──* entity_mentions *──1 chunks
entity_relationships.subject_id → entities
entity_relationships.object_id → entities
```

## Locator JSON

`canonical_blocks.locator`, `chunks.locator_start`, `chunks.locator_end`, and `message_citations.locator` share this object. Absent fields are null or omitted.

```json
{
  "source_type": "pdf",
  "page": 4,
  "bbox": {"x0": 72.0, "y0": 140.2, "x1": 540.0, "y1": 188.6},
  "heading_path": ["Methods", "Retrieval"],
  "slide": null,
  "sheet": null,
  "row": null,
  "cell": null,
  "json_path": null,
  "url": null,
  "file_path": null,
  "start_line": null,
  "end_line": null,
  "char_start": 0,
  "char_end": 812
}
```

## Tables

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| email | citext unique not null | |
| password_hash | text not null | Argon2 |
| display_name | text not null | |
| created_at | timestamptz not null | |
| updated_at | timestamptz not null | |

### `refresh_tokens`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| user_id | uuid fk users not null | on delete cascade |
| token_hash | text not null unique | SHA-256 of the secret |
| expires_at | timestamptz not null | |
| revoked_at | timestamptz null | |
| created_at | timestamptz not null | |

Index: `(user_id, expires_at)`.

### `knowledge_bases`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| owner_id | uuid fk users not null | |
| name | text not null | |
| description | text not null default `''` | |
| retrieval_config | jsonb not null | defaults from D10 |
| chunk_config | jsonb not null | defaults from architecture |
| created_at | timestamptz not null | |
| updated_at | timestamptz not null | |
| deleted_at | timestamptz null | |

Index: `(owner_id)` where `deleted_at is null`.

### `knowledge_base_members`

| Column | Type | Notes |
| --- | --- | --- |
| knowledge_base_id | uuid fk | |
| user_id | uuid fk | |
| role | text not null | check constraint |
| created_at | timestamptz not null | |

Primary key `(knowledge_base_id, user_id)`. Index `(user_id)`.

### `documents`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| title | text not null | display name; original filename or URL |
| source_type | text not null | |
| source_uri | text null | URL or `git@host:owner/repo` canonical https URL |
| status | text not null | `queued`, `extracted`, `ready`, or `failed` |
| error_code | text null | set when status is `failed` (no serving version and no extracted preview) |
| error_detail | text null | |
| current_version_id | uuid null | serving version; updated only in the activation transaction (Phase 3) |
| preview_version_id | uuid null | latest version whose canonical blocks committed (Phase 2 worker); Phase 2A upload does not set this |
| pending_version_id | uuid null | version being ingested; null when idle |
| pending_run_id | uuid null | in-flight run; at most one; paired with `pending_version_id` |
| created_by | uuid fk users not null | |
| created_at | timestamptz not null | |
| updated_at | timestamptz not null | |
| deleted_at | timestamptz null | |

Indexes:

- `(knowledge_base_id, created_at desc)` where `deleted_at is null`
- `(knowledge_base_id, status)` where `deleted_at is null`

### `document_versions`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| document_id | uuid fk not null | |
| version_number | int not null | starts at 1 |
| content_hash | text null until stored | SHA-256 of bytes |
| dedup_hash | text null | upload dedup key (`= content_hash` for file uploads; null for URL/GitHub) |
| storage_path | text not null | relative to `STORAGE_DIR`; may point at `incoming/{uuid}` before promotion |
| storage_state | text not null | `incoming` or `stored` |
| byte_size | bigint not null | |
| mime_type | text not null | detected, not trusted from the client |
| original_filename | text null | display only |
| created_at | timestamptz not null | |

Unique `(document_id, version_number)`. Unique `(document_id, content_hash)` where `content_hash IS NOT NULL`. Unique `(knowledge_base_id, dedup_hash)` where `dedup_hash IS NOT NULL` (upload dedup only).

`documents.current_version_id` references `document_versions.id`. The foreign key is `DEFERRABLE` or added after insert so the first version can point both ways inside one transaction.

### `canonical_blocks`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| document_version_id | uuid fk not null | on delete cascade |
| sequence | int not null | |
| block_type | text not null | |
| text | text not null | |
| locator | jsonb not null | `source_type` plus only the fields that apply; TXT/Markdown use 1-based `start_line` and `end_line`, and Markdown adds `heading_path` |
| metadata | jsonb not null default `{}` | extractor flags such as `quote` and `language`; `{}` when none |

Unique `(document_version_id, sequence)`. Index `(document_version_id)`.

### `processing_runs`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| document_id | uuid fk not null | denormalized so one active run can be indexed |
| document_version_id | uuid fk not null | |
| knowledge_base_id | uuid fk not null | denormalized for workers |
| status | text not null | includes `activating` before `ready` or `failed` |
| is_active | boolean not null default false | serving run |
| chunk_strategy | jsonb not null | |
| embedding_model | text null | set when embedding starts |
| embedding_revision | text null | |
| embedding_dimension | int null | |
| chunk_count | int not null default 0 | |
| error_code | text null | |
| error_detail | text null | |
| trace_id | uuid null | fk query_traces |
| superseded_at | timestamptz null | set when a newer run becomes active |
| retired_at | timestamptz null | set when vectors and postings are removed |
| started_at | timestamptz null | |
| finished_at | timestamptz null | |
| created_at | timestamptz not null | |

Indexes:

- unique partial: `(document_id) WHERE is_active` — at most one serving run per document
- `(knowledge_base_id, status)`
- `(document_version_id, created_at desc)`
- `(document_id, created_at desc)`

### `chunks`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| processing_run_id | uuid fk not null | |
| document_version_id | uuid fk not null | |
| knowledge_base_id | uuid fk not null | |
| document_id | uuid fk not null | |
| sequence | int not null | |
| text | text not null | includes heading prefix when present |
| token_count | int not null | |
| block_start_id | uuid fk canonical_blocks not null | |
| block_end_id | uuid fk canonical_blocks not null | |
| locator_start | jsonb not null | |
| locator_end | jsonb not null | |
| metadata | jsonb not null default `{}` | `heading_prefix`, strategy, char splits |
| created_at | timestamptz not null | |

Unique `(processing_run_id, sequence)`. Index `(knowledge_base_id, document_id)`. Index `(document_version_id)`.

Chunks for inactive and retired runs remain so an old ingestion trace can still show chunk text. Query retrieval joins only the active run. There is no `deleted_at` on `chunks`; purge deletes the rows after mappings and postings are gone.

### `vector_indexes`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| embedding_model | text not null | |
| embedding_revision | text not null | |
| dimension | int not null | |
| metric | text not null | `ip` for v1 |
| faiss_path | text not null | relative to `INDEX_DIR` |
| generation | bigint not null default 0 | bumped in the mapping-commit transaction |
| vector_count | int not null default 0 | count of `committed` mappings |
| next_vector_id | bigint not null default 1 | allocated by the writer; never reused |
| created_at | timestamptz not null | |

Unique `(knowledge_base_id, embedding_model, embedding_revision, dimension, metric)`.

### `vector_mappings`

| Column | Type | Notes |
| --- | --- | --- |
| index_id | uuid fk not null | |
| vector_id | bigint not null | |
| chunk_id | uuid fk chunks not null | |
| state | text not null | `staged`, `committed`, or `retired` |
| l2_norm | double precision null | set when the sidecar row is written; required before commit |
| created_at | timestamptz not null | |

Primary key `(index_id, vector_id)`. Unique `(index_id, chunk_id)`. Index `(index_id, state)`.

Embedding floats are not a column. They live in the run sidecar named in `ARCHITECTURE.md`. Inspectable metadata is model, revision, dimension, `vector_id`, `state`, and L2 norm. Search joins neighbors only to rows with `state = committed`. `staged` rows are an incomplete publish. `retired` rows remain as history after the id is removed from FAISS.

### `chunk_stats`

| Column | Type | Notes |
| --- | --- | --- |
| chunk_id | uuid pk fk chunks | |
| knowledge_base_id | uuid not null | |
| token_count | int not null | BM25 `dl`: tokenizer output length, not the embedding token count |
| processing_run_id | uuid not null | |

### `lexical_postings`

| Column | Type | Notes |
| --- | --- | --- |
| knowledge_base_id | uuid not null | |
| term | text not null | tokenizer output |
| chunk_id | uuid fk not null | |
| processing_run_id | uuid not null | joined to `is_active` for `df` and search |
| term_frequency | int not null | count inside the chunk; retries replace the row |

Primary key `(knowledge_base_id, term, chunk_id)`.

Index `(chunk_id)` for deletion. Index `(processing_run_id)`. The primary key prefix `(knowledge_base_id, term)` serves lookup. `df(t)` counts rows for that term whose run `is_active` is true and whose document is not deleted. A retry deletes postings for the chunk and inserts replacements in one transaction.

### `lexical_corpus_stats`

| Column | Type | Notes |
| --- | --- | --- |
| knowledge_base_id | uuid pk | |
| chunk_count | int not null | `N`, active chunks only |
| token_sum | bigint not null | sum of active `dl` |
| avg_token_length | double precision not null | `token_sum / chunk_count`, or `0` when `N = 0` |
| updated_at | timestamptz not null | |

Rewritten in the activation transaction and in the delete transaction that clears `is_active`. Writing postings for an inactive run does not change these numbers. `df` is not cached in v1; the active-posting count is the definition, and each lexical trace event stores the `df` values it used.

### `conversations`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| created_by | uuid fk users not null | |
| title | text not null | first question, editable |
| created_at | timestamptz not null | |
| updated_at | timestamptz not null | |
| deleted_at | timestamptz null | |

Index `(knowledge_base_id, updated_at desc)` where `deleted_at is null`.

### `messages`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| conversation_id | uuid fk not null | |
| role | text not null | |
| content | text not null default `''` | assistant content grows during streaming |
| status | text not null | |
| trace_id | uuid null | |
| error_code | text null | |
| created_at | timestamptz not null | |

Index `(conversation_id, created_at)`.

### `message_citations`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| message_id | uuid fk not null | |
| label | text not null | `S1` |
| chunk_id | uuid null | null if purged |
| document_id | uuid null | |
| excerpt | text not null | copied at generation time |
| locator | jsonb not null | |
| scores | jsonb not null | vector, bm25, rrf, rerank as available |
| source_deleted | boolean not null default false | |

Unique `(message_id, label)`. Index `(message_id)`. Index `(document_id)`.

### `query_traces`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| kind | text not null | `ingestion` or `query` |
| user_id | uuid null | |
| conversation_id | uuid null | |
| message_id | uuid null | |
| processing_run_id | uuid null | ingestion |
| question | text null | query text or file name |
| status | text not null | |
| config | jsonb not null | immutable snapshot taken when the trace is created |
| error_code | text null | copied from the failed stage, if any |
| started_at | timestamptz not null | |
| finished_at | timestamptz null | |

Indexes: `(knowledge_base_id, started_at desc)`, `(message_id)`, `(processing_run_id)`.

### `trace_events`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| trace_id | uuid fk not null | on delete cascade |
| stage | text not null | |
| sequence | int not null | |
| attempt | int not null default 1 | increases when the stage is retried |
| status | text not null | `running`, `succeeded`, `failed`, `skipped` |
| skip_reason | text null | required when `status = skipped` |
| started_at | timestamptz not null | `clock_timestamp()` at start |
| ended_at | timestamptz null | `clock_timestamp()` at finish |
| duration_ms | int null | `0` for skipped stages; set when the stage finishes |
| params | jsonb not null default `{}` | stage configuration |
| input | jsonb not null default `{}` | |
| output | jsonb not null default `{}` | |
| error_code | text null | |
| error_detail | text null | |

Unique `(trace_id, sequence)`. Index `(trace_id, stage, attempt)`.

Events are append-only apart from closing a `running` row. A retry inserts a new sequence. A finished ingestion or query trace contains every stage name in the pipeline list, with skipped rows where a stage did not run. `build_context.output.labels` is the citation allow-list. `map_citations.output.rejected_labels` lists strings the model emitted that were not in that set. `lexical_search.output` includes `n`, `avgdl`, `k1`, `b`, and per-hit `matched_terms` with `tf`, `df`, and `idf`.

`input` and `output` hold inspectable JSON. For `vector_search`, `output.hits` is a list of `{chunk_id, vector_id, score, rank}`. For `lexical_search`, hits include `{chunk_id, score, rank, matched_terms: [{term, tf, idf}]}`. For `build_prompt`, `output.prompt` is the full string and `output.prompt_template_version` is set. For `embed` on ingestion, output includes `{model, revision, dimension, chunk_count}` and not every float.

### `jobs`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| job_type | text not null | |
| idempotency_key | text not null | see architecture job table |
| knowledge_base_id | uuid null | |
| payload | jsonb not null | includes `processing_run_id` for ingest |
| status | text not null | `pending`, `running`, `succeeded`, `failed` |
| attempts | int not null default 0 | incremented when the row is claimed |
| max_attempts | int not null default 3 | `ingest_document` enqueue sets **4** (1 claim + 3 transient retries). Other job types may use `1` for permanent-only handlers. |
| run_after | timestamptz not null | |
| locked_at | timestamptz null | heartbeat, refreshed at least every 30 seconds |
| lock_timeout_seconds | int not null default 900 | expired `running` rows may be claimed |
| locked_by | text null | worker id |
| last_error_code | text null | |
| last_error | text null | |
| created_at | timestamptz not null | |
| updated_at | timestamptz not null | |

Indexes:

- `(status, run_after)` where `status = 'pending'`
- unique `(job_type, idempotency_key)` where `status in ('pending', 'running')`
- `(status, locked_at)` where `status = 'running'`

### `evaluations`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| name | text not null | |
| created_by | uuid fk users not null | |
| created_at | timestamptz not null | |

### `evaluation_cases`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| evaluation_id | uuid fk not null | |
| question | text not null | |
| expected_document_ids | uuid[] not null | |
| expected_chunk_ids | uuid[] null | optional, stricter |
| reference_answer | text null | not required for Hit@k |
| position | int not null | |

### `evaluation_runs`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| evaluation_id | uuid fk not null | |
| label | text not null | experiment name |
| config | jsonb not null | frozen retrieval config and model ids |
| metrics | jsonb null | `{hit_at_5, mrr, mean_latency_ms, ...}` |
| status | text not null | |
| started_at | timestamptz null | |
| finished_at | timestamptz null | |
| created_at | timestamptz not null | |

### `evaluation_run_results`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| run_id | uuid fk not null | |
| case_id | uuid fk not null | |
| retrieved_chunk_ids | uuid[] not null | |
| hit | boolean not null | |
| reciprocal_rank | double precision not null | |
| latency_ms | int not null | |
| answer | text null | present only if the run enabled generation |

Unique `(run_id, case_id)`.

### `entities`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| name | text not null | surface form chosen as display |
| normalized_name | text not null | lowercased |
| entity_type | text not null | spaCy label |
| created_at | timestamptz not null | |

Unique `(knowledge_base_id, entity_type, normalized_name)`.

### `entity_mentions`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| entity_id | uuid fk not null | |
| chunk_id | uuid fk not null | |
| locator | jsonb not null | |

Index `(entity_id)`, `(chunk_id)`.

### `entity_relationships`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid pk | |
| knowledge_base_id | uuid fk not null | |
| subject_id | uuid fk entities not null | |
| object_id | uuid fk entities not null | |
| predicate | text not null | v1 uses `co_occurs` |
| evidence_chunk_id | uuid fk chunks not null | |
| confidence | double precision not null | |
| created_at | timestamptz not null | |

Index `(knowledge_base_id)`, `(subject_id)`, `(object_id)`.

## Integrity rules

The partial unique index guarantees one `is_active` run per document. Services and tests also lock these rules:

1. `current_version_id` changes only in the activation transaction that flips `is_active`.
2. Query results come from the active run of `current_version_id` on a document that is not deleted. FAISS hits must join to `vector_mappings.state = committed`.
3. `vector_mappings.chunk_id` belongs to the same knowledge base as the index. A mapping is `committed` only after its sidecar file is in place.
4. `lexical_corpus_stats` matches the active chunk set after every activation and serving delete.
5. A citation label is inserted only when it is an exact member of `build_context.output.labels` for that trace.
6. Purge never removes `message_citations.excerpt`.
7. Two live jobs with the same `(job_type, idempotency_key)` cannot exist.

## Migration phases

| Phase | Schema introduced |
| --- | --- |
| 1 | `users`, `refresh_tokens`, `knowledge_bases`, `knowledge_base_members` |
| 2 | `documents`, `document_versions`, `canonical_blocks`, `processing_runs`, `jobs`, `query_traces`, `trace_events` |
| 3 | `chunks`, `vector_indexes`, `vector_mappings`, `chunk_stats`, `lexical_postings`, `lexical_corpus_stats` |
| 4 | `conversations`, `messages`, `message_citations` |
| 5 | no new tables; retrieval config already on `knowledge_bases` |
| 6 | no new tables; playground reads `trace_events` from Phase 2 |
| 7 | `entities`, `entity_mentions`, `entity_relationships` |
| 8 | `evaluations`, `evaluation_cases`, `evaluation_runs`, `evaluation_run_results` |

`query_traces` and `trace_events` are created in Phase 2 so extraction and later index stages share one trace log. Phase 4 adds conversation foreign keys onto traces that already exist.
