# RAGFlow — Decision Record

**Last updated:** 2026-11-12. D7–D9, D12, D14, and D18 are refined. D27 and D28 are new.

Decisions below are accepted for planning. Implementation must follow them unless a later entry supersedes them and the affected docs are updated together.

Format: context, decision, consequences.

---

## D1. Modular monolith, not microservices

**Context.** The system has many logical stages (ingest, chunk, embed, retrieve, generate) but one developer and one local deployment.

**Decision.** One backend codebase and one frontend app. Stages are Python packages with explicit interfaces. Docker Compose runs `web`, `api`, `worker`, and `postgres`. No service mesh, no queue broker in the first build.

**Consequences.** Cross-stage refactors stay easy. The API and the worker are separate processes. A worker crash stops new ingestion and leaves the last activated version searchable. Query code never imports extractor libraries.

## D2. Ingestion and query execution stay separate

**Context.** Mixing “index this file” with “answer this question” in one request handler makes traces, retries, and permissions muddy.

**Decision.** Ingestion is an async job that ends in a `ready` processing run. Query execution only reads active, ready artifacts. Chat, search, and the playground all enter through the query path.

**Consequences.** The playground can re-run retrieval without re-parsing files. Upload handlers return quickly with a document id and a job id.

## D3. No LangChain as the orchestration layer

**Context.** Frameworks hide prompts, retrievers, and document objects. The playground has to show those pieces, and the project has to demonstrate the engineering.

**Decision.** Implement extraction adapters, chunking, BM25, reciprocal rank fusion, context packing, and citation parsing in project code. Use focused libraries for file parsing, FAISS, sentence-transformers, and HTTP. Do not introduce LangChain, LlamaIndex, or Haystack for control flow.

**Consequences.** More code to own. Failures are visible. A later experiment can wrap a single stage for comparison; it must not become the default path unless this decision is revised.

## D4. Canonical blocks with source locators

**Context.** Citations need a stable place to point: PDF page, DOCX heading, slide, cell, JSON path, URL, GitHub line.

**Decision.** Every extractor emits `CanonicalBlock` records: ordered text plus a `SourceLocator`. Chunks store start and end locators and the block id range they cover. Citations copy the locator and a text excerpt onto the message so history survives deletion.

**Consequences.** Extractors are slightly more work than “dump text.” The playground can show why a chunk cites page 4 rather than a bare snippet. Locator shape is specified in `ARCHITECTURE.md` and stored as JSONB.

## D5. Provider interfaces for embeddings, reranking, generation, and vectors

**Context.** Models and indexes will change. Call sites should not know FAISS or Gemini details.

**Decision.** Define narrow protocols:

- `EmbeddingProvider.embed_documents` / `embed_query`
- `Reranker.rerank`
- `LanguageModel.generate` (streaming)
- `VectorIndex.add` / `search` / `remove` (logical ids)
- `LexicalIndex.search`

FAISS, Sentence Transformers, the cross-encoder, and Gemini are the first adapters.

**Consequences.** Tests can use a fake embedder (hashing bag-of-words is not used in production; tests may use a tiny deterministic embedder). Swapping Qdrant or a local LLM later is an adapter, not a rewrite.

## D6. Default embedding model: `BAAI/bge-small-en-v1.5`

**Context.** The model must be free, local, and good enough to demo. Larger models slow a laptop demo.

**Decision.** Default to `BAAI/bge-small-en-v1.5` (384 dimensions). L2-normalize vectors. Search with inner product, which matches cosine on normalized vectors. Persist model name, revision (Hugging Face commit or library version plus model id), and dimension on every processing run and vector index.

**Consequences.** English-first quality. Multilingual corpora will underperform until a different model is configured. A knowledge base is queried only with the model that built its active index. Re-embedding is a new processing run.

**Alternative rejected.** `all-MiniLM-L6-v2` is smaller to download but weaker. `bge-base` is stronger and heavier; it remains a settings override, not the default.

## D7. FAISS `IndexIDMap2` plus a Postgres mapping

