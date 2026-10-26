"""Auth + CORS middleware.

Two auth surfaces:
- Service token (shared bearer SKILL_TRACKER_TOKEN) for server-to-server
  calls from discordbot / Tamarin / the training-site backend. Sets
  request.caller_member from X-Caller-Member when present.
- Browser session token (django.core.signing, salt "session") issued by the
  Discord OAuth flow. Sets request.session_member.

Also: per-IP fixed-window rate limiting on /api/auth/ (the AIR-001 lesson:
expensive endpoints outside the rate-limited prefix get skipped entirely)
and a minimal CORS shim so the training-site frontend can call the API
without pulling in django-cors-headers.
"""
import hmac
import time

from django.conf import settings
from django.core import signing
from django.http import JsonResponse

from .models import Member

SESSION_SALT = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def issue_session_token(member: Member) -> str:
    return signing.TimestampSigner(salt=SESSION_SALT).sign(str(member.id))


def resolve_session_token(token: str) -> Member | None:
    try:
        member_id = signing.TimestampSigner(salt=SESSION_SALT).unsign(
            token, max_age=SESSION_MAX_AGE
        )
    except signing.BadSignature:
        return None
    return Member.objects.filter(pk=member_id).first()


class ApiTokenMiddleware:
    OPEN_PATHS = ("/api/health/", "/api/auth/", "/api/openapi")

    # fixed-window per-IP limiter for auth endpoints (OAuth exchange is the
    # expensive bit); cleared lazily, fine for a single-process dev box and
    # per-process acceptable in prod behind gunicorn workers.
    AUTH_RATE_LIMIT = 30  # requests per minute per IP
    _buckets: dict[str, tuple[int, int]] = {}

    def __init__(self, get_response):
        self.get_response = get_response

    def _rate_limited(self, request) -> bool:
        ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
              or request.META.get("REMOTE_ADDR", "?"))
        window = int(time.time() // 60)
        count_window, count = self._buckets.get(ip, (window, 0))
        if count_window != window:
            count_window, count = window, 0
        count += 1
        self._buckets[ip] = (count_window, count)
        if len(self._buckets) > 10_000:  # bound memory
            self._buckets = {k: v for k, v in self._buckets.items() if v[0] == window}
        return count > self.AUTH_RATE_LIMIT

    def __call__(self, request):
        request.caller_member = None
        request.session_member = None

        if request.path.startswith("/api/auth/"):
            if self._rate_limited(request):
                return JsonResponse({"error": "rate limited"}, status=429)
            return self.get_response(request)

        if request.path.startswith("/api/") and not request.path.startswith(self.OPEN_PATHS):
            expected = settings.SKILL_TRACKER_TOKEN
            supplied = ""
            header = request.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                supplied = header[7:]
            token_ok = bool(expected) and hmac.compare_digest(supplied, expected)
            if token_ok:
                caller_id = request.headers.get("X-Caller-Member", "")
                if caller_id:
                    request.caller_member = Member.objects.filter(discord_id=caller_id).first()
            else:
                session_token = request.headers.get("X-Auth-Token", "")
                member = resolve_session_token(session_token) if session_token else None
                if not member:
                    return JsonResponse({"error": "unauthorized"}, status=401)
                request.session_member = member
        return self.get_response(request)


class CorsMiddleware:
    """Minimal CORS for configured origins; handles preflight."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin", "")
        allowed = origin and origin in settings.CORS_ALLOWED_ORIGINS
        if request.method == "OPTIONS" and allowed:
            resp = JsonResponse({}, status=204)
        else:
            resp = self.get_response(request)
        if allowed:
            resp["Access-Control-Allow-Origin"] = origin
            resp["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            resp["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Auth-Token, X-Caller-Member"
            )
            resp["Access-Control-Max-Age"] = "86400"
        return resp
