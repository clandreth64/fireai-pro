"""HTTP security helpers: auth hook, body-size limit, security headers.

AUTHENTICATION STATUS: this milestone provides only an optional shared bearer
token (FIREAI_API_TOKEN). Without it the API is UNAUTHENTICATED and must not be
exposed publicly. ``require_principal`` is the single dependency every route
uses, so real per-user auth (OIDC/session) can replace it in one place, and
job records already carry an ``owner`` column for per-tenant isolation.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fireai.config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def require_principal(request: Request, settings: Settings = Depends(get_settings_dep)) -> str | None:
    """Return the caller identity (None = anonymous). Swap this for real auth."""
    if not settings.api_token:
        return None
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), settings.api_token.encode()):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Valid bearer token required."},
                            headers={"WWW-Authenticate": "Bearer"})
    return "token-holder"


class BodySizeLimit:
    """Reject request bodies larger than the limit while streaming (before
    multipart parsing spools them to disk)."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            return await _too_large(send, self.max_bytes)
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge()
            return msg

        async def tracking_send(msg: Message) -> None:
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await _too_large(send, self.max_bytes)


class _TooLarge(Exception):
    pass


async def _too_large(send: Send, limit: int) -> None:
    body = (b'{"detail":{"code":"FILE_TOO_LARGE","message":"Upload exceeds the '
            + str(limit // (1024 * 1024)).encode() + b' MB limit."}}')
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class SecurityHeaders:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                h = list(msg.get("headers") or [])
                h += [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                      (b"referrer-policy", b"no-referrer"),
                      (b"content-security-policy",
                       b"default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
                       b"script-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'")]
                msg["headers"] = h
            await send(msg)

        await self.app(scope, receive, send_with_headers)
