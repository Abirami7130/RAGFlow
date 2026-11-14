# RAGFlow — Project Overview

**Status:** Phase 1 complete. Phase 2 Milestones 1 (schema) and 2A (upload/storage) are implemented. Extraction and the worker are not started.
**Location:** `/Users/abirami/Desktop/ragflow`
**Last updated:** 2026-11-12

RAGFlow is a multi-source AI knowledge platform and a RAG engineering playground. It lets a person collect documents from files, websites, and GitHub repositories, ask questions across them, and read answers that carry verifiable source citations. Alongside that product, it exposes the real retrieval pipeline — extraction, chunking, embeddings, search, reranking, prompt construction, generation, and citation mapping — as inspectable execution traces.

The project is meant to be portfolio work: substantial enough to discuss in an interview, and honest enough that every playground stage comes from a backend run. The interface is a warm, editorial developer tool, not a generic chatbot template.

## Goals

1. Ingest common document formats into one knowledge base and preserve source locations for citations.
2. Answer questions with a real retrieval-augmented generation pipeline and citations a reviewer can check.
3. Show the pipeline as it executed: chunks, scores, prompts, context, and model output.
4. Keep the system modular so embedding models, language models, and vector stores can be replaced.
5. Ship a distinctive interface that feels designed, with a documented visual system.

## Non-goals (initial release)

- Microservices, multi-region deployment, or a hosted multi-tenant SaaS.
- A general web crawler or private GitHub organization sync.
- LangChain (or a similar framework) as the orchestration layer.
- Streamlit, notebook UIs, or simulated pipeline animations.
- Training custom embedding models.
- Perfect citation faithfulness. The system records what the model cited and what was retrieved. It does not invent citations when the model emits none.

## Two products in one system

### A. AI knowledge platform

- Create knowledge bases and control who can read or edit them.
- Upload PDF, DOCX, TXT, Markdown, PPTX, CSV, XLSX, and JSON.
- Ingest a website URL (bounded fetch) and a public GitHub repository (bounded file set).
- Track document status from upload through indexing.
- Ask questions across the sources in a knowledge base.
- Receive streamed answers with citations that resolve to a page, slide, sheet row, JSON path, URL heading, or repository line range.
- Summarize one document and compare two or more sources.
- Search passages directly, without generation.
- Keep conversation history.
- Read usage, ingestion, and citation analytics.

PDF, DOCX, TXT, and Markdown are the first ingestion target. The other formats and remote sources follow once that path is solid. See the roadmap.

### B. RAG engineering playground

The playground is a first-class surface, not a debug afterthought. A run calls the same backend path as chat and search. The UI binds React Flow nodes and inspection panels to persisted trace events.

A person can inspect:

- Document extraction and the canonical blocks it produced.
- Preprocessing changes.
- Chunk boundaries, token counts, and source locators.
- Embedding model, revision, dimension, vector id, and vector summary.
- Vector hits with scores.
- BM25 hits with term statistics.
- Reciprocal Rank Fusion output.
- Cross-encoder rerank scores.
- The exact context pack and prompt sent to the model.
- The generated answer and the citation map.

If a stage did not run, the trace says so. The UI does not synthesize stand-in output.

## Primary user

A developer or interviewer reviewing the project, using a local deployment. The data model still includes users, roles, and knowledge-base membership so access control is real rather than bolted on later.

## Scope of a convincing demo

A strong demo is: sign in, create a knowledge base, upload a PDF and a Markdown file, watch ingestion reach `ready`, ask a question, open a citation to the page or heading, then open the playground and walk the trace for that question — hybrid list, rerank, prompt, and answer.

Analytics, the knowledge graph, evaluation runs, website ingest, and GitHub ingest deepen the project after that demo path works.

## Pages

| Page | Role |
| --- | --- |
| Dashboard | Orientation for the active knowledge base: recent documents, latest inquiry, quiet activity. |
| Knowledge Bases | Create and switch libraries. |
| Documents | Upload, status, version, canonical preview, reprocess, delete. |
| AI Chat | Multi-turn questions, streamed answers, citation rail. |
| Search | Ranked passages and score breakdown, no generation required. |
| RAG Playground | Live pipeline canvas and stage inspector. |
| Analytics | Ingestion health, query volume, latency, cited sources. |
| Evaluations | Golden questions and experiment comparison. |
| Settings | Profile, provider readiness, retrieval defaults. |

Page layout is specified in `UI_UX_GUIDELINES.md`.

## Technology summary

Modular monolith. React + TypeScript + Vite on the client. FastAPI on the server. PostgreSQL for metadata, text, and lexical postings. FAISS for vectors. Local disk for original files and index files. Sentence Transformers and a cross-encoder run locally. Gemini is the first language-model provider, behind an interface, and is optional for retrieval inspection.

Full reasoning is in `DECISIONS.md` and `ARCHITECTURE.md`.

## Documents that govern the project

| File | Owns |
| --- | --- |
| `PROJECT_OVERVIEW.md` | Vision, scope, demo definition. |
| `ARCHITECTURE.md` | Modules, data flow, providers, storage. |
| `DATABASE_DESIGN.md` | Tables, keys, indexes. |
| `API_SPECIFICATION.md` | HTTP API and schemas. |
| `IMPLEMENTATION_ROADMAP.md` | Phases and acceptance tests. |
| `IMPLEMENTATION_LOG.md` | What has actually been done. |
| `REVIEW_LOG.md` | Review findings and their status. |
| `DECISIONS.md` | Decisions and trade-offs. |
| `UI_UX_GUIDELINES.md` | Visual system and page design. |

When a decision or behavior changes, the relevant file is updated in the same change. These files stay the source of truth.

## Working constraints

- Build in phases. Do not generate the whole application in one step.
- Do not mark a phase complete until its tests have been run and its acceptance criteria met.
- Prefer a small set of direct implementations for chunking, BM25, fusion, and citation mapping so the playground can explain them.
- Prefer free, local tools. Gemini is the one planned external model API, and retrieval still runs without it.
- Keep the interface on the visual system in `UI_UX_GUIDELINES.md`.
