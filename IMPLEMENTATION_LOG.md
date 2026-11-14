# RAGFlow — Implementation Log

**Last updated:** 2026-11-12. Phase 1 application code exists under `apps/`.

This log records work that actually happened. It does not copy the roadmap’s intentions. A phase is not complete until acceptance checks are listed here with results.

## 2026-11-12 — Project folder and planning documents

**Location.** `/Users/abirami/Desktop/ragflow`

**Checked first.** The folder did not exist. It was created. Git was initialized. No pre-existing project files were modified.

**Created.**

- `PROJECT_OVERVIEW.md`
- `ARCHITECTURE.md`
- `DATABASE_DESIGN.md`
- `API_SPECIFICATION.md`
- `IMPLEMENTATION_ROADMAP.md`
- `IMPLEMENTATION_LOG.md`
- `REVIEW_LOG.md`
- `DECISIONS.md`
- `UI_UX_GUIDELINES.md`

**Not created.** Application code, dependency manifests, Docker Compose, environment files, migrations, tests.

**Tests.** None. There is no software to run.

**Phase status.**

| Phase | Status |
| --- | --- |
| 0 Architecture and project foundation | In review. Documents are drafted. Waiting for explicit approval before any application code. |
| 1–9 | Not started. |

## 2026-11-12 — Consistency review of the plan

**Requested.** Refine the existing design for FAISS/Postgres recovery, atomic version activation, job idempotency, BM25 corpus rules, citation allow-lists, complete traces, and URL address checks. Do not implement.

**Updated.**

- `ARCHITECTURE.md`
- `DATABASE_DESIGN.md`
- `API_SPECIFICATION.md`
- `IMPLEMENTATION_ROADMAP.md`
- `DECISIONS.md` (D7–D9, D12, D14, D18 refined; D27 and D28 added)
- `REVIEW_LOG.md` (R2, F11–F18)
- `IMPLEMENTATION_LOG.md`

**Not created.** Application code, schemas in code, migrations, tests, or extra documentation files.

**Tests.** None. Phase 0 remains unapproved.

**Pending at that point.** User approval before implementation. Approval arrived as the Phase 1 request.

## 2026-11-12 — Phase 1 foundation

**Implemented.**

- `apps/api`: FastAPI under `/api/v1`, config, logging, error envelope, SQLAlchemy models, Alembic `0001_phase1_identity`, health, register, login, refresh rotation, logout, `/auth/me`, knowledge-base CRUD, membership, provider readiness.
- `apps/web`: Vite, React, TypeScript, Tailwind tokens, React Router, TanStack Query, a restyled dialog sheet. Nine routes, auth in memory, empty states with no sample documents or scores.
- `docker-compose.yml`, Dockerfiles, `.env.example`, `.gitignore`, `README.md`.

**Decisions applied.** D15 refresh tokens, D29 non-member `404` and insufficient role `403`, D30 synchronous soft delete. Search and Evaluations are included because the roadmap and UI guidelines require nine pages. They are empty routes.

**Migration.** `alembic upgrade head` applied to the local `ragflow` database and, during tests, to `ragflow_test`.

**Commands.**

```bash
brew install postgresql@16
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 -l /tmp/ragflow-pg.log start
cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd apps/web && npm install
```

**Tests actually run.**

```text
apps/api: DATABASE_URL=postgresql+psycopg://abirami@localhost:5432/ragflow_test .venv/bin/pytest
8 passed, 1 warning in 0.81s
Warning: StarletteDeprecationWarning about httpx versus httpx2. Tests passed.

apps/web: npm test
Test Files  2 passed (2)
Tests  7 passed (7)
npx tsc --noEmit exited 0.
```

Live API, after migration of database `ragflow`: `GET /api/v1/health` returned `{"status":"ok","database":"ok"}`. Register, login, `/auth/me`, knowledge-base create, and refresh succeeded against `http://127.0.0.1:8000`.

**Browser.** The register page at `http://127.0.0.1:5173/register` shows the ivory ground, Instrument Serif title, cream panel, and charcoal button. A signed-in browser walk was not completed. The shell test covers all nine routes.

**Docker.** Compose files are written. `docker` is not installed on this machine, so `docker compose up` was not run.

**Not implemented.** Ingestion, embeddings, FAISS, BM25, chat, citations, pipeline execution, analytics calculations, the worker.

**Phase status.**

| Phase | Status |
| --- | --- |
| 0 | Complete. The implementation request approved the plan. |
| 1 | Complete. Acceptance checks above passed, with the 403 rule clarified by D29 and Docker Compose not executed here. |
| 2–9 | Not started. |

**Known limitations.**

- Tokens live in memory. Reloading the browser signs the user out.
- `document_count` and `ready_count` are `0` until document tables exist.
- Provider readiness is always `ready: false`.
- Knowledge-base delete does not enqueue a purge job.
- The dev server started for verification was launched before the UTC timestamp serializer; restart `uvicorn` to pick that up.
- npm reported 3 audit findings in the web dependency tree. They were not patched in this phase.