**Context.** FAISS ids are integers. Chunk ids are UUIDs. FAISS deletion is awkward. Rebuilds must not orphan mappings.

**Decision.** One FAISS index per knowledge base and embedding identity (model, revision, dimension). Use `IndexIDMap2` over `IndexFlatIP`. The application assigns `vector_id` values and never reuses them. `vector_mappings` stores `(index_id, vector_id, chunk_id, state)`. Floats live in a per-run `.npz` sidecar. Postgres `generation` is the source of truth for which sidecar rows are published. The FAISS file is written to a temp path, fsynced, and renamed. Mappings move from `staged` to `committed` in a transaction that sets `generation`. Search drops any neighbor that is not a committed mapping on the active serving run.

**Consequences.** A crash between renaming the file and committing mappings cannot change query results, because the new ids are still `staged`. Startup rebuilds the file from committed sidecars when the on-disk stamp and `generation` disagree. Exact search stays explainable at the portfolio scale (tens of thousands of chunks). The `VectorIndex` protocol remains the seam for another engine. The publish steps are normative in `ARCHITECTURE.md`.

## D8. Deletes tombstone, then rebuild

**Context.** `IndexIDMap2` can remove ids, but a long-lived index plus lexical postings is easier to reason about with an explicit purge.

**Decision.** Document delete, in one transaction, sets `deleted_at`, clears `is_active` on that document’s runs, and recomputes BM25 corpus stats. A purge job then removes blobs, blocks, postings, sidecars, FAISS ids, and chunk rows. Superseded runs are not deleted at activation time. The serving run and the immediately previous ready run are retained. Older runs are retired: mappings become `retired`, ids are removed in a new publish, postings go away, chunk text stays. Retrieval requires `is_active` and `state = committed`, so ids that are still physically in the file after a failed publish or before purge do not surface.

**Consequences.** Queries stop returning a deleted document as soon as the delete transaction commits, before file cleanup finishes. Citations keep their excerpt and set `source_deleted`. A lost sidecar for a committed run is repaired by `reembed_run`, which does not clear a serving version that is still intact.

## D9. Okapi BM25 stored in Postgres

**Context.** Hybrid search needs a durable lexical index the playground can open. An in-memory `rank-bm25` object disappears on restart and is hard to inspect. Elasticsearch adds a service for little demo value.

**Decision.** The chunk is the BM25 document. Tokenization is NFKC, lowercase, and splits on apostrophes and on characters that are not Unicode letters or numbers. No stemming and no stopword list. `dl` is the resulting token count. `N`, `avgdl`, and `df` count active serving chunks only. Postings for a pending run are stored early and stay out of the corpus until activation recomputes `lexical_corpus_stats` in the same transaction that flips `is_active`. Delete and retire drop chunks out of `N` and `df` the same way. IDF is `ln(1 + (N - df + 0.5) / (df + 0.5))`. The score formula and defaults (`k1 = 1.5`, `b = 0.75`) are unchanged. Each lexical trace event stores `N`, `avgdl`, and the `df` values it used.

**Consequences.** A replacement version cannot change BM25 statistics until it becomes the serving version, and a failed replacement leaves the previous corpus in place. Traces stay interpretable after later deletes. Query cost is acceptable at thousands of chunks. A larger base would replace `LexicalIndex` rather than add a second definition of `df`.

## D10. Reciprocal Rank Fusion, then a cross-encoder

**Context.** Vector and keyword ranks are not on the same scale. A learned reranker is more convincing after a fused candidate set exists.

**Decision.** Retrieve `top_k_vector` and `top_k_lexical` (default 20). Fuse with RRF, `k = 60`:

`score(d) = Σ 1 / (k + rank_i(d))`

Rerank the top `rerank_top_n` (default 20) with `cross-encoder/ms-marco-MiniLM-L-6-v2`. Pass the top `context_k` (default 5) that fit the context budget into the prompt.

Each step is its own trace stage. Hybrid or rerank can be turned off per request so the playground can compare.

**Consequences.** Two local models load into memory (embedder and cross-encoder). The worker and API should not each load both if memory is tight; see D17.

## D11. Gemini behind a language-model interface

