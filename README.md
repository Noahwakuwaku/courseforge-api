# CourseForge API

[简体中文](README.zh-CN.md) | English

CourseForge is an AI-assisted curriculum and learning-material generator. Given a subject name, the backend builds a curriculum skeleton, evaluates possible subcategories, generates reviewed lecture outlines and full materials, and creates multiple-choice exams.

This repository contains the FastAPI backend and ARQ worker. A separate Vue frontend provides the interactive user interface.

## Features

- Generate a subject description and curriculum skeleton
- Expand courses into subcategories when useful
- Generate and review lecture outlines with specialised agents
- Produce discipline-aware learning materials
- Create single- and multiple-answer exam questions
- Run long LLM operations as Redis-backed ARQ jobs
- Poll individual or batched job status through the API
- Persist subjects, courses, lectures, materials, and exams in MongoDB
- Regenerate content and cascade-delete related records
- Bound LLM concurrency and retry transient provider failures

## Architecture

```text
Vue frontend
    │ HTTP / task polling
    ▼
FastAPI ───────► MongoDB
    │ enqueue       ▲
    ▼               │ results
  Redis ───────► ARQ worker ───────► OpenAI-compatible model API
```

## Tech stack

- Python 3.11 or later
- FastAPI and Uvicorn
- MongoDB with Motor
- Redis and ARQ
- OpenAI Python SDK using `AsyncOpenAI`
- Pydantic Settings

## Requirements

- Python 3.11+
- MongoDB 6+
- Redis 6+
- Credentials for an OpenAI-compatible model provider

## Quick start

Clone the repository and create a virtual environment:

```bash
git clone <your-backend-repository-url>
cd <your-backend-repository-directory>
python -m venv .venv
```

Activate it and install dependencies:

```bash
# macOS or Linux
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy and edit the environment file:

```bash
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env`.

Make sure MongoDB and Redis are running, then start the API and worker in two terminals from the repository root:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

```bash
arq tasks.worker.WorkerSettings
```

The API is available at <http://localhost:8000/api>, and interactive documentation is available at <http://localhost:8000/docs>.

## Configuration

| Variable | Example | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | `sk-...` | Model-provider API key |
| `OPENAI_BASE_URL` | `https://.../compatible-mode/v1` | OpenAI-compatible API base URL |
| `MODEL_NAME` | `qwen-plus-latest` | Provider model identifier |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `course_gen` | MongoDB database name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker and result store |
| `MAX_REVIEW_RETRIES` | `2` | Maximum outline generation/review attempts |
| `WORKER_MAX_JOBS` | `30` | Concurrent jobs per worker process |
| `LLM_MAX_CONCURRENCY` | `20` | Concurrent model calls per process |
| `LLM_MAX_RETRIES` | `3` | Retries for transient model-provider errors |
| `LLM_RETRY_BACKOFF` | `2.0` | Base seconds for exponential backoff |
| `LLM_TIMEOUT` | `180.0` | Timeout for each model request, in seconds |

Never commit `.env`. The checked-in `.env.example` contains placeholders only.

## Main API routes

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/subjects` | Generate and store a curriculum skeleton |
| `GET` | `/api/subjects` | List subjects |
| `GET` | `/api/subjects/{id}/snapshot` | Load the complete subject view |
| `POST` | `/api/subjects/{id}/expand/async` | Queue course-expansion jobs |
| `POST` | `/api/courses/{id}/content` | Queue outline generation |
| `POST` | `/api/subcategories/{id}/content` | Queue subcategory outline generation |
| `POST` | `/api/lectures/{id}/material` | Queue learning-material generation |
| `POST` | `/api/materials/{id}/exam` | Queue exam generation |
| `GET` | `/api/tasks/{id}` | Poll one job |
| `POST` | `/api/tasks/batch` | Poll several jobs in one request |

Use the OpenAPI page at `/docs` for the complete, current route list.

## Project structure

```text
agents/                 # Curriculum, content, review, material, and exam agents
routers/main_router.py  # HTTP endpoints and ARQ dispatch
tasks/worker.py         # Active ARQ jobs and worker configuration
models.py               # MongoDB persistence, indexes, and cascade deletion
utils.py                # Shared LLM client, retries, DB client, and JSON helpers
config.py               # Environment-backed settings
main.py                 # FastAPI application entry point
```

## Production and security notes

- Replace wildcard CORS with the exact frontend origins.
- Add authentication and authorisation before exposing generation and delete routes publicly.
- Never log connection strings that may contain credentials.
- Run the API and worker with the same working directory or an explicit environment-file strategy so they use the same configuration.
- Tune worker and LLM concurrency to the provider's RPM/TPM limits.
- Back up MongoDB before administrative or destructive operations.
- Generated educational content should be reviewed before high-stakes use.

## Current status

The project is an early-stage application. Automated tests, authentication, rate limiting, structured migrations, and production deployment manifests have not yet been added. The `tasks/celery_app.py` and `tasks/generation_tasks.py` files are remnants of an earlier Celery implementation; the active queue implementation is ARQ.

## Contributing

Issues and pull requests are welcome. Keep credentials, database exports, generated sitemaps, caches, and virtual environments out of commits. At minimum, run a Python syntax check before submitting changes:

```bash
python -m compileall -q .
```

## License

No open-source license has been selected yet. Add a `LICENSE` file before a public release; without one, the code is not automatically open for reuse.