**Pending.** Independent review. Do not start Phase 2 until that review approves it.

## 2026-11-12 — Phase 1 review corrections

**Scope.** Addressed independent review findings F-01 through F-10 (excluding npm dependency upgrades). No Phase 2 work.

**Changes.**

- `JWT_SECRET` is required with no insecure default. Placeholder values from `.env.example` are rejected. `create_app()` exits when settings validation fails.
- Refresh rotation locks the refresh-token row (`SELECT … FOR UPDATE`) so concurrent refreshes yield one successor token; reuse detection unchanged.
- Pytest default `DATABASE_URL` uses `PGUSER` or `$USER` against `ragflow_test`, not a fixed Postgres role name.
- Added tests: JWT configuration, expired access tokens, malformed refresh tokens, concurrent refresh, editor/viewer membership rules, invalid KB list cursors.
- Roadmap acceptance text now states v1 browser reload signs the user out. `PROJECT_OVERVIEW.md` status updated. `UI_UX_GUIDELINES.md` documents the Phase 1 knowledge-base count line.

**Tests run (this pass).**

```text
cd apps/api && .venv/bin/pytest
18 passed, 1 warning (Starlette httpx deprecation)

cd apps/web && npm test && npx tsc --noEmit
7 passed; tsc exit 0

JWT_SECRET=$(openssl rand -hex 32) alembic current
head: 0001_phase1_identity

JWT_SECRET=replace-with-a-long-random-string python -c "from app.core.config import Settings; Settings()"
ValidationError (placeholder rejected)

env -u JWT_SECRET python -c "from app.core.config import Settings; Settings()"
ValidationError (field required)
```

**npm audit.** Three issues remain in `vitest` / `@vitest/mocker` / `tinypool` (dev-only). Not auto-fixed; see review correction notes below.

**Still open.** Docker Compose not smoke-tested here. Browser tokens remain in memory only. Vitest audit findings unresolved without a major-version bump.

## 2026-11-12 — Phase 2 Milestone 1 (database foundation)

**Scope.** PostgreSQL schema and models for documents, versions, blocks, processing runs, traces, and jobs per the approved Phase 2 plan ([review transcript](2d6328af-63b7-4b78-87bb-f7b7d5e91298)).

**Created / modified.** `0002_phase2_ingestion_foundation` migration, `app/models/documents.py`, `tests/test_phase2_schema.py`, `tests/conftest.py` truncate list.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
33 passed (includes 18 Phase 1 tests + 15 Phase 2 schema tests), 1 warning
```

## 2026-11-12 — Phase 2 Milestone 2A (upload handling and storage)

**Implemented.**

- `app/modules/ingestion/`: validation, durable storage (`incoming` → `{kb}/{doc}/{ver}`), crash recovery, upload/replacement services, HTTP routes.
- `STORAGE_DIR` and `MAX_UPLOAD_BYTES` settings; startup storage recovery and orphan cleanup (15-minute grace).
- `python-multipart` dependency for multipart uploads.
- Root docs updated for `preview_version_id`, `dedup_hash`, `storage_state`, and upload endpoints.

**Endpoints.**

- `POST /api/v1/knowledge-bases/{kb_id}/documents`
- `POST /api/v1/documents/{document_id}/versions`
- `GET /api/v1/documents/{document_id}`

**Not implemented (2B+).** Worker claiming, extractors, canonical blocks, URL/GitHub ingestion.

## 2026-11-12 — Phase 2 Milestone 2B (durable ingestion worker)

**Scope.** PostgreSQL job worker, ingestion traces, automatic and user retry semantics, `force` on replacement uploads, `POST /documents/{id}/reprocess`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
68 passed, 2 warnings in ~4.1s
```

**Not implemented (2C+).** PDF/DOCX/… extractors, canonical blocks, chunking, indexing.

## 2026-11-12 — Phase 2 Milestone 2C.1 (TXT and Markdown canonical blocks)

**Scope.** In-memory TXT/Markdown extraction, normalization, atomic canonical-block persistence, the extracted-preview failure fix, and read-only block inspection. No later 2C formats, chunking, indexing, activation, or `current_version_id` writes.

**Behavior.**

- `txt` and `md` decode UTF-8 (optional BOM) or UTF-16 with a BOM. Invalid encodings and NUL bytes fail permanently (`invalid_encoding`, `nul_in_text`). Whitespace-only output fails as `empty_content` and writes no blocks.
- TXT becomes paragraph blocks split on blank lines. Markdown handles ATX/setext headings, paragraphs, list items, fenced code, block quotes, and pipe-table body rows. Malformed fences and tables become paragraphs. `script` and `style` contents are dropped outside code fences. Locators carry `source_type`, 1-based line ranges, and, for Markdown, `heading_path`. No synthetic character offsets.
- The normalized set is inserted in one transaction after the document lock confirms the run is still pending. The first success sets `preview_version_id`, clears that run’s pending pointers, and marks the document and run `extracted` with the run inactive. `current_version_id` stays null. A replacement while a serving version is active moves only `preview_version_id`.
- `commit_ingest_failure` keeps `ready` when a serving version exists and `extracted` when only a preview exists. It sets `failed` only when both are null, and it clears pending pointers only when they still refer to that run.
- A reclaimed job whose version is already the preview and already has a dense block set inserts nothing and appends `extract` skipped with `blocks_present`.
- Success traces: `extract` and `normalize` succeeded (monotonic durations); `chunk`, `embed`, `vector_index`, `lexical_index`, and `activate` skipped with `deferred` and duration 0. Failures fail the active stage and skip the rest with `upstream_failed`. Job success stays a separate commit.

