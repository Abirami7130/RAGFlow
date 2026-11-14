# RAGFlow — Review Log

**Last updated:** 2026-11-12. R2 records the consistency review. Findings stay open until the named tests exist.

Findings are about the current planning baseline. There is no application code to review. “Independent” here means a pass that looks for contradictions and risks, not a second person. A later code review should append sections rather than rewrite closed entries.

Severity: `high` (should change before build), `medium` (handle in the named phase), `low` (watch).

## R1 — Planning baseline, 2026-11-12

### F1. Scope can bury the demo

- **Severity:** medium
- **Where:** Roadmap Phases 2–9 versus the demo definition in `PROJECT_OVERVIEW.md`.
- **Finding:** Website, GitHub, spreadsheets, graph, and evaluation are valuable and easy to start too early.
- **Fix:** The roadmap states the demo is end of Phase 6, and Phase 2 may stop after PDF, DOCX, TXT, and Markdown.
- **Status:** accepted, documented. Re-check at the end of Phase 2.

### F2. FAISS deletes are easy to get wrong

- **Severity:** medium
- **Where:** D8, retrieval filter rules.
- **Finding:** If query code trusts FAISS neighbors without checking `deleted_at` and `is_active`, purged documents leak into answers.
- **Fix:** R2 specifies committed mappings, generation rebuild, and the `is_active` filter. Phase 3 acceptance includes the crash-before-commit test and the delete test.
- **Status:** design updated in R2. Open until the Phase 3 tests exist.

### F3. Gemini availability

- **Severity:** medium
- **Where:** D11, chat acceptance.
- **Finding:** A portfolio machine may have no API key. A UI that only works with a live model will look broken during retrieval review.
- **Fix:** Retrieval and traces persist when generation fails with `llm_not_configured`. Search and playground can disable generation.
- **Status:** accepted as design. Open until Phase 4 test covers the missing key.

### F4. Two local models and RAM

- **Severity:** medium
- **Where:** D17.
- **Finding:** Embedding model plus cross-encoder can stall a laptop. Silent fallback would make scores incomparable.
- **Fix:** Rerank may be off, but the trace must say `skipped`. Settings shows readiness.
- **Status:** accepted as design. Open until Settings and traces show model readiness.

### F5. BM25 in Postgres will be slow if misused

- **Severity:** low
- **Where:** D9, scale assumption.
- **Finding:** Document-frequency as a count per term is fine at thousands of chunks and poor at millions.
- **Fix:** Scale target is written down. `LexicalIndex` is the replacement seam. No action before a measured problem.
- **Status:** accepted.

### F6. shadcn default theme drift

- **Severity:** medium
- **Where:** D19, `UI_UX_GUIDELINES.md`.
- **Finding:** Initializing shadcn with defaults produces the generic look this project rejects.
- **Fix:** Tokens and a visual QA checklist are specified. Phase 1 acceptance requires the editorial shell.
- **Status:** open until Phase 1 visual check is logged.

### F7. Citation honesty

- **Severity:** high
- **Where:** D12.
- **Finding:** Attaching top-k chunks as “citations” when the model did not cite them would misrepresent the system in an interview.
- **Fix:** R2 binds acceptance to the exact `build_context` label set, including rejection of `S01` and of chunks that were not packed. Phase 4 tests cover that set.
- **Status:** design updated in R2. Open until the Phase 4 tests exist.

### F8. SSRF on URL ingest

- **Severity:** high
- **Where:** D18, Phase 2 and Phase 9.
- **Finding:** A URL fetcher that follows redirects can reach metadata endpoints.
- **Fix:** R2 requires DNS pinning, rejection of mixed public/private answers, manual redirects, and link-local/private/loopback coverage. Phase 2 lists the stub-resolver cases.
- **Status:** design updated in R2. Open until those tests exist. The URL adapter is not done without them.

### F9. Trace tables scheduled after ingestion

- **Severity:** low
- **Where:** `DATABASE_DESIGN.md` migration phases.
- **Finding:** Ingestion traces are required in Phase 2, while the table list originally places traces in Phase 4.
- **Fix:** D28 and the migration table place `query_traces` and `trace_events` in Phase 2. Every stage, including skipped stages, is required.
- **Status:** resolved in the plan. Open only as a Phase 2 implementation check.

### F10. No code review yet

- **Severity:** low
- **Finding:** This pass reviewed documents only.
- **Status:** accepted. Next review is the Phase 1 diff: auth, error envelope, and visual shell.

## R2 — Consistency review, 2026-11-12

Reviewed the planning set against failed publishes, version switches, job crashes, BM25 maintenance, citation labels, trace rows, and URL fetches. No application code was written. The stack and the modular monolith are unchanged.

### F11. FAISS publish was a single step

- **Severity:** high
- **Where:** D7, D8, Phase 3.
- **Finding:** A crash between writing the index file and committing mappings could expose half-published vectors or lose the previous file with no rebuild source.
- **Fix:** Sidecars, `staged`/`committed` mappings, generation stamps, and startup rebuild from committed sidecars. Search requires a committed mapping on the active serving run. Normative steps are in `ARCHITECTURE.md`.
- **Status:** design updated. Open until the Phase 3 crash test passes.

### F12. A new version could replace a working one before it was ready

- **Severity:** high
- **Where:** `documents.current_version_id`, activation.
- **Finding:** The first plan moved the serving pointer as part of “becoming ready” without a single commit that preserves the previous run.
- **Fix:** D27. Pending runs stay inactive until one transaction flips `is_active`, `current_version_id`, and corpus stats. Rollback restores the retained previous run. Retire applies only to runs older than that previous run.
- **Status:** design updated. Open until the Phase 3 activation and rollback tests pass.

