"""
Middleware for ObsidianSyncMCP.
Handles OAuth token extraction, user identification, and auto-configuration of Obsidian sync.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from .auth import get_user_from_token
from .storage import set_current_user, set_obsidian_headers, clear_obsidian_headers


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
                        # Log warning for debugging - token not found in store
                        logger.warning(f"⚠️ OAuth token provided but not found in token store. Token: {token[:20]}...")
                        logger.warning(f"⚠️ This usually means the token is invalid or the user needs to re-authenticate.")
                else:
                    logger.debug(f"Authorization header found but not Bearer token: {auth_header[:20]}...")
            except Exception as e:
                logger.warning(f"Could not extract user_id from OAuth token: {e}")
        else:
            # Log all headers for debugging (but don't log sensitive values)
            all_headers = {k: v[:20] + "..." if len(v) > 20 else v for k, v in request.headers.items()}
            logger.warning(f"⚠️ No Authorization header found in request. Available headers: {list(all_headers.keys())}")
            logger.debug(f"Request headers: {all_headers}")

        # Only set user_id if it's valid (not None and not a placeholder)
        if user_id and not (user_id.startswith("{{") and user_id.endswith("}}")):
            set_current_user(user_id)

            # Store Obsidian headers in context for potential auto-configuration
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

            # Store headers in context (even if placeholders, so status check can see them)
            set_obsidian_headers(repo_url, token, branch)

            # Auto-configure Obsidian sync if headers are present and valid
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
                # LibreChat requires WWW-Authenticate: Bearer header to detect OAuth requirement
                response = JSONResponse(
                    {"error": "OAuth authentication required", "oauth_required": True},
                    status_code=401
                )
                response.headers["WWW-Authenticate"] = "Bearer"
                return response

        response = await call_next(request)
        set_current_user(None)
        clear_obsidian_headers()
        return response