**Endpoint.** `GET /api/v1/document-versions/{version_id}/blocks` for knowledge-base members, ordered by sequence, with `limit` and cursor pagination.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
99 passed, 2 warnings in 6.04s
```

Warnings: the existing Starlette/`httpx` deprecation, and the existing `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`. `python -m compileall app tests` completed. The repository has no Ruff or mypy configuration.

**Not implemented (2C.2+).** PDF, DOCX, PPTX, CSV, XLSX, JSON, URL, and GitHub extraction. Chunking, embeddings, indexing, retrieval, chat, UI, and activation.

## 2026-11-14 — Phase 2 Milestone 2C.1 replay/idempotency fix

**Scope.** Reprocess replay when persistence committed but job success was interrupted; preserve a newer `preview_version_id` on serving-version reprocess; ready-document `inconsistent_blocks` without clobbering serving pointers.

**Behavior.**

- Finalize/publish replay treats a run as already complete when `pending_run_id` is null, target blocks are dense, and the run is `extracted` with `finished_at` set—even if another version is the preview.
- Ingest short-circuits the same finalized reprocess without re-extracting, without failure persistence, and without appending duplicate trace events when `blocks_present` skips are already recorded.
- Fresh publish no longer overwrites `preview_version_id` when reprocessing the serving version (`current_version_id`).

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
106 passed, 2 warnings in 8.50s
```

Warnings: the existing Starlette/`httpx` deprecation, and the existing `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 2 Milestone 2C.2 (PDF extraction)

**Scope.** PyMuPDF PDF adapter on the 2C.1 canonical-block pipeline. No OCR, no DOCX/other formats, no chunking or activation.

**Behavior.**

- `pdf` ingest reads bytes with PyMuPDF, emits `paragraph` and `heading` blocks with one-based `page` and PDF-point `bbox` locators (`x0`, `y0`, `x1`, `y1`).
- Heading ancestry is inferred from relative font size when the document has no usable outline (v1 does not walk the PDF outline).
- Documents over **500** pages, encrypted PDFs, malformed/unopenable files, and image-only PDFs fail permanently with `pdf_unreadable`.
- Worker persistence, idempotency/replay, trace tails, preview/current-version rules, and ready-document failure behavior are unchanged from 2C.1.

**Dependency.** `pymupdf` (AGPL-3.0); see `apps/api/THIRD_PARTY_NOTICES.md`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
114 passed, 2 warnings in 8.81s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`. PyMuPDF’s SWIG extension emits `DeprecationWarning`s on import (Python 3.14); pytest `filterwarnings` in `pyproject.toml` ignores those three upstream messages so they are not counted in the suite summary.

**Not implemented (2C.3+).** DOCX, PPTX, CSV, XLSX, JSON, URL, GitHub extraction. Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 2 Milestone 2C.3 (DOCX extraction)

**Scope.** python-docx adapter on the 2C.1 canonical-block pipeline. No PPTX or later formats, no chunking or activation.

**Behavior.**

- `docx` ingest scans the OOXML zip in memory (2,000-entry and 100 MB uncompressed caps) before python-docx opens the package.
- The walk covers `document.element.body` only: `Heading 1`–`Heading 6` as `heading`, visible list paragraphs as `list_item`, other text as `paragraph`, and one `table` block per non-empty row (`table_index` / `row_index` in metadata). Locators are `source_type: docx` and `heading_path` from Word heading styles (no page numbers).
- Headers, footers, comments, and text boxes are omitted.
- Malformed, corrupt, or oversize packages fail permanently with `docx_unreadable`. Worker persistence, idempotency/replay, trace tails, preview/current-version rules, and ready-document failure behavior are unchanged from 2C.1.

**Dependency.** `python-docx` (MIT); see `apps/api/THIRD_PARTY_NOTICES.md`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
120 passed, 2 warnings in 9.37s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

**Not implemented (2C.4+).** PPTX, CSV, XLSX, JSON, URL, GitHub extraction. Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 2 Milestone 2C.4 (PPTX extraction)

**Scope.** python-pptx adapter on the 2C.1 canonical-block pipeline. No CSV/XLSX/JSON or later formats, no chunking or activation.

**Behavior.**