### F13. Job retry could duplicate work or stall after a crash

- **Severity:** high
- **Where:** D14, `jobs`.
- **Finding:** The queue had a retry count and skip-locked claims, and no idempotency key, heartbeat, or rule for an expired `running` row.
- **Fix:** Unique live `(job_type, idempotency_key)`, 30-second heartbeat, 900-second lock timeout, and handlers that reuse existing artifacts. Abandoned query traces close as `execution_lost` and are not regenerated.
- **Status:** design updated. Open until the Phase 2 kill-and-reclaim test passes.

### F14. BM25 corpus math was underspecified

- **Severity:** medium
- **Where:** D9.
- **Finding:** Tokenization, the document unit, `df`, `avgdl`, and the effect of deletion were named without an update rule. A pending version’s postings could have changed scores for the serving version.
- **Fix:** Tokenizer and IDF are fixed in D9 and `ARCHITECTURE.md`. `N`, `avgdl`, and `df` include active chunks only and change in the activation or delete transaction. Trace events store the statistics they used.
- **Status:** design updated. Open until the Phase 3 tokenizer and corpus tests, and the Phase 5 hand-computed score test, pass.

### F15. Citation checks needed an exact allow-list

- **Severity:** high
- **Where:** D12, F7.
- **Finding:** “Drop unknown labels” still allowed a near match, or a retrieved but unpacked chunk, to be stored as a citation.
- **Fix:** Validation is string equality against `build_context.output.labels`. Rejected labels stay on the trace event. Excerpts are copied from that record.
- **Status:** design updated. Open until the Phase 4 parser test passes.

### F16. Traces could omit skipped stages and timings

- **Severity:** medium
- **Where:** tracing section, F9.
- **Finding:** The event shape had timestamps and status, and did not require a row for every stage, a stored duration, an immutable config snapshot, or a closed row after a crash.
- **Fix:** D28. Full stage list, `duration_ms`, `skip_reason`, append-only attempts, immutable header `config`.
- **Status:** design updated. Open until Phase 4 persists a failed generation with the downstream stage skipped, and Phase 6 renders those rows.

### F17. URL checks stopped at a denylist

- **Severity:** high
- **Where:** D18, F8.
- **Finding:** Blocking a few literal addresses misses DNS rebinding, mixed A/AAAA answers, redirect hops, and link-local space beyond the metadata host.
- **Fix:** Manual redirects, DNS pinning, reject-if-any-address-is-blocked, and explicit loopback, private, shared, link-local, and metadata ranges. Phase 2 tests use a stub resolver.
- **Status:** design updated. Open until those tests pass.

### F18. One previous version is the rollback window

- **Severity:** low
- **Where:** D27.
- **Finding:** Keeping every historical FAISS id would grow the index. Retiring immediately would make rollback impossible.
- **Fix:** Retain the serving run and one previous ready run. Retire older runs after a successful activation.
- **Status:** accepted. The window is one version, by decision, not an open question.

## R3 — Phase 1 implementation, 2026-11-12

Backend and shell tests passed. See `IMPLEMENTATION_LOG.md` for commands. The register screen was opened in the browser and matches the ivory, cream, serif, and charcoal system. A signed-in click-through of every page was not completed in the browser because entering a password there was blocked; the shell test walks all nine routes instead.

### F6, continued

- **Status:** still open for a signed-in visual pass. The default shadcn theme was not installed. The register screen does not show slate cards or a purple gradient.

### F19. Phase 1 delete response differs from the original 202

- **Severity:** low
- **Where:** D30, `DELETE /knowledge-bases/{id}`.
- **Finding:** The first API draft returned a job id. Phase 1 has no job table.
- **Fix:** Soft delete returns `200` until Phase 2.
- **Status:** accepted for Phase 1.

## R3 — Phase 1 implementation review (independent), 2026-11-12

Findings from the Phase 1 code review and correction pass.

| ID | Severity | Status | Notes |
| --- | --- | --- | --- |
| F-01 | High | fixed | Required `JWT_SECRET`, rejects placeholders, startup fails, tests in `test_config.py` |
| F-02 | Medium | fixed | Row lock on refresh; `test_concurrent_refresh_issues_one_successor` |
| F-03 | Medium | fixed | Test DB URL from `PGUSER` / `$USER` |
| F-04 | Medium | fixed | Roadmap acceptance; memory-only tokens unchanged |
| F-05 | Medium | fixed | `test_editor_and_viewer_membership_rules` |
| F-06 | Low | fixed | Expired JWT, malformed refresh, invalid cursor tests |
| F-07 | Low | accepted | Registration 409 email enumeration — unchanged for local demo |
| F-08 | Low | fixed | `PROJECT_OVERVIEW.md`, `IMPLEMENTATION_LOG.md` status |
| F-09 | Low | open | Git still has no commits (out of scope for correction pass) |
| F-10 | Low | fixed | `UI_UX_GUIDELINES.md` Phase 1 count exception |

**npm audit (dev dependencies).** `tinypool` (critical, via `vitest`) and `@vitest/mocker` (moderate). Affects test runs only, not the production bundle. `npm audit fix --force` would jump to Vitest 5.x (breaking). Unresolved by choice; revisit before CI hardening.

## How to add a finding

```text
### F{n}. Title
- Severity:
- Where:
- Finding:
- Fix:
- Status: open | fixed | accepted
```

When a fix lands, record the test command in `IMPLEMENTATION_LOG.md` and set the status here. Do not delete the finding.
