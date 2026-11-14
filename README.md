# RAGFlow

Phase 1 runs the API, PostgreSQL, and the editorial web shell. Phase 2A adds authenticated file upload and durable storage under `STORAGE_DIR`. Phase 2B adds the PostgreSQL ingestion worker and job queue. Phase 2C.1 extracts TXT and Markdown into canonical blocks. Phase 2C.2 adds PDF extraction (PyMuPDF). DOCX and the other formats still fail with `extractor_not_implemented`. The worker does not chunk, index, or set `current_version_id`.

## Local development

PostgreSQL 16 must be running. Copy the environment file, then set a real `JWT_SECRET` (at least 32 characters). The API exits on startup if `JWT_SECRET` is missing or still a placeholder from `.env.example`. Adjust `DATABASE_URL` if your database user is not `ragflow`:

```bash
cp .env.example .env
# Edit .env: set JWT_SECRET to a long random string (openssl rand -hex 32 is fine).
```

Create the database, then start the API from `apps/api`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Start the web app from `apps/web`:

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite server proxies `/api` to `http://127.0.0.1:8000`.

Register an account on `/register`, then sign in. Tokens stay in memory, so a reload asks you to sign in again.

### Ingestion worker (Phase 2B)

From `apps/api` with the same `.env` as the API:

```bash
python -m app.worker
```

The worker claims `ingest_document` jobs from PostgreSQL. Until you need ingestion, you can leave it stopped; uploads are stored and enqueued. TXT, Markdown, and PDF extract with the worker running; DOCX and the other formats still fail until their extractors ship. Use the worker in development only when exercising claiming, traces, retries, or the `noop` handler in tests. After extractors ship, run the worker alongside the API so ingestion can complete.

## Tests

API tests use database `ragflow_test` on `localhost`. By default they connect as `PGUSER` or your login name (`$USER`). Override with `DATABASE_URL` when needed. `JWT_SECRET` is set automatically for pytest. Ingestion tests keep `EMBEDDING_USE_FAKE=1` so vectors come from the deterministic fake embedder (no Hugging Face download).

```bash
cd apps/api && .venv/bin/pytest
cd apps/web && npm test
```

Optional integration test for the production embedder (downloads `BAAI/bge-small-en-v1.5`):

```bash
cd apps/api && pip install -e ".[embeddings]"
RUN_EMBEDDING_INTEGRATION=1 .venv/bin/pytest -m integration
```

### Embeddings dependency (Phase 3S)

Production indexing and query encoding use [sentence-transformers](https://www.sbert.net/) (Apache-2.0). The default model is [`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5) (MIT). Install the extra before running the worker or API against real vectors:

```bash
cd apps/api && pip install -e ".[embeddings]"
```

Vectors are L2-normalized before storage and search (inner product on unit vectors). Override `EMBEDDING_MODEL` in `.env`; set `EMBEDDING_USE_FAKE=1` only for tests or offline demos without model weights.

## Docker Compose

Docker is optional for local development. From the repository root, with a `.env` file present:

```bash
docker compose up --build
```

The web container listens on `http://localhost:8080` and proxies `/api` to the API container. PostgreSQL is published on port 5432.

The worker is optional in Compose until you need background ingestion; see **Ingestion worker** above before enabling it on real uploads pre-2C.

Do not put a real Gemini key or a production JWT secret in git. `.env` is ignored. `.env.example` holds only local placeholders.
