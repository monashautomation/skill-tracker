"""Auth + CORS middleware.

Two auth surfaces:
- Service token (shared bearer SKILL_TRACKER_TOKEN) for server-to-server
  calls from discordbot / Tamarin / the training-site backend. Sets
  request.caller_member from X-Caller-Member when present.
- Admin-issued API key (X-API-Key or Authorization: Bearer, sha256-hashed in
  the ApiKey table): acts like the service token, `read` scope is GET-only.
- Browser session token (django.core.signing, salt "session") issued by the
  Discord OAuth flow. Sets request.session_member.

Also: per-IP fixed-window rate limiting on /api/auth/ (the AIR-001 lesson:
expensive endpoints outside the rate-limited prefix get skipped entirely)
and a minimal CORS shim so the training-site frontend can call the API
without pulling in django-cors-headers.
"""
import hashlib
import hmac
import time
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone

from .models import ApiKey, Member

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
    # Archived members' outstanding tokens stop working immediately (audit SKL-1).
    return Member.objects.filter(pk=member_id, archived_at__isnull=True).first()


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
        request.api_key = None

        # /api/v1/ is an alias of /api/: apply every check to the canonical path
        path = request.path
        if path.startswith("/api/v1/"):
            path = "/api/" + path[len("/api/v1/"):]

        if path.startswith("/api/auth/"):
            if self._rate_limited(request):
                return JsonResponse({"error": "rate limited"}, status=429)
            return self.get_response(request)

        if path.startswith("/api/") and not path.startswith(self.OPEN_PATHS):
            expected = settings.SKILL_TRACKER_TOKEN
            supplied = ""
            header = request.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                supplied = header[7:]
            token_ok = bool(expected) and hmac.compare_digest(supplied, expected)
            api_key = None
            if not token_ok:
                key = request.headers.get("X-API-Key", "") or supplied
                if key:
                    api_key = ApiKey.objects.filter(
                        key_hash=hashlib.sha256(key.encode()).hexdigest(), revoked_at__isnull=True
                    ).first()
            if api_key:
                if api_key.scope == ApiKey.SCOPE_READ and request.method not in ("GET", "HEAD", "OPTIONS"):
                    return JsonResponse({"error": "this key is read-only"}, status=403)
                now = timezone.now()
                if api_key.last_used_at is None or now - api_key.last_used_at > timedelta(minutes=1):
                    ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
                request.api_key = api_key
            if token_ok or api_key:
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


class SiteCsrfMiddleware(CsrfViewMiddleware):
    """CSRF for the website; /api/ is exempt (it authenticates by header tokens, not cookies)."""

    def process_view(self, request, callback, callback_args, callback_kwargs):
        if request.path.startswith("/api/"):
            return None
        return super().process_view(request, callback, callback_args, callback_kwargs)


class SiteMemberMiddleware:
    """Sets request.member (cookie-session login for the website) on every non-API request.

    None when not logged in or the member has been archived.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.member = None
        if not request.path.startswith("/api/"):
            member_id = request.session.get("member_id")
            if member_id:
                request.member = Member.objects.filter(
                    pk=member_id, archived_at__isnull=True
                ).first()
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
                "Authorization, Content-Type, X-API-Key, X-Auth-Token, X-Caller-Member"
            )
            resp["Access-Control-Max-Age"] = "86400"
        return resp
