# RAGFlow — Implementation Roadmap

**Last updated:** 2026-11-14. Acceptance checks cover index recovery, activation, jobs, BM25 statistics, citation allow-lists, full traces, URL address checks, live FAISS reopen, and torn-publish recovery.
**Rule:** A phase is complete only when every acceptance check has been run and the result is recorded in `IMPLEMENTATION_LOG.md`.

**Status:** Phase 0, Phase 1, Phase 2 (Milestones 1, 2A, 2B, and 2C.1–2C.8), and Phase 3 are complete. Phase 2 delivered PDF, DOCX, TXT, Markdown, PPTX, CSV, XLSX, JSON, and secure URL ingestion with fixture-backed locators. **GitHub ingestion is deferred** (not implemented). Phase 3 delivered chunking, fake and Sentence Transformers embedders, staged/committed FAISS, lexical indexing, activation/rollback/retirement/purge, and document chunk preview UI.

The demo target is the end of Phase 6: ingest PDF and Markdown (DOCX and TXT included), ask a cited question, and inspect the real trace, including hybrid search. Phases 7–9 deepen the portfolio; they are not required for that demo.

MVP format order inside Phase 2: PDF, DOCX, TXT, Markdown first. PPTX, CSV, XLSX, JSON, and URL shipped in 2C.4–2C.8. **GitHub remains deferred** to a later milestone. Do not list GitHub as shipped until it is implemented and logged.

## Phase 0 — Architecture and project foundation

**Outcome.** This folder exists, the nine documents agree, and no application code has been written.

**Tasks.**

- Create `/Users/abirami/Desktop/ragflow`.
- Write the nine source-of-truth documents.
- Record stack, schema, API, UI, and phase gates.

**Acceptance.**

- The directory is exactly `/Users/abirami/Desktop/ragflow`.
- All nine markdown files are present at the root.
- No `apps/` tree, no dependency manifest, no Docker Compose yet.
- User explicitly approves the plan before Phase 1.

## Phase 1 — Frontend and backend foundation

**Outcome.** A signed-in shell on the editorial system, with an API, database, and empty routes. No ingestion yet.

**Tasks.**

- `apps/api` FastAPI project, config, error envelope, `/health`.
- Alembic migration for users, refresh tokens, knowledge bases, members.
- Register, login, refresh, logout, me.
- Knowledge-base CRUD and membership checks.
- Postgres and API in Docker Compose.
- `apps/web` Vite, React, TypeScript, Tailwind tokens from `UI_UX_GUIDELINES.md`, React Router, TanStack Query.
- App rail and nine pages with their real layout skeletons (not lorem dashboards).
- Login page.
- Pytest for auth and membership. Vitest for the token client and one layout smoke test.

**Acceptance.**

- `GET /api/v1/health` returns database ok.
- A user can register, log in, create a knowledge base, and rotate the session with `POST /auth/refresh` while the SPA still holds tokens in memory. A full browser reload signs the user out in v1; there is no `localStorage` or `sessionStorage` persistence yet.
- A non-member receives 404. A viewer who updates or deletes the knowledge base receives 403. See D29.
- All nine routes render in the editorial palette with the correct type roles.
- Tests for this phase pass and are logged.

## Phase 2 — Multi-format document ingestion

**Outcome.** Files become canonical blocks with locators. The worker claims jobs safely. Ingestion traces exist from the first stage, including failures and skipped tail stages.

**Tasks.**

- Document, version, block, job, `query_traces`, and `trace_events` tables, including job idempotency and lock columns.
- Upload, content hash, magic-byte checks, 25 MB cap, disk storage.
- Worker loop: skip-locked claims, heartbeat, expired-lock reclaim, idempotent stage resume.
- Adapters: PDF, DOCX, TXT, Markdown, then PPTX, CSV, XLSX, JSON.
- URL adapter with the DNS, redirect, and address rules in `ARCHITECTURE.md`.
- GitHub public clone with file and byte caps.
- Status endpoint separates serving `status` from `pending_status`.
- Fixture tests per format asserting locators (page, heading, slide, row, json path, line range).

**Acceptance.**