**Context.** The brief asks for the Gemini API. It also asks to prefer free tools and avoid lock-in.

**Decision.** `LanguageModel` is the only generation API the query path sees. The first adapter is Gemini, configured with `GEMINI_API_KEY` and `GEMINI_MODEL`. The concrete model id is chosen at Phase 4 against the current Gemini Flash-class model and then recorded here. The key comes from the environment, not from a user-entered settings field.

If the key is missing, retrieval, fusion, and rerank still run and are traced. The generation stage fails with `llm_not_configured`. The playground shows that failure. The system does not fall back to a fabricated answer.

**Consequences.** Demo generation needs a key and network. Interview machines without a key can still show retrieval quality. A local model adapter can be added later without touching chat UI.

## D12. Do not invent citations

**Context.** A polished demo can cheat by attaching the top chunk as a citation even when the model ignored it.

**Decision.** `build_context` persists the only legal label set (`S1`, `S2`, …) with chunk id, excerpt, and locator. The prompt prints those labels as `[S1]`. After generation, the server keeps a match of `\[S[1-9][0-9]*\]` only when the full label string is in that set. `S01` is not `S1`. Retrieved chunks that were not packed have no label and cannot be cited. Duplicates collapse by first appearance. Excerpt and locator are copied from the label record. Rejected strings are stored on the `map_citations` event and are not inserted as citations. If the model cites nothing valid, the answer is stored with an empty citation list and `no_citations_emitted`.

Overlap between the answer and an uncited chunk is not a citation.

## D13. Retrieval uses the current question only

**Context.** Query rewriting over chat history improves some answers and makes the playground harder to trust, because the embedded string is no longer what the person typed.

**Decision.** For the initial chat release, embed and search the current user message verbatim. Include the last few turns in the generation prompt only. The `query_prepare` trace stage records the exact retrieval string. Query rewriting is a later experiment with its own stage, default off.

## D14. Database-backed jobs, not Celery

**Context.** Ingestion must survive a refresh and retry safely. Redis plus Celery is a second operational system.

**Decision.** A `jobs` table is the queue. A worker polls with `FOR UPDATE SKIP LOCKED`, including `running` rows whose `locked_at` is older than `lock_timeout_seconds` (default 900). The handler refreshes `locked_at` at least every 30 seconds. Job types: `ingest_document`, `purge_document`, `retire_run`, `rebuild_index`, `reembed_run`, `extract_entities`, `run_evaluation`. Each live job has an idempotency key, unique while `pending` or `running`. Transient failures retry three times with delays of 10s, 30s, and 90s. Permanent format and SSRF errors fail once. Handlers resume the same processing run and treat existing sidecars, staged ids, blocks, and postings as success.

**Consequences.** A killed worker retries work that may have partially written files. The publish and activation rules make that retry safe for readers. Throughput stays modest, which matches local document sets. The worker entrypoint is `python -m app.worker`. Query streaming stays on the API process; startup marks abandoned query traces `execution_lost` and does not call the model again.

## D15. Authentication is real, even for a local demo

**Context.** A fake logged-in user hides authorization bugs. Retrofitting users onto documents is expensive.

**Decision.** Email and password. Passwords hashed with Argon2. Short-lived JWT access token and a rotating refresh token stored only as a hash. Knowledge base membership roles: `owner`, `editor`, `viewer`. Creating a knowledge base inserts the owner membership. Viewers query and read. Editors ingest, reprocess, and delete documents. Owners manage membership and delete the base.

The first-run bootstrap creates one admin from `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` when the user table is empty. Member-management UI can wait; the tables and checks cannot.

## D16. Files and vectors stay outside PostgreSQL

**Context.** Bytea blobs and embedding arrays bloat backups and slow the app database.

**Decision.** Original bytes go to `STORAGE_DIR/{kb_id}/{document_id}/{version_id}`. Filenames on disk are ids, never the user-supplied name. FAISS files go to `INDEX_DIR/{kb_id}/{index_id}/index.faiss`. Postgres holds metadata, canonical text, chunk text, postings, and vector-id mappings.

