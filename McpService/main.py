import os
import sys
from pathlib import Path

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import routes as auth_routes
from shared.middleware import SetUserIdFromHeaderMiddleware
from tools.file_storage import (
    create_note,
    delete_file,
    list_files,
    modify_file,
    read_file,
    search_files,
    upload_file,
)
from tools.obsidian_sync import (
    configure_obsidian_sync,
    force_complete_reindex,
    get_obsidian_sync_status,
    reset_obsidian_sync_failures,
)

obsidian_sync_mcp = FastMCP("Obsidian Sync MCP Server", stateless_http=True)

# Register Obsidian sync tools
obsidian_sync_mcp.tool(configure_obsidian_sync)
obsidian_sync_mcp.tool(get_obsidian_sync_status)
obsidian_sync_mcp.tool(reset_obsidian_sync_failures)
obsidian_sync_mcp.tool(force_complete_reindex)

# Register file storage tools
obsidian_sync_mcp.tool(upload_file)
obsidian_sync_mcp.tool(create_note)
obsidian_sync_mcp.tool(list_files)
obsidian_sync_mcp.tool(read_file)
obsidian_sync_mcp.tool(modify_file)
obsidian_sync_mcp.tool(delete_file)
obsidian_sync_mcp.tool(search_files)

# Create app
base_app = obsidian_sync_mcp.http_app()

# Preserve lifespan from FastMCP app for proper StreamableHTTPSessionManager initialization
mcp_lifespan = getattr(base_app, "lifespan", None)

# Add OAuth routes
if hasattr(base_app, "routes"):
    base_app.routes.extend(auth_routes)
    print(f"✅ OAuth routes added. Total routes: {len(base_app.routes)}")
    print(f"   OAuth routes: {[r.path for r in auth_routes]}")
else:
    # Fallback: create a new app with combined routes
    # IMPORTANT: Preserve lifespan to ensure StreamableHTTPSessionManager task group is initialized
    print("⚠️  base_app doesn't have routes attribute, using fallback")
    combined_routes = list(base_app.routes) if hasattr(base_app, "routes") else []
    combined_routes.extend(auth_routes)
    base_app = Starlette(routes=combined_routes, lifespan=mcp_lifespan)


# Add health check endpoint
async def health_check(_request):
    return JSONResponse({"status": "healthy", "service": "obsidian-sync-mcp"})


health_routes = [
    Route("/health", health_check, methods=["GET"]),
]

if hasattr(base_app, "routes"):
    base_app.routes.extend(health_routes)
else:
    # If we need to create a new app, preserve lifespan
    combined_routes = list(base_app.routes) if hasattr(base_app, "routes") else []
    combined_routes.extend(health_routes)
    base_app = Starlette(routes=combined_routes, lifespan=mcp_lifespan)

# Add middleware
app = SetUserIdFromHeaderMiddleware(base_app)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3003"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting ObsidianSyncMCP server on {host}:{port}")
    # uvicorn.run automatically detects and handles lifespan from the ASGI app
    uvicorn.run(app, host=host, port=port)