- A PDF fixture yields blocks whose `page` values match known pages.
- A DOCX fixture preserves heading ancestry.
- TXT and Markdown fixtures preserve line or heading locators.
- An encrypted or image-only PDF ends `failed` with `pdf_unreadable`, the trace contains `extract` failed and later stages `skipped` / `upstream_failed`, and the worker keeps polling.
- A repeated upload of the same bytes reuses the version.
- A second ingest while `pending_run_id` is set returns `409 ingest_in_progress` and does not insert another job.
- Killing the worker after a claim and waiting past the lock timeout lets a new process claim the same job. A second execution does not duplicate blocks.
- With a stub resolver, these targets fail `ssrf_blocked` and fetch no body: `127.0.0.1`, `10.1.2.3`, `169.254.1.1`, `169.254.169.254`, a name that resolves to `127.0.0.1`, a name that resolves to both a public address and `10.0.0.1`, and an otherwise allowed URL that redirects to `169.254.169.254`. A redirect chain stops at five hops. `http://user:pass@example.com/` and `file:///etc/passwd` are rejected before enqueue.
- The Documents page shows live status and block text for a real upload.
- Format tests pass and are logged.

## Phase 3 — Chunking, embeddings, and vector indexing

**Outcome.** Ready documents are chunked, embedded, and searchable by vector id.

**Tasks.**

- Structure-aware chunker with the default config.
- `EmbeddingProvider` plus Sentence Transformers adapter.
- Deterministic fake embedder for unit tests.
- FAISS `IndexIDMap2` adapter, staged/committed mappings, generation stamp, and per-run sidecars.
- BM25 postings and `chunk_stats.token_count` written at index time. Search scoring can wait for Phase 5; corpus membership rules apply now.
- Activation transaction, rollback, and retire of runs older than the previous ready run.
- Chunk inspection API and a simple chunk list in the document preview.

**Acceptance.**

- A fixture document reaches `ready` with a stable chunk count.
- Every chunk has locators that fall inside its block range.
- Restarting the API still returns the same neighbor ids for a fixed query vector against the committed generation.
- A crash injected after the FAISS rename and before the mapping commit leaves the previous generation serving. After startup recovery, search does not return the staged ids. Retry then commits them and activates.
- Replacing a ready document with a run that fails in `vector_index` leaves `current_version_id` and search results on the previous version.
- A successful replacement switches search to the new version in one commit. `POST /rollback` restores the previous ready run. A third successful version retires the oldest of the three, and rollback to that oldest run returns `409 version_retired`.
- Deleting a document removes it from vector search after the delete transaction, before purge finishes, and purge deletes postings and sidecars.
- Tokenizer tests: NFKC casefold, apostrophe split, digit retention, and no stemming (`running` stays `running`).
- After activation, `lexical_corpus_stats.chunk_count` equals the active chunk count, and postings of the superseded run are excluded from a `df` query. Deleting the serving document recomputes `N` and `avgdl`.
- Embedding model, revision, and dimension are visible on the processing run.
- Unit tests for the chunker and mapping round-trip pass without downloading the production model. One marked integration test may load `bge-small` and is logged separately.

## Phase 4 — RAG chat and citations

**Outcome.** A question returns a streamed answer with real citations, or a clear generation failure after a real retrieval trace.

**Tasks.**

- Conversations, messages, citations, traces.
- Vector-only retrieval first (hybrid flags stored but may call the same vector list until Phase 5).
- Context packing, versioned prompt template, `[S1]` labels.
- Gemini adapter behind `LanguageModel`. Model id recorded in `DECISIONS.md` when chosen.
- SSE stream with replay snapshot.
- Citation parser that accepts only exact labels from `build_context.output.labels`.
- Chat UI: transcript, rail, stream, link to trace.
- Query traces always persist the full stage list, timings, config, and errors.

**Acceptance.**

- On a fixture knowledge base, a question whose answer is on a known page cites that document and a locator on that page when the model emits the matching label.
- If the model emits no labels, the message is `complete` and `citations` is empty. Test this with a fake language model.
- Given a context whose labels are `S1` and `S2`, the strings `S3`, `S01`, and `source 1` produce no citation rows and appear in `rejected_labels` when they were parsed as label-shaped (`S3`, `S01`). A retrieved chunk that was not packed cannot be cited.
- The citation excerpt equals the excerpt stored on the `build_context` label, not a fresh slice of the answer.
- With the Gemini key unset, retrieval events are still persisted, `generate` is `failed` with `llm_not_configured`, and `map_citations` is `skipped` with `upstream_failed`.
- The trace header `config` matches the request, and every stage row has `started_at`, `ended_at`, and `duration_ms`.
- SSE delivers tokens from the fake model in order.
- Chat UI test or manual check recorded: citation click selects the rail item.

## Phase 5 — Hybrid retrieval and reranking

**Outcome.** BM25, RRF, and the cross-encoder are real stages with inspectable scores.

**Tasks.**

- Okapi BM25 over postings, `k1=1.5`, `b=0.75`.
- RRF with `k=60`.
- Cross-encoder adapter. Tests can use a fake reranker that scores by a fixed function.
- Wire flags: hybrid off, rerank off.
- Search page with the score stack.
- Extend traces with hit payloads specified in the API.