Upload limit for the MVP: 25 MB per file. Reject mismatched magic bytes for PDF, DOCX, PPTX, XLSX, and ZIP-based formats.

## D17. Process split for model memory

**Context.** Loading the embedder in both API and worker doubles RAM.

**Decision.** The worker loads the embedding model and performs ingestion indexing. The API loads the embedding model for query vectors, the cross-encoder, and the lexical search. They share index files through a volume. Generation is an HTTP call, so it adds little RAM.

If a laptop cannot hold both local models, rerank defaults off and the trace shows the stage skipped by configuration. Document this in Settings as provider/model readiness, not as a silent quality drop.

## D18. Website and GitHub ingestion are bounded

**Context.** Open-ended crawling and arbitrary clones are safety and scope problems.

**Decision.**

- **URL:** Fetch at most 20 pages, depth at most 1, same host only. Follow at most five redirects manually. Resolve DNS immediately before every connect, reject the name if any address is loopback, private, shared (`100.64.0.0/10`), link-local, unspecified, multicast, or metadata, and connect only to an address that passed (DNS pinning). Re-check every redirect and every crawl link. Block userinfo, non-HTTP schemes, and ports other than 80 and 443. Cap the body at 5 MB and the timeouts at 5s connect / 15s read. Extract with BeautifulSoup. No JavaScript execution in v1, so client-rendered apps yield thin text. The normative check list is in `ARCHITECTURE.md`.
- **GitHub:** Public HTTPS repositories. `git clone --depth 1` into a scratch directory. Cap file count (200) and total bytes. Skip binaries and respect `.gitignore`. Store path and line ranges. Private repositories wait for a later token design; do not ask users to paste tokens into the database in v1.

## D19. shadcn/ui as accessible primitives, not as the visual identity

**Context.** The requested stack includes shadcn/ui. The default shadcn theme looks like every other dashboard.

**Decision.** Use Radix-based primitives where behavior is subtle (dialog, popover, dropdown, tabs, tooltip). Restyle every token to the palette in `UI_UX_GUIDELINES.md`. Do not use the default card-grid dashboard, default radius, or default slate theme. Prefer purpose-built layout components for documents, traces, and the pipeline.

## D20. Typography

**Context.** The brief suggested several faces and asked for a judged combination.

**Decision.**

| Role | Face | Why |
| --- | --- | --- |
| Editorial display | Instrument Serif | Sharp, contemporary, used only for titles and a few large names. |
| UI headings and navigation | Space Grotesk | Geometric and recognizable, distinct from Inter and Manrope. |
| Body and interface text | DM Sans | Neutral at small sizes, readable in tables, not the default Inter look. |
| Technical data | IBM Plex Mono | Scores, ids, prompts, locators. |

**Rejected.** Playfair Display is too ceremonial for dense data. Manrope is too close to a generic geometric product face. Inter is explicitly out. One family everywhere would flatten the editorial/engineering contrast.

Self-host via Fontsource at implementation time so the UI does not depend on a runtime call to Google.

## D21. Light editorial theme only for v1

**Decision.** No dark theme in the first build. The identity is warm paper and ink. A dark theme would need its own contrast study; adding it early splits visual effort.

## D22. Streaming with a durable message, then SSE

**Context.** A single long POST is painful to resume and awkward to authorize twice.

**Decision.** `POST /messages` creates the user message and an assistant message in `pending`, starts the query, and returns ids plus a trace id. `GET /messages/{id}/stream` emits SSE from stored events and live generation. Reconnecting reads what is already persisted.

## D23. Entities are a later, local extraction step

**Decision.** Phase 7 uses spaCy `en_core_web_sm` to write entities and in-chunk relationships. It does not call Gemini to invent a graph. Confidence and evidence chunk ids are stored. The graph UI reads those rows.

## D24. Evaluation metrics that can be computed without a judge model

**Decision.** Phase 8 golden sets store expected document ids and, when available, expected chunk ids. Required metrics: Hit@k, MRR, and latency. An LLM-as-judge faithfulness score is optional and off by default, because it needs Gemini and is harder to defend. Lexical overlap between the answer and cited excerpts can be shown as a diagnostic, labeled as overlap, not as truth.

