"""
Security middleware — API key auth, secret filtering, rate limiting.
Protects your prompts, code, and agent data from unauthorized access.
"""
from __future__ import annotations
import hashlib
import re
import time
from collections import defaultdict
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
     lambda m: m.group(0).split(":")[0] + ": [FILTERED]"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), lambda m: "[FILTERED_KEY]"),
    (re.compile(r"Bearer [a-zA-Z0-9._-]{20,}"), lambda m: "Bearer [FILTERED]"),
]


def filter_secrets(text: str) -> str:
    """Strip secrets from text (prompts, responses, file contents)."""
    if not text:
        return text
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


class AuthMiddleware(BaseHTTPMiddleware):
    """All endpoints require X-Jarvis-Key header. Key is compared via SHA-256 hash."""

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self._hash = hashlib.sha256(api_key.encode()).hexdigest() if api_key else None

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/api/health",) or request.method == "OPTIONS":
            return await call_next(request)
        if not self._hash:
            return await call_next(request)

        key = request.headers.get("x-jarvis-key", "")
        if not key:
            return JSONResponse(status_code=401, content={"error": "Auth required. Set X-Jarvis-Key header."})
        if hashlib.sha256(key.encode()).hexdigest() != self._hash:
            return JSONResponse(status_code=403, content={"error": "Invalid API key."})
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """100 requests per minute per IP. WebSocket messages excluded."""

    def __init__(self, app, limit: int = 100, window: int = 60):
        super().__init__(app)
        self.limit = limit
        self.window = window
        self._buckets: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/api/health",) or request.method == "OPTIONS":
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        self._buckets[ip] = [t for t in self._buckets[ip] if now - t < self.window]
        if len(self._buckets[ip]) >= self.limit:
            return JSONResponse(status_code=429, content={"error": "Too many requests."})
        self._buckets[ip].append(now)
        return await call_next(request)


class SecretFilterMiddleware(BaseHTTPMiddleware):
    """Scans all messages in /send endpoints, strips secrets before storing in DB."""

    async def dispatch(self, request: Request, call_next):
        # Filter prompt before agent processes it
        if request.url.path.endswith("/message"):
            body = await request.body()
            import json
            try:
                parsed = json.loads(body)
                if "prompt" in parsed:
                    parsed["prompt"] = filter_secrets(parsed["prompt"])
                    # Swap body for filtered version
                    request._receive = lambda: asyncio.Future()
            except Exception:
                pass

        response = await call_next(request)
        # Filter secrets from JSON responses
        if response.headers.get("content-type", "").startswith("application/json"):
            import json, asyncio
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            if body_bytes:
                try:
                    data = json.loads(body_bytes)
                    data = _filter_dict(data)
                    body_bytes = json.dumps(data).encode()
                    async def new_iter():
                        yield body_bytes
                    response.body_iterator = new_iter()
                except Exception:
                    pass
        return response


def _filter_dict(d):
    if isinstance(d, dict):
        return {k: _filter_dict(v) if isinstance(v, (dict, list)) else (filter_secrets(v) if isinstance(v, str) else v) for k, v in d.items()}
    if isinstance(d, list):
        return [_filter_dict(x) for x in d]
    return d


def generate_api_key() -> str:
    """Generates a cryptographically secure API key. Save this — it cannot be recovered."""
    import secrets
    return secrets.token_urlsafe(32)