- `pptx` ingest uses the same OOXML zip pre-scan as DOCX (2,000-entry and 100 MB uncompressed caps) before python-pptx opens the package.
- Slides are walked in order with 1-based `slide` locators. Title placeholders emit `heading` blocks and set per-slide `heading_path`; other text frames emit one `paragraph` per paragraph in shape order. Tables emit one `table` block per non-empty row (`table_index` / `row_index` in metadata). Speaker notes emit `caption` with `metadata.part = "notes"`.
- Group shapes are flattened in document order. Pictures and charts are skipped. Empty slides contribute no blocks; a deck with no extractable text reaches `empty_content` at normalize time.
- Malformed, corrupt, or oversize packages fail permanently with `pptx_unreadable`. Worker persistence, idempotency/replay, trace tails, preview/current-version rules, and ready-document failure behavior are unchanged from 2C.1.

**Dependency.** `python-pptx` (MIT); see `apps/api/THIRD_PARTY_NOTICES.md`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
126 passed, 2 warnings in 9.49s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

**Not implemented (2C.5+).** CSV, XLSX, JSON, URL, GitHub extraction. Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 2 Milestone 2C.5 (CSV extraction)

**Scope.** Stdlib `csv` on the 2C.1 canonical-block pipeline. No XLSX/JSON or later formats, no chunking or activation.

**Behavior.**

- `csv` ingest decodes through `decode_textual` (UTF-8 with optional BOM, UTF-16 with BOM). Invalid encodings and NUL bytes keep existing permanent codes (`invalid_encoding`, `nul_in_text`).
- Delimiter is sniffed from the first 8 KB among comma, tab, and semicolon; failure falls back to comma. The first non-empty row is the header (not emitted). Each later non-empty data row is one `table` block with `header: value` lines, `locator.sheet` set from `document_version.original_filename`, and 1-based `locator.row` at the record’s starting file line. Column names are also stored in `metadata.headers`.
- Ragged rows omit missing cells from text. Header-only files yield no blocks and fail at normalize with `empty_content`. Parse failures raise permanent `csv_unreadable`.
- Worker passes `original_filename` into extraction. Persistence, idempotency/replay, trace tails, and preview/current-version rules are unchanged from 2C.1.

**Dependency.** None (stdlib `csv` only).

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
135 passed, 2 warnings in 9.46s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

**Not implemented (2C.5 remainder+).** XLSX and JSON (same milestone tranche), URL, GitHub. Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 2 Milestone 2C.6 (XLSX extraction)

**Scope.** openpyxl adapter on the 2C.1 canonical-block pipeline. No JSON or later formats, no chunking or activation.

**Behavior.**

- `xlsx` ingest applies the same OOXML zip pre-scan as DOCX/PPTX (2,000-entry and 100 MB uncompressed caps), then opens the workbook twice (`data_only=False` and `data_only=True`) without evaluating formulas.
- Worksheets are walked in workbook order. Row 1 that is entirely non-empty strings is a header row (not emitted). Each later non-empty row becomes one `table` block with `header: value` lines when headers exist, otherwise column letters (`A`, `B`, …). Locators are `source_type: xlsx`, worksheet `sheet`, and 1-based spreadsheet `row` (`cell` is omitted for row blocks).
- Formula cells use cached values from the `data_only=True` read when present; otherwise the formula text is emitted and `metadata.formula = true`. Hidden worksheets are included with `metadata.hidden = true`. Blank rows are skipped.
- Malformed, corrupt, or oversize packages fail permanently with `xlsx_unreadable`. Worker persistence, idempotency/replay, trace tails, and preview/current-version rules are unchanged from 2C.1.

**Dependency.** `openpyxl` (MIT); see `apps/api/THIRD_PARTY_NOTICES.md`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
144 passed, 2 warnings in 10.57s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

**Not implemented (2C.7+).** JSON, URL, GitHub extraction. Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 2 Milestone 2C.7 (JSON extraction)

**Scope.** Stdlib `json` on the 2C.1 canonical-block pipeline. No URL/GitHub or later phases, no chunking or activation.

**Behavior.**

- `json` ingest decodes via `decode_textual`, parses with `object_pairs_hook` (ordered objects, duplicate keys last-value-wins with `metadata.duplicate_keys = true` on the affected object block).
- Each object’s scalar fields become one `table` block of `key: value` lines at that object’s `json_path` (`$`, `$.a`, `$.meta["weird.key"]`, …). Nested objects and arrays are walked separately. Scalar arrays under 50 elements are one block; longer arrays split in 50-element chunks with `[start:end]` path suffixes. Root scalars are a single `paragraph` at `$`.
- Path notation uses dot segments for simple identifiers and JSON-quoted bracket segments otherwise. Locators are `source_type: json`, `json_path`, and `heading_path: []`.
- Nesting deeper than **64** levels or more than **5,000** blocks fails permanently with `json_unreadable`. Invalid UTF-8/NUL keep existing codes. Worker persistence, idempotency/replay, trace tails, and preview/current-version rules are unchanged from 2C.1.

**Dependency.** None (stdlib `json` only).

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
154 passed, 2 warnings in 10.25s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

**Not implemented (2C.8+).** URL ingestion; GitHub extraction (deferred). Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 2 Milestone 2C.8 (secure URL ingestion)

