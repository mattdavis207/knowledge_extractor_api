# knowledge_extractor_api

Reusable FastAPI service for exposing local endpoints that call external APIs and return normalized data.

## Project Structure

```text
src/app/
  main.py                  # FastAPI app factory and health endpoint
  api/v1/router.py         # Versioned API router
  api/v1/endpoints/        # Endpoint modules, one per API/domain
  core/config.py           # Environment-based settings
  schemas/                 # Pydantic response/request models
  services/                # Reusable external API clients
tests/                     # Pytest tests
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Run Locally

```bash
fastapi dev src/app/main.py
```

Or run Uvicorn directly:

```bash
uvicorn app.main:app --reload --app-dir src
```

Open:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/v1/example/posts/1`

## Add Another Endpoint Service

1. Add any new environment variables to `.env.example` and `src/app/core/config.py`.
2. Create a service client in `src/app/services/` if the integration needs custom auth or request logic.
3. Create schemas in `src/app/schemas/`.
4. Create a router module in `src/app/api/v1/endpoints/`.
5. Register the router in `src/app/api/v1/router.py`.

## Quality Checks

```bash
ruff check .
pytest
```
