# ObsidianSync Worker

Background service for Git synchronization and RAG indexing of Obsidian vaults.

## Features

- Git pull/push operations
- RAG API indexing with JWT authentication
- Failure tracking (stops after 5 consecutive failures)
- Throttling to prevent API overload
- LIFO indexing (most recently modified files first)
- Hidden directory exclusion (.git, .obsidian, etc.)

## Configuration

Environment variables:
- `RAG_API_URL`: RAG API endpoint
- `RAG_API_JWT_SECRET`: JWT secret for authentication
- `STORAGE_ROOT`: Storage path (default: /storage)
- `MAX_FILES_PER_CYCLE`: Max files per cycle (default: 10)
- `INDEX_DELAY`: Delay between requests (default: 0.5s)
- `MAX_CONCURRENT_INDEXING`: Max concurrent (default: 2)

## Running

```bash
# Development
python main.py

# With environment variables
RAG_API_URL=http://rag_api:8000 RAG_API_JWT_SECRET=secret STORAGE_ROOT=/storage python main.py
```

## Docker

```bash
docker build -t obsidian-sync-worker .
docker run -e RAG_API_URL=http://rag_api:8000 -e RAG_API_JWT_SECRET=secret obsidian-sync-worker
```
