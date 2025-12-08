# ObsidianSyncMCP - MCP Server

MCP server for configuring and managing Obsidian vault synchronization.

## Tools

- `configure_obsidian_sync` - Configure Git repository and credentials
- `get_obsidian_sync_status` - Get sync status and progress percentage
- `reset_obsidian_sync_failures` - Reset failure count to resume sync
- `force_complete_reindex` - Force reindex of all files by deleting sync_hashes.json

## OAuth

- Client ID: `obsidian_sync_mcp`
- Authorization: `/authorize`
- Token: `/token`

## Running

```bash
# Development
python main.py

# With environment variables
PORT=3003 HOST=0.0.0.0 STORAGE_ROOT=/storage python main.py
```

## Docker

```bash
docker build -t obsidian-sync-mcp .
docker run -p 3003:3003 -e STORAGE_ROOT=/storage obsidian-sync-mcp
```
