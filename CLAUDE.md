# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Text-to-SQL web app: converts natural language questions to SQL queries using Google Gemini, executes them against user-connected databases, and returns formatted results with auto-generated visualizations. Deployed on Render.

**Stack:** FastAPI (Python 3.11) + React 19 (Vite) + PostgreSQL (metadata) + Qdrant (vector search) + Google Gemini API

## Common Commands

```bash
# Backend dev server
uvicorn app.main:app --reload --port 8000

# Frontend dev server (HMR)
cd frontend && npm run dev

# Frontend production build
cd frontend && npm install && npm run build

# Install Python dependencies
pip install -r requirements.txt

# Docker build & run
docker build -t text-to-sql .
docker run -p 10000:10000 -e GEMINI_API_KEY_1=<key> -e QDRANT_URL=<url> -e APP_DATABASE_URL=<url> text-to-sql
```

No test suite exists. `qa_runner.py` is an integration/QA script, not a pytest suite.

## Architecture

### Query Pipeline (core flow)

```
User Question → QueryPlanner (Qdrant semantic search + Gemini intent analysis)
  → SQLGenerator (schema enrichment + Gemini SQL generation)
  → SQLValidator (regex + sqlglot AST security checks, SELECT-only, forced LIMIT)
  → QueryExecutor (pre-validates tables/columns, executes with timeout)
  → SmartFormatter (Gemini humanizes results + visualization hints)
  → Frontend renders text + Recharts chart + paginated table
```

### Training Pipeline (schema indexing, 3 steps)

1. **SchemaExtractor** — SQLAlchemy inspector reads all tables/columns/FKs
2. **Describer** — Gemini generates table descriptions for vector search
3. **Indexer** — Embeds descriptions (3072-dim) into Qdrant collection per session

### Key Subsystems

- **GeminiKeyManager** (`app/utils/gemini_key_manager.py`): Rotates across up to 13 API keys (GEMINI_API_KEY_1 through _13) when hitting quota limits. Thread-safe with asyncio.Lock.
- **UniversalConnector** (`app/db/connector.py`): Creates read-only SQLAlchemy engines from connection strings. Supports SQLite (file upload), PostgreSQL, MySQL, SQL Server.
- **Session management**: In-memory store with TTL (4h default), async background cleanup every 30min, persistent store backed by metadata DB.
- **SQL security**: All queries must be SELECT-only. Validator blocks DML/DDL, prevents semicolons (no statement chaining), injects LIMIT clauses via sqlglot AST.

### Frontend

React SPA with 5 main pages: Auth, Dashboard, Connect (DB connection input), Training (SSE progress), Chat (messages + charts + tables). Uses Recharts for Bar/Pie charts. Built frontend is served by FastAPI as static files.

## Configuration

- `config.yaml` — Runtime config (Gemini model, Qdrant settings, query limits, session TTL)
- `.env` — Secrets: `APP_DATABASE_URL`, `GEMINI_API_KEY_1`..`_13`, `QDRANT_URL`, `QDRANT_API_KEY`
- Config values support `${ENV_VAR}` interpolation in YAML

## Database Models (`app/db/metadata_models.py`)

Core tables: ServiceMeta, TableMeta, ColumnMeta, JoinKey, QueryLog, Correction, User, UserSession, SavedModel, ChatThread, ChatThreadMessage.

## Conventions

- Async-first: all I/O operations use async/await; sync operations wrapped with `asyncio.to_thread()`
- Custom exception hierarchy: `AppError` → `DBError`, `QueryError`, `SessionError`, `ValidationError`
- API routes prefixed with `/api/` (auth, session, train, chat, models)
- Qdrant collections named `session_{db_hash}`
- Schema cache stored at `uploads/cache/{db_hash}/`
- Branch strategy: `master` is production, feature branches for development