## D25. Phase gates

**Decision.** A phase is complete only when its acceptance checks in `IMPLEMENTATION_ROADMAP.md` pass and `IMPLEMENTATION_LOG.md` records the commands and results. Skipping ahead to “build the playground UI with sample JSON” is not allowed. The playground ships when traces exist.

## D26. Where code will live

**Decision.** Future application code lives in this repository only:

```text
/Users/abirami/Desktop/ragflow/
  apps/api/
  apps/web/
  docker-compose.yml
```

Planning documents stay at the repository root. Nothing for this project is created outside this folder.

## D27. Activation switches the serving version in one transaction

**Context.** Indexing a replacement into the live corpus before it is complete, or clearing the old version first, leaves a failed upload with no working document.

**Decision.** `current_version_id` is the serving version and moves only inside the activation transaction described in `ARCHITECTURE.md`. The pending run becomes searchable in that same commit, and the previous run becomes inactive in that same commit. Corpus stats are recomputed there. Failure before commit leaves the previous ready version serving. One previous ready run is retained for `POST /documents/{id}/rollback`. Older runs are retired afterward.

**Consequences.** Readers never observe a gap between versions or a mix of two versions. Rollback is impossible after `retire_run` has removed the previous run’s vectors and postings. Chunk text for retired runs remains for traces.

## D28. Traces are complete and append-only

**Context.** A playground that only stores successful stages hides the configuration that skipped rerank, and a crash can erase the last open stage.

**Decision.** Every ingestion and query pipeline writes one event per stage, including skipped stages with `skip_reason`. Header `config` is immutable. Timestamps come from `clock_timestamp()`, and `duration_ms` is stored. A retry appends a new attempt. Recovery closes a `running` event with `worker_lost` or `execution_lost` and fills downstream stages as `upstream_failed`. Citation allow-lists and BM25 `N`, `avgdl`, and `df` are part of the stage output.

**Consequences.** A stored trace is enough to explain a run after the corpus has changed. The event table grows with attempts; that is acceptable at demo volume. Trace tables are created in Phase 2.

## D29. Hidden knowledge bases return 404

**Context.** The roadmap asked for 403 when a second user opens someone else's knowledge base. The API spec says a resource the caller cannot see is `404`, so existence does not leak.

**Decision.** A non-member, and a member of a soft-deleted base, receives `404 not_found`. A member whose role is too low for the action receives `403 forbidden`.

**Consequences.** Tests cover both cases. The Phase 1 acceptance line matches this rule.

## D31. Upload dedup uses `dedup_hash`, not raw `content_hash` at the knowledge-base level

**Context.** URL and GitHub snapshots may share bytes across documents. A unique `(knowledge_base_id, content_hash)` would forbid that.

**Decision.** `content_hash` records SHA-256 for every version. File uploads set `dedup_hash = content_hash`. URL and GitHub leave `dedup_hash` null. Enforce `UNIQUE (knowledge_base_id, dedup_hash) WHERE dedup_hash IS NOT NULL` and `UNIQUE (document_id, content_hash) WHERE content_hash IS NOT NULL`. Concurrent duplicate uploads race on the dedup index; the loser returns `200` with `reused: true`.

**Consequences.** Cross-document duplicate file bytes return `409 duplicate_content`. Same-document duplicate bytes return `200` reused (replacement route) or `409 duplicate_content` on a second new-document upload.

## D30. Phase 1 deletion does not enqueue a job

**Context.** Safe deletion of a knowledge base was specified as `202` plus a purge job. The `jobs` table arrives with ingestion in Phase 2, and Phase 1 has no documents to purge.

**Decision.** `DELETE /knowledge-bases/{id}` sets `deleted_at` in the request and returns `200` with `{ "id", "deleted": true }`. Phase 2 will switch this response to `202` and a purge job when document bytes exist. Until then the API spec records the Phase 1 response.

**Consequences.** Deleted bases disappear from lists immediately. No job row is invented.