**Acceptance.**

- A fixture query that uses a rare exact term absent from a naive embedding neighborhood is returned by BM25 and appears in the fused top 5. The test uses the fake embedder so the vector list is controlled.
- A hand-computed score for a two-chunk corpus matches the implementation, including `idf`, `dl`, and `avgdl`. Deleting one chunk changes `N` and `df` and does not change the stored trace of an earlier query.
- RRF unit test matches hand-computed scores for a three-document example.
- Turning hybrid off leaves `lexical_search` as `skipped` with `skip_reason = config_disabled`, and the trace still contains that row.
- Search API returns vector rank, BM25 rank, RRF, and rerank when those stages run.
- An integration run with the real cross-encoder is optional and, if run, is logged. Unit tests must not require it.

## Phase 6 — Interactive RAG playground

**Outcome.** The canvas and inspector are bound to real traces. No sample pipeline.

**Tasks.**

- `POST /pipeline/runs` and pipeline SSE.
- React Flow layout from the guideline, interaction locked to pan/zoom.
- Inspector per stage, including prompt text and score tables.
- Replay of a stored trace.
- Deep link from chat and search.

**Acceptance.**

- Running a question creates a trace whose stages match the nodes, including skipped stages, durations, and the immutable config.
- Inspector values for the fuse stage equal the API event payload (component test with a recorded fixture that originated from the API, not hand-waved numbers).
- A run with generation off does not show tokens and marks `generate` skipped.
- Disconnecting the network mid-stream and reloading replay shows persisted stages.
- Visual check against `UI_UX_GUIDELINES.md`: square nodes, editorial palette, no bubble chat on this page.

## Phase 7 — Analytics and knowledge graph

**Outcome.** Counts and charts come from SQL. Entities come from spaCy, not from the language model.

**Tasks.**

- Analytics endpoints and the Analytics page.
- Entity tables, worker job, mentions, `co_occurs` relationships.
- A simple graph or adjacency list on the Analytics page, secondary to the charts. Keep it typographic if a force-directed graph looks like a toy.

**Acceptance.**

- Overview counts match fixture rows inserted in the test database.
- Empty windows render the empty copy, not a fake series.
- After the entity job, a known fixture name appears as an entity with at least one mention chunk id.
- Analytics visual check: no metric-card row, olive/taupe/terracotta only.

## Phase 8 — Evaluation and experimentation

**Outcome.** Two retrieval configs can be compared on a golden set.

**Tasks.**

- Evaluation tables and APIs.
- Worker job that runs retrieval per case.
- Hit@5, MRR, latency.
- Evaluations page comparison table.

**Acceptance.**

- A golden set of at least five cases, with a controlled index, produces expected Hit@5 for a known config in a test.
- Two runs with different `hybrid_enabled` values persist separate metrics.
- The UI identifies the better Hit@5 without manual calculation.
- Generation remains off unless requested.

## Phase 9 — External integrations, security, and deployment

**Outcome.** The Compose stack is what a reviewer runs. Security checks that were designed are tested. URL and GitHub paths are finished if they were deferred.

**Tasks.**

- Confirm URL and GitHub acceptance tests if skipped earlier.
- Login rate limit and upload abuse cases.
- Path traversal tests on storage keys.
- Redirect SSRF test.
- Production-shaped Compose file: postgres, api, worker, web, volumes for storage, indexes, and model cache.
- README is allowed at this phase as run instructions, only if the user wants it; until then the nine docs remain the guide. Do not add a README earlier unless asked.
- Seed script for a demo knowledge base using local fixtures, clearly labeled.

**Acceptance.**

- `docker compose up` serves the web app and API. A clean machine path is written in `IMPLEMENTATION_LOG.md` including model download time if observed.
- SSRF, traversal, and auth tests pass.
- A reviewer can complete the demo path in `PROJECT_OVERVIEW.md` on the Compose stack.
- `REVIEW_LOG.md` has a pass over security-sensitive routes with findings closed or explicitly accepted.

## Suggested build order inside a phase

Schema and failing tests first, service second, API third, UI last. The playground UI never precedes the trace writer.

## Out of scope unless a decision is revised

- OCR, JavaScript-rendered websites, private GitHub tokens.
- Elasticsearch, Qdrant, Redis, Celery, LangChain.
- Dark mode, mobile-first redesign, multi-tenant billing.
- Approximate nearest-neighbor indexes before the corpus exceeds the scale in `ARCHITECTURE.md`.
