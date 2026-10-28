"""Security headers and a small per-client rate limit for the public dashboard and API.

The service is single-process on a free host, so an in-memory sliding window is enough.
Behind the Space's proxy the client address comes from X-Forwarded-For (uvicorn runs with
--proxy-headers, so request.client.host is already the forwarded address).
"""
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; "
                                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


# Public reads are cached at the edge (Vercel's CDN honours s-maxage), so N viewers cost one database
# read a minute instead of N. Anything sent with credentials, any write and any other /api path: no-store.
SHARED = "public, s-maxage=60, stale-while-revalidate=300"
CACHE = {"/api/usage": "public, s-maxage=300, stale-while-revalidate=300"}
PUBLIC_API = ("/api/status", "/api/stats", "/api/decisions", "/api/incidents", "/api/health/checks", "/api/eval")
PAGES = ("/", "/live", "/incidents", "/eval", "/how", "/usage", "/health", "/robots.txt")


def cache_control(request: Request, response) -> str | None:
    path = request.url.path.rstrip("/") or "/"
    if request.headers.get("authorization") or request.method not in ("GET", "HEAD"):
        return "no-store"
    if response.status_code != 200:
        return "no-store" if path.startswith("/api/") else None
    if path in CACHE:
        return CACHE[path]
    if any(path == p or path.startswith(p + "/") for p in PUBLIC_API):
        return SHARED
    if path.startswith("/api/"):
        return "no-store"
    html = response.headers.get("content-type", "").startswith(("text/html", "text/plain"))
    if html and (path in PAGES or path.startswith("/incidents/")):
        return SHARED
    return None


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        policy = cache_control(request, response)
        if policy:
            response.headers["Cache-Control"] = policy
        return response


HITS: dict[tuple[str, bool], deque] = defaultdict(deque)  # module-level so tests can reset it


class RateLimit(BaseHTTPMiddleware):
    """`limit` requests per `window_s` per client on /api/*; /health is never limited (uptime pings)."""

    def __init__(self, app, limit: int = 120, window_s: float = 60.0, write_limit: int = 10):
        super().__init__(app)
        self.limit, self.window_s, self.write_limit = limit, window_s, write_limit
        self.hits = HITS

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        write = request.method != "GET"
        key = (request.client.host if request.client else "?", write)
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        cap = self.write_limit if write else self.limit
        if len(q) >= cap:
            retry = int(self.window_s - (now - q[0])) + 1
            return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": str(retry)})
        q.append(now)
        if len(self.hits) > 10_000:  # bound memory under a spray of addresses
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window_s]:
                del self.hits[k]
        return await call_next(request)
