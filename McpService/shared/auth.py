import os
import secrets
import json
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.requests import Request
from starlette.routing import Route
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_200_OK

# OAuth client ID for ObsidianSyncMCP
CLIENT_ID = "obsidian_sync_mcp"

# In-memory storage for simplicity (since we are a single replica for now)
# In production with multiple replicas, this should be Redis
AUTH_CODES = {}  # code -> user_id
TOKENS = {}      # token -> user_id

def generate_token():
    return secrets.token_urlsafe(32)

def generate_auth_code():
    return secrets.token_urlsafe(16)

async def authorize(request: Request):
    """
    OAuth 2.0 Authorization Endpoint
    """
    params = request.query_params
    redirect_uri = params.get("redirect_uri")
    state = params.get("state")
    client_id = params.get("client_id")

    if not redirect_uri or not state:
        return HTMLResponse("Missing redirect_uri or state", status_code=400)

    # Extract user_id from state (format: userId:serverName)
    # This is the critical step where we identify the user without manual input
    try:
        user_id_raw = state.split(":")[0]
        # Sanitize or validate if necessary
        user_id = user_id_raw
    except Exception:
        return HTMLResponse("Invalid state parameter format. Expected userId:serverName", status_code=400)

    # If we received a POST (user clicked "Connect"), generate code and redirect
    if request.method == "POST":
        import logging
        logger = logging.getLogger(__name__)
        
        form = await request.form()
        logger.info(f"Authorization POST - action: {form.get('action')}, user_id: {user_id}, redirect_uri: {redirect_uri}")
        
        if form.get("action") == "approve":
            code = generate_auth_code()
            AUTH_CODES[code] = user_id
            
            logger.info(f"Authorization code generated: {code[:10]}... for user_id: {user_id}")
            logger.info(f"Stored in AUTH_CODES. Total codes: {len(AUTH_CODES)}")
            
            # Redirect back to LibreChat
            # Separator is ? or & depending if redirect_uri already has params
            sep = "&" if "?" in redirect_uri else "?"
            target = f"{redirect_uri}{sep}code={code}&state={state}"
            
            logger.info(f"Redirecting to: {target}")
            return RedirectResponse(target, status_code=302)
        else:
            logger.warning(f"Authorization denied - action: {form.get('action')}")
            return HTMLResponse("Access Denied", status_code=400)

    # Render simple confirmation page
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connect Obsidian Sync</title>
        <style>
            body {{ font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5; }}
            .card {{ background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 300px; text-align: center; }}
            h2 {{ margin-top: 0; color: #333; }}
            p {{ color: #666; }}
            .btn {{ display: block; width: 100%; padding: 0.75rem; border: none; border-radius: 4px; background: #007bff; color: white; font-size: 1rem; cursor: pointer; }}
            .btn:hover {{ background: #0056b3; }}
            .user-id {{ background: #eee; padding: 0.25rem 0.5rem; border-radius: 4px; font-family: monospace; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Connect Obsidian Sync</h2>
            <p>LibreChat is requesting access to your Obsidian sync configuration.</p>
            <p>User Context: <span class="user-id">{user_id}</span></p>
            <form method="POST">
                <input type="hidden" name="action" value="approve">
                <button type="submit" class="btn">Connect</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

async def token(request: Request):
    """
    OAuth 2.0 Token Endpoint
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if request.method == "POST":
        # Can be form-encoded or JSON
        content_type = request.headers.get("content-type", "")
        logger.info(f"Token request - Content-Type: {content_type}")
        
        if "application/json" in content_type:
            data = await request.json()
        else:
            data = await request.form()
        
        logger.info(f"Token request data: {dict(data) if hasattr(data, 'keys') else data}")
        
        code = data.get("code")
        grant_type = data.get("grant_type")
        code_verifier = data.get("code_verifier")  # PKCE support
        
        logger.info(f"Token request - code: {code[:10] if code else None}..., grant_type: {grant_type}, code_verifier present: {bool(code_verifier)}")
        logger.info(f"Available auth codes: {list(AUTH_CODES.keys())[:3] if AUTH_CODES else 'None'}")
        
        if not code:
            logger.warning("Token request missing 'code' parameter")
            return JSONResponse({"error": "invalid_grant", "error_description": "Missing authorization code"}, status_code=400)
        
        if code not in AUTH_CODES:
            logger.warning(f"Token request with invalid code: {code[:10]}... (not in AUTH_CODES)")
            logger.warning(f"Available codes: {list(AUTH_CODES.keys())}")
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid or expired authorization code"}, status_code=400)
            
        user_id = AUTH_CODES.pop(code) # Consume code
        access_token = generate_token()
        TOKENS[access_token] = user_id
        
        logger.info(f"Token generated successfully for user_id: {user_id}")
        
        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600 * 24 * 30, # 30 days
            "scope": "obsidian_sync"
        })
    
    return JSONResponse({"error": "method_not_allowed"}, status_code=405)

def get_user_from_token(token: str):
    return TOKENS.get(token)

routes = [
    Route("/authorize", authorize, methods=["GET", "POST"]),
    Route("/token", token, methods=["POST"]),
]
