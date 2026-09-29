import os
from datetime import datetime, timedelta, timezone

import jwt
from app.core.config import settings
from mcp.server.auth.provider import AccessToken, TokenVerifier


# ============================================================
# JWT CONFIGURATION
# ============================================================

JWT_SECRET = settings.iam_jwt_secret

JWT_ALGORITHM = "HS256"

JWT_ISSUER = "iam-demo-auth"

JWT_AUDIENCE = "iam-mcp-server"


# ============================================================
# CREATE JWT
# ============================================================

def create_token(
    subject: str,
    client_id: str,
    scopes: list[str],
    expires_minutes: int = 60,
) -> str:

    now = datetime.now(timezone.utc)

    payload = {
        # Who is authenticated?
        "sub": subject,

        # Which client/app?
        "client_id": client_id,

        # Permissions
        "scope": " ".join(scopes),

        # Token issuer
        "iss": JWT_ISSUER,

        # Intended server
        "aud": JWT_AUDIENCE,

        # Issued at
        "iat": now,

        # Expiration
        "exp": now + timedelta(
            minutes=expires_minutes
        ),
    }

    token = jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    return token


# ============================================================
# VERIFY JWT
# ============================================================

def verify_jwt(
    token: str
) -> dict | None:

    try:

        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[
                JWT_ALGORITHM
            ],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )

        return payload

    except jwt.PyJWTError:

        return None


class JWTTokenVerifier(TokenVerifier):
    """Use the same signed JWT rules for each logical MCP resource."""

    def __init__(self, resource_url: str):
        self.resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        payload = verify_jwt(token)
        if not payload or not all(
            isinstance(payload.get(field), str) and payload[field]
            for field in ("sub", "client_id")
        ) or not isinstance(payload.get("scope", ""), str):
            return None
        return AccessToken(
            token=token,
            client_id=payload["client_id"],
            subject=payload["sub"],
            scopes=payload.get("scope", "").split(),
            resource=self.resource_url,
            expires_at=payload.get("exp"),
        )
