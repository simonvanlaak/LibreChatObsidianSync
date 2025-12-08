import os
import sys
from pathlib import Path
from fastmcp import FastMCP
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route
from starlette.responses import JSONResponse

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import routes as auth_routes
from shared.middleware import SetUserIdFromHeaderMiddleware
from tools.obsidian_sync import (
    configure_obsidian_sync,
    get_obsidian_sync_status,
    reset_obsidian_sync_failures,
    force_complete_reindex,
)

obsidian_sync_mcp = FastMCP("Obsidian Sync MCP Server", stateless_http=True)

# Register tools
obsidian_sync_mcp.tool(configure_obsidian_sync)
obsidian_sync_mcp.tool(get_obsidian_sync_status)
obsidian_sync_mcp.tool(reset_obsidian_sync_failures)
obsidian_sync_mcp.tool(force_complete_reindex)

# Create app
base_app = obsidian_sync_mcp.http_app()

# Add OAuth routes
if hasattr(base_app, 'routes'):
    base_app.routes.extend(auth_routes)
    print(f"✅ OAuth routes added. Total routes: {len(base_app.routes)}")
    print(f"   OAuth routes: {[r.path for r in auth_routes]}")
else:
    # Fallback: create a new app with combined routes
    print("⚠️  base_app doesn't have routes attribute, using fallback")
    combined_routes = list(base_app.routes) if hasattr(base_app, 'routes') else []
    combined_routes.extend(auth_routes)
    base_app = Starlette(routes=combined_routes)

# Add health check endpoint
async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "obsidian-sync-mcp"})

health_routes = [
    Route("/health", health_check, methods=["GET"]),
]

if hasattr(base_app, 'routes'):
    base_app.routes.extend(health_routes)
else:
    combined_routes = list(base_app.routes) if hasattr(base_app, 'routes') else []
    combined_routes.extend(health_routes)
    base_app = Starlette(routes=combined_routes)

# Add middleware
app = SetUserIdFromHeaderMiddleware(base_app)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3003))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting ObsidianSyncMCP server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