**Scope.** `POST /api/v1/knowledge-bases/{kb_id}/sources/url`, worker fetch/crawl, and HTML extraction. No GitHub (deferred) or later phases, no chunking or activation.

**Behavior.**

- Enqueue-time validation allows only `http`/`https`, ports 80/443, no userinfo, and blocked hostnames (`localhost`, `*.localhost`, metadata hosts). DNS/SSRF checks run in the worker. `dedup_hash` stays null; job payload carries `source_url`, `max_pages`, and `depth`.
- Worker fetch uses manual redirects (max **5**), per-hop DNS validation (blocked loopback/private/link-local/shared/metadata ranges, mixed public+private rejection), pinned connects via stdlib `http.client`, and **httpx** transports in tests. Limits: 5s connect, 15s read, 5 MB body, HTML/XHTML/plain only. Failures: permanent `ssrf_blocked`, `redirect_limit`, `unsupported_type`; transient `fetch_failed`.
- Same-host crawl when `depth=1`, at most **20** pages. Seed failure writes no blocks; skipped discovered URLs are recorded only in extract trace output. BeautifulSoup emits `heading` / `paragraph` / `list_item` / `code` blocks with `source_type: web`, final `url`, and `heading_path`.
- Serving version rules, idempotency/replay, and trace tails are unchanged from 2C.1.

