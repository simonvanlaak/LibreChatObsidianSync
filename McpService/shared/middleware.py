"""
Middleware for ObsidianSyncMCP.
Handles OAuth token extraction, user identification, and auto-configuration of Obsidian sync.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from .auth import get_user_from_token
from .storage import set_current_user


class SetUserIdFromHeaderMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract user ID from OAuth token or headers.
    Also handles auto-configuration of Obsidian sync when customUserVars are provided.
    """
    
    async def dispatch(self, request: Request, call_next):
        import logging
        import sys
        from pathlib import Path
        
        logger = logging.getLogger(__name__)
        
        user_id = None
        
        # Method 1: OAuth Token Extraction (HIGHEST PRIORITY)
        # Extract Bearer token from Authorization header
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header:
            try:
                # Check if it's a Bearer token
                if auth_header.startswith("Bearer ") or auth_header.startswith("bearer "):
                    token = auth_header.split(" ", 1)[1].strip()
                    user_id = get_user_from_token(token)
                    if user_id:
                        logger.info(f"✅ Extracted user_id from OAuth token: {user_id}")
                    else:
                        logger.debug(f"OAuth token provided but not found in token store: {token[:10]}...")
            except Exception as e:
                logger.debug(f"Could not extract user_id from OAuth token: {e}")
        
        # Method 2: Header-based extraction (if OAuth didn't work)
        if not user_id:
            user_id = request.headers.get("x-user-id")
        
        # If header is missing or is the literal placeholder, try alternative extraction methods
        if not user_id or user_id == "{{LIBRECHAT_USER_ID}}":
            # Method 3: Try URL query parameter (fallback)
            query_user_id = request.query_params.get("userId") or request.query_params.get("user_id")
            
            if query_user_id and query_user_id != "{{LIBRECHAT_USER_ID}}":
                user_id = query_user_id
                logger.info(f"✅ Extracted user_id from URL query parameter: {user_id}")
            else:
                logger.debug(f"Query parameter 'userId' not found or is placeholder")
        
        # Only set user_id if it's valid (not None and not a placeholder)
        if user_id and not (user_id.startswith("{{") and user_id.endswith("}}")):
            set_current_user(user_id)
            
            # Auto-configure Obsidian sync if headers are present
            # LibreChat normalizes headers to lowercase, so check both cases
            repo_url = (
                request.headers.get("x-obsidian-repo-url") or 
                request.headers.get("X-Obsidian-Repo-URL")
            )
            token = (
                request.headers.get("x-obsidian-token") or 
                request.headers.get("X-Obsidian-Token")
            )
            branch = (
                request.headers.get("x-obsidian-branch") or 
                request.headers.get("X-Obsidian-Branch") or 
                "main"
            )
            
            # Only auto-configure if we have repo_url and token (required)
            if repo_url and token:
                try:
                    # Import here to avoid circular dependencies
                    sys.path.insert(0, str(Path(__file__).parent.parent))
                    from tools.obsidian_sync import auto_configure_obsidian_sync
                    
                    # Check if values are not placeholders before calling
                    def is_placeholder(value: str) -> bool:
                        return value and value.startswith("{{") and value.endswith("}}")
                    
                    if not is_placeholder(repo_url) and not is_placeholder(token):
                        await auto_configure_obsidian_sync(user_id, repo_url, token, branch)
                        logger.info(f"✅ Auto-configured Obsidian sync for user {user_id}")
                    else:
                        logger.debug(f"Skipping auto-configuration: placeholder values detected")
                except Exception as e:
                    # Log but don't fail the request if auto-configuration fails
                    logger.warning(f"Failed to auto-configure Obsidian sync for user {user_id}: {e}")
        else:
            # Don't set invalid user_id - get_current_user() will raise proper error
            set_current_user(None)
            
            # If OAuth is required and no valid user_id found, return 401 to trigger OAuth flow
            # Only do this for MCP endpoint, not for OAuth endpoints themselves
            if request.url.path == "/mcp" or request.url.path.endswith("/mcp"):
                logger.warning("OAuth required but no valid token found. Returning 401 to trigger OAuth flow.")
                return JSONResponse(
                    {"error": "OAuth authentication required", "oauth_required": True},
                    status_code=401
                )
        
        response = await call_next(request)
        set_current_user(None)
        return response
