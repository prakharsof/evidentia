# Evidentia

**Evidence-first research over your own documents.** Upload papers, reports, and notes; ask a question; inspect the passages used to answer it. Every generated claim must point to a retrieved source label. When no model key is configured, Evidentia still works in a deterministic extractive mode.

This is a portfolio project built to show product thinking, backend design, information retrieval, responsible AI behavior, and operational trade-offs in one coherent application.

## Why this project is interesting

- **Inspectable retrieval:** SQLite FTS5 performs local BM25 retrieval; the UI shows source, page, term coverage, and retrieval score.
- **Citation-aware generation:** optional OpenAI-compatible generation receives only retrieved passages; citation labels are checked against the retrieved set.
- **Graceful fallback:** no API key, model outage, or empty evidence set means the app still returns useful source excerpts rather than failing closed or fabricating an answer.
- **Local-first data:** documents and text chunks stay in a local SQLite database. The model provider receives retrieved excerpts only when generation is enabled.
- **Real product surface:** responsive browser interface, upload validation, duplicate detection, document deletion, and readable failure states.

## Architecture

```text
Browser
  ├── Upload PDF / Markdown / text
  ├── Ask a research question
  └── Inspect answer and evidence trail
          │ HTTP / JSON
          ▼
FastAPI ── parser + overlapping chunker ── SQLite documents/chunks
   │                                      └── FTS5 BM25 index
   └── retrieval → optional LLM → citation-label validation
```

## Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env   # Windows PowerShell; optional
uvicorn app:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Upload a text-based PDF, `.md`, or `.txt` file, then ask a specific question. Without an API key, the answer is explicitly extractive. To enable grounded synthesis, set `OPENAI_API_KEY` in `.env`; optionally set `OPENAI_MODEL` and `OPENAI_BASE_URL` for a compatible provider.

### Run with Docker

```bash
docker build -t evidentia .
docker run --rm -p 8000:8000 -v evidentia-data:/app/data evidentia
```

The named volume preserves the local library between container runs. Configure optional model credentials through your container platform's secret settings; do not bake `.env` files into an image.

## API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service and answer mode |
| `GET` | `/api/documents` | List indexed documents |
| `POST` | `/api/documents` | Index a multipart file upload |
| `DELETE` | `/api/documents/{id}` | Remove a document and its indexed chunks |
| `POST` | `/api/ask` | Retrieve evidence and answer a question |

FastAPI's interactive schema is available at `/docs`.

## Design decisions and limitations

1. **Why FTS5 first?** It is built into SQLite, deterministic, quick to run locally, and makes retrieval explainable. It is a lexical baseline, so paraphrases may be missed. A natural next step is hybrid retrieval with a local embedding model and measured recall/latency comparisons.
2. **Why optional generation?** The app has a useful no-key path and does not pretend that a language model is required for search. Generation is bounded to retrieved context and has deterministic temperature settings.
3. **Citation checking is not factual verification.** The app rejects labels that point outside the retrieved set, but it cannot prove every sentence is entailed by its citation. A serious evaluation layer should measure claim support and retrieval recall on a curated benchmark.
4. **PDF parsing is text extraction, not OCR.** Scanned PDFs need OCR before upload. The app states this rather than silently indexing empty content.
5. **Local demo, not public multi-user deployment.** Before internet exposure, add authentication, CSRF protections, rate limits, malware scanning, resource quotas, secure secrets management, and migration/backup policies.

## A strong demo walkthrough

1. Add two short papers that disagree on a claim.
2. Ask a question requiring comparison; show which passages were retrieved.
3. Ask for a claim unsupported by the library; show that the system reports missing evidence.
4. Remove one source and repeat to demonstrate the evidence trail changing.
5. Explain why lexical retrieval is the baseline and propose a benchmark before adding embeddings.

## Suggested next research extensions

- Add reciprocal-rank fusion between FTS5 and embedding retrieval, with a small labeled query set and recall@k / MRR measurements.
- Track answer claims separately and score citation entailment on a human-reviewed evaluation set.
- Add background indexing with a durable task queue and cancellation.
- Add document collections and per-collection access boundaries before any shared deployment.

## LinkedIn project summary

> Built Evidentia, an evidence-first research assistant that indexes PDFs and notes locally, retrieves passages with SQLite FTS5/BM25, and optionally generates source-cited answers with citation-label validation and a no-key extractive fallback. Designed the ingestion pipeline, API, persistence model, responsive interface, and failure behavior; documented retrieval limitations and a measurable hybrid-search roadmap.

**Before publishing:** replace the demo screenshots with your own, add a short screen recording, include an evaluation set with real relevance judgments, and explain which parts you personally designed and implemented. Never present hypothetical results as measured performance.