**Dependencies.** `httpx`, `beautifulsoup4`; see `apps/api/THIRD_PARTY_NOTICES.md`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
167 passed, 2 warnings in ~11s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`. Phase 2C.8 had introduced nine `httpx` `DeprecationWarning`s from per-request `cookies=` on stubbed fetches; fixed by setting cookies on the shared `httpx.Client` for the redirect loop and closing it in `finally`.

**Not implemented (2C.9+).** GitHub extraction (deferred). Chunking, embeddings, indexing, activation.

## 2026-11-14 — Phase 3 Milestone 3A (structure-aware chunking)

**Scope.** Deterministic chunking from canonical blocks, chunk persistence, worker `chunk` stage, and read-only chunk inspection APIs. GitHub ingestion remains deferred. No embeddings, FAISS, BM25, search, activation, or UI.

**Behavior.**

- After normalize, the worker runs `chunk` with the knowledge base `chunk_config` snapshot on the processing run (`structure_aware`, target **400**, overlap **60**, split oversized blocks). Traces record `chunk` succeeded; `embed` through `activate` stay `skipped` / `deferred`.
- Chunker concatenates whole blocks up to the token budget, starts a new chunk at headings, splits single blocks that exceed the budget with overlap, prefixes heading paths when the first block is not a heading, and stores `block_start_id`, `block_end_id`, `locator_start`, `locator_end`, and metadata (`heading_prefix`, `block_split`).
- Token sizing uses the BM25 lexical tokenizer rules (NFKC, casefold, apostrophe split, Unicode letters/numbers). `chunks.token_count` is that count.
- APIs: `GET /processing-runs/{run_id}`, `GET /processing-runs/{run_id}/chunks`, `GET /chunks/{chunk_id}`. `DocumentRead.chunk_count` reflects the latest extracted/ready run on the preview or serving version.

**Schema.** `chunks` table (migrations `0003_phase3a_chunks`, `0004_chunk_block_fk_cascade`) with cascade when canonical blocks are removed.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
175 passed, 2 warnings in ~11s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3B (embedding provider + fake embedder)

**Scope.** `EmbeddingProvider` protocol and a deterministic `FakeEmbeddingProvider` for unit tests. No Sentence Transformers adapter, worker `embed` stage, FAISS, BM25, activation, search, or UI. GitHub ingestion remains deferred.

**Behavior.**

- Protocol: `model_id`, `revision`, `dimension`, `embed_documents`, `embed_query` (D5 / `ARCHITECTURE.md`).
- Fake embedder: SHA-256 expansion to the configured dimension (default **384**, matching `BAAI/bge-small-en-v1.5`), L2-normalized non-empty vectors; query and passage roles differ so the same string encodes differently; empty strings return a zero vector without normalization.
- Module: `app/modules/embeddings/` (`protocol.py`, `fake.py`). Constants `FAKE_EMBEDDING_MODEL_ID`, `FAKE_EMBEDDING_REVISION`, `FAKE_EMBEDDING_DIMENSION`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
185 passed, 2 warnings in ~11s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3C (`vector_indexes` + `vector_mappings` schema)

**Scope.** SQLAlchemy models and migration `0005_phase3c_vector_indexes` per `DATABASE_DESIGN.md`. Schema/unit tests only. No FAISS, sidecars, worker embed/index stages, BM25, activation, search, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `vector_indexes`: KB-scoped embedding identity (`embedding_model`, `embedding_revision`, `dimension`, `metric`), `faiss_path`, `generation`, `vector_count`, `next_vector_id` defaults (`0`, `0`, `1`). Unique `(knowledge_base_id, embedding_model, embedding_revision, dimension, metric)`. Metric check: `ip` only; `dimension > 0`.
- `vector_mappings`: composite PK `(index_id, vector_id)`; unique `(index_id, chunk_id)`; index `(index_id, state)`. States `staged`, `committed`, `retired` via check constraint. `l2_norm` optional until commit. FK `index_id` → `vector_indexes` (`ON DELETE CASCADE`), `chunk_id` → `chunks` (`ON DELETE CASCADE`).

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
202 passed, 2 warnings in ~14s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3D (lexical index schema)

**Scope.** SQLAlchemy models and migration `0006_phase3d_lexical_index` for `chunk_stats`, `lexical_postings`, and `lexical_corpus_stats` per `DATABASE_DESIGN.md`. Schema/unit tests only. No BM25 scoring, postings writers, worker changes, FAISS, activation, search, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `chunk_stats`: PK/FK `chunk_id` → `chunks` (`ON DELETE CASCADE`); `knowledge_base_id`, `token_count` (BM25 `dl`), `processing_run_id`.
- `lexical_postings`: composite PK `(knowledge_base_id, term, chunk_id)`; `processing_run_id`, `term_frequency > 0`; indexes `ix_lexical_postings_chunk_id`, `ix_lexical_postings_processing_run_id`; `chunk_id` cascades on delete.
- `lexical_corpus_stats`: PK/FK `knowledge_base_id`; `chunk_count`, `token_sum`, `avg_token_length`, `updated_at`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
215 passed, 2 warnings in ~13s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3E (vector index creation + staged id allocation)

**Scope.** Database service in `app/modules/vectorstore/allocation.py` to get-or-create `vector_indexes` per KB embedding identity and allocate monotonic `vector_id` values with `staged` `vector_mappings` for a processing run. Unit tests only. No sidecars, FAISS, embeddings worker stage, BM25 writers, search, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `get_or_create_vector_index`: unique identity `(knowledge_base_id, embedding_model, embedding_revision, dimension, metric)`; `faiss_path` `{kb_id}/{index_id}/index.faiss` after insert; nested savepoint on create races.
- `stage_vector_mappings_for_run`: locks the index row, walks chunks in `sequence` order, reuses existing mappings per `chunk_id`, allocates from `next_vector_id` only for missing chunks, bumps the counter in the same transaction; retry returns the same pairs without extra rows or ids.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
221 passed, 2 warnings in ~13s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3F (per-run vector sidecars)

**Scope.** Durable `.npz` sidecar write/read/validate under `INDEX_DIR/{kb_id}/{index_id}/sidecars/{run_id}.npz` via `persist_run_sidecar` and the fake embedder over staged mappings. `index_dir` setting added (default `indexes`). Dependency: `numpy`. No FAISS, worker embed stage, BM25 writers, activation, search, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `write_sidecar_atomic`: buffer `ids` (`int64`) and L2-normalized `vectors` (`float32`); temp `{name}.npz.tmp`, fsync, `os.replace`, fsync file and sidecars directory.
- `load_sidecar` / `validate_sidecar_arrays`: shape, dtype, id order, normalization checks.
- `persist_run_sidecar`: embed chunks in `sequence` order; sets `vector_mappings.l2_norm`; mappings stay `staged`; skips rewrite when sidecar already complete (same staged ids), `force=True` replaces; idempotent retries.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
225 passed, 2 warnings in ~14s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3G (in-memory FAISS adapter)

**Scope.** `FaissIdMapIndex` in `app/modules/vectorstore/faiss_index.py`: `IndexIDMap2` over `IndexFlatIP`, in-memory only. Dependency `faiss-cpu`; license noted in `THIRD_PARTY_NOTICES.md`. No on-disk `index.faiss`, worker stages, mapping commit, search API, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `add` / `search` / `remove` / `rebuild` with prepared int64 ids and float32 vectors; inner product on L2-normalized inputs (cosine).
- Validates dimension on build, add, and search; empty add is a no-op; empty index search returns `[]`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
229 passed, 2 warnings in ~15s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3H (staged FAISS index files)

**Scope.** Build per-run staged FAISS files from completed vector sidecars at `INDEX_DIR/{kb_id}/{index_id}/staged/{run_id}.faiss` (not live `index.faiss`). Atomic write/read/validate helpers. No publish to live index, generation bump, mapping commit, worker, search API, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `build_staged_faiss_from_sidecar` / `persist_staged_faiss_for_run`: load sidecar, `FaissIdMapIndex.from_sidecar`, `write_faiss_atomic` (`.faiss.tmp`, fsync, rename, directory fsync); skip when already complete; `force=True` replaces.
- `validate_staged_faiss_file` / `staged_faiss_is_complete` / `FaissIdMapIndex.read_from_file`: `IndexIDMap2` dimension, row count, and id set; `round_trip_sidecar_search` checks NN hits per sidecar row.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
233 passed, 2 warnings in ~14s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3I (lexical posting drafts)

**Scope.** In-memory `build_lexical_posting_drafts` in `app/modules/lexical/postings.py` using the shared BM25 tokenizer. No DB writes, BM25 scoring, worker changes, FAISS publish, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `ChunkLexicalInput` + `LexicalPostingDraft`: normalized `term`, `chunk_id`, `term_frequency`, `document_length` (BM25 `dl`), and `token_count` per chunk.
- Token frequencies from tokenizer output; chunks sorted by `chunk_id`; terms sorted lexically; whitespace-only chunks produce no postings.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
240 passed, 2 warnings in ~14s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3J (lexical persistence)

**Scope.** `persist_lexical_for_run` in `app/modules/lexical/service.py` writes `chunk_stats` (per chunk, BM25 `dl` via tokenizer) and `lexical_postings` from posting drafts. No `lexical_corpus_stats`, BM25 scoring, worker, FAISS publish, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- Builds drafts from run chunks, compares to existing rows; skip when complete (`replaced=False`).
- Otherwise delete postings for `processing_run_id` and `chunk_stats` for run chunks, then insert fresh rows (`replaced=True`); chunk text changes trigger replace on retry.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
244 passed, 2 warnings in ~15s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3K (corpus stats recompute)

**Scope.** `recompute_lexical_corpus_for_active_chunks` for a caller-selected active chunk set: persist `lexical_corpus_stats` (`N`, `token_sum`, `avgdl`) and materialized `df(t)` in `lexical_term_document_frequencies` (migration `0007_phase3k_term_df`). No BM25 scoring, worker, FAISS publish, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `N` and `avgdl` from `chunk_stats` for selected chunk ids; `df(t)` counts active postings rows per term (one per chunk, repeated within-chunk terms do not inflate `df`).
- Empty selection zeros corpus stats and clears term `df` rows; recompute replaces prior snapshot for the knowledge base (run swap idempotent for same selection).

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
249 passed, 2 warnings in ~14s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3L (Okapi BM25 scoring)

**Scope.** Pure BM25 in `app/modules/lexical/bm25.py`: architecture idf formula, term saturation with `k1=1.5` and `b=0.75`, tokenizer-aligned queries, optional `bm25_score_chunk_from_db` over persisted postings and corpus stats. No search API, worker, FAISS publish, or activation. GitHub ingestion remains deferred.

**Behavior.**

- `bm25_idf`, `bm25_term_contribution`, `bm25_score`; terms with `df=0` add nothing; zero `N` or `avgdl` yields score `0`.
- Query tokens repeated in the query string contribute multiple term scores.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
256 passed, 2 warnings in ~15s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3M (batch chunk embedding service)

**Scope.** In-memory `embed_chunks_batch` in `app/modules/embeddings/batch.py` over `EmbeddingProvider`. Validates provider row count, input order, per-vector dimension, and finite values. No persistence, worker `embed` stage, FAISS publish, lexical writers, search, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- `ChunkEmbeddingInput` / `ChunkEmbeddingResult`: ordered `(chunk_id, vector)` pairs from chunk texts via `embed_documents`.
- Mismatched provider row count, wrong dimension, or non-finite components raise `ValueError`; empty input returns `()`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
262 passed, 2 warnings in ~16s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3N (staged embedding artifacts per run)

**Scope.** `build_staged_embedding_artifacts_for_run` in `app/modules/vectorstore/staged_embeddings.py`: staged vector mappings, batch embed via `embed_chunks_batch` inside `persist_run_sidecar`, durable sidecar write, then `processing_runs` embedding model/revision/dimension only after sidecar validation. No FAISS build/publish, worker, lexical writers, search, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- Idempotent staging and sidecar skip when complete; run metadata filled on retry when the sidecar already exists.
- Mismatched run embedding identity vs provider raises `ValueError`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
266 passed, 2 warnings in ~16s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3O (staged FAISS per run)

**Scope.** `build_staged_faiss_artifacts_for_run` in `app/modules/vectorstore/staged_faiss_run.py`: load sidecar, verify against staged mappings (`staged_vector_ids_for_run`), build `INDEX_DIR/.../staged/{run_id}.faiss`, validate ids/count/dimension and sidecar round-trip search. No live `index.faiss` publish, worker, lexical writers, search, activation, or UI. GitHub ingestion remains deferred.

**Behavior.**

- Rejects missing or mismatched sidecars before FAISS build; retries reuse complete staged files; invalid staged files are rebuilt; mapping rows stay `staged`.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
270 passed, 2 warnings in ~16s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3P (worker index stages)

**Scope.** After chunking, the ingest worker runs real `embed`, `vector_index`, and `lexical_index` trace stages via `app/modules/ingestion/index_stages.py` (fake embedder, staged sidecar/FAISS, lexical persist). `activate` remains deferred. Document stays `extracted` with preview only; no `current_version_id` or live index publish. GitHub ingestion remains deferred.

**Behavior.**

- Chunks commit before indexing; index failures commit trace tails then retry; resume finalizes when chunks exist but indexing is incomplete; preview-only lifecycle unchanged.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
273 passed, 2 warnings in ~17s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3Q (live FAISS publish + recovery)

**Scope.** `publish_live_faiss_for_run` in `app/modules/vectorstore/live_publish.py`: validate sidecar and staged FAISS, merge into live `index.faiss`, write generation stamp, commit `staged`→`committed` mappings and bump `vector_indexes.generation`. Startup `run_vector_index_recovery` rebuilds from committed sidecars when stamp or file disagrees with DB. Committed-only lookup helpers. No activation, `current_version_id`, search API, or UI. GitHub ingestion remains deferred.

**Behavior.**

- Torn publish (files ahead of DB) leaves staged ids non-searchable; recovery restores prior committed generation; publish retry is safe.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
278 passed, 2 warnings in ~17s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3R (activation, rollback, retirement, purge)

**Scope.** `app/modules/activation/`: atomic `activate_processing_run` (sets `current_version_id`, active run, document `ready`, KB corpus recompute, retire job enqueue), `rollback_document_serving` (`409 version_retired` when prior run retired), async `retire_processing_run` and `purge_document`, `vector_mapping_visible_in_search`, ingest `complete_activate_stage` (live FAISS publish then activation). Ingest finalize commits chunk + index + activate in one transaction. API: `POST /documents/{id}/rollback`, `DELETE /documents/{id}` → purge job. Worker handlers `retire_run`, `purge_document`. No search API, chat, or UI.

**Behavior.**

- First successful ingest activates to `ready`. Failed replacement on a serving document keeps the prior `current_version_id`. Third ready run enqueues retirement of the oldest retained run. Soft-delete drops document chunks from active corpus immediately; purge removes postings/mappings/sidecars and rebuilds live FAISS.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
286 passed, 2 warnings in ~20s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3S (Sentence Transformers embedder)

**Scope.** `SentenceTransformersEmbeddingProvider` for default `BAAI/bge-small-en-v1.5` (384-d, L2-normalized outputs), lazy model load, `EmbeddingConfigurationError` / `EmbeddingModelLoadError`, and `embedding_provider_from_settings` (`EMBEDDING_USE_FAKE` → fake embedder). Optional pip extra `embeddings` (`sentence-transformers>=3.0`). Worker ingest still uses `FakeEmbeddingProvider` directly in unit tests. No search API, UI, or activation changes. GitHub ingestion remains deferred.

**Behavior.**

- Revision defaults to `{sentence_transformers_version}+{model_id}` when `EMBEDDING_REVISION` is unset; pin via settings when needed.
- `pytest` excludes `@pytest.mark.integration` by default; set `RUN_EMBEDDING_INTEGRATION=1` to exercise live weights.

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
298 passed, 1 deselected, 2 warnings in ~18s
```

Warnings (pre-existing only): Starlette/`httpx` deprecation on `TestClient` import; `SAWarning` in `tests/test_worker.py` when a failed ingest has already cleared `pending_run_id`.

## 2026-11-14 — Phase 3 Milestone 3T (documents chunk preview UI)

**Scope.** `DocumentsPage` uses existing `GET /documents/{id}`, `GET /processing-runs/{id}`, and `GET /processing-runs/{id}/chunks` with cursor pagination, locator summaries, and editorial empty/loading/error states. Recent documents persist locally by library; ingest list/upload UI unchanged. `DocumentRead.chunk_preview_run_id` resolves the pending or latest extracted/ready run for the preview/current version so the UI can load chunks without a new route.

**Behavior.**

- Open a document by UUID (or from recents) to preview chunks in sequence order with token counts and locator spans; **Load more** follows `next_cursor`.
- Failed runs, missing runs, empty chunk lists, and `401`/`403`/`404` show concise messages in the existing shell layout.

**Tests run.**

```text
cd apps/web && npm test
12 passed in ~0.5s

cd apps/api && .venv/bin/pytest
298 passed, 1 deselected, 2 warnings in ~18s
```

## 2026-11-14 — Phase 3 acceptance gaps (embedder factory + FAISS acceptance)

**Scope.** Worker post-chunk index stages resolve the embedder via `embedding_provider_from_settings()` (`EMBEDDING_USE_FAKE=1` in pytest). Focused acceptance tests prove identical committed-generation neighbor ids after reloading the live index, and torn publish → recovery → `complete_activate_stage` commits mappings and activates the run. `IMPLEMENTATION_ROADMAP.md` updated for completed Phase 2 formats (GitHub deferred) and completed Phase 3.

**Tests run.**

```text
cd apps/web && npm test
12 passed in ~0.7s

cd apps/api && .venv/bin/pytest
300 passed, 1 deselected, 2 warnings in ~18s
```

**Not implemented (4+).** GitHub ingestion. Lexical search/ranking API and chat.

**Milestone 1 review checks (verified).**

- Migration `alembic upgrade head` runs in pytest `conftest` on `ragflow_test` (fresh tables each session) atop Phase 1 revision.
- Composite FK tests reject a version from another document (`test_phase2_schema.py`).
- Ready document keeps `current_version_id` and `status = ready` when a replacement upload is accepted (`test_uploads.py`).

**Tests run.**

```text
cd apps/api && .venv/bin/pytest
47 passed, 1 warning in ~2.7s
Warning: Starlette httpx deprecation (unchanged)
```
