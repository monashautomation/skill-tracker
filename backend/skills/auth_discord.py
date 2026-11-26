"""Discord OAuth2 login for the training site.

Flow: GET /api/auth/discord/login/ -> redirect to Discord -> callback
exchanges the code, upserts the Member, issues a signed session token.
The token is returned as JSON (API clients) or as a URL fragment to
FRONTEND_URL (browser flow). No secrets in the token — it's a signed
member id, revocable by deleting the Member row.
"""
import json
import secrets
import urllib.parse
import urllib.request

from django.conf import settings
from django.core import signing
from django.http import JsonResponse, HttpResponseRedirect
from django.utils import timezone
from django.views.decorators.http import require_GET

from .middleware import issue_session_token
from .models import Member

STATE_SALT = "oauth-state"
DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"  # noqa: S105
DISCORD_ME = "https://discord.com/api/v10/users/@me"


def _admin_ids() -> set[str]:
    return {s.strip() for s in settings.DISCORD_ADMIN_IDS.split(",") if s.strip()}


def _member_payload(member: Member) -> dict:
    return {
        "discord_id": member.discord_id,
        "display_name": member.display_name or member.discord_username,
        "is_admin": member.is_admin,
    }


@require_GET
def discord_login(request):
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        return JsonResponse({"error": "Discord OAuth not configured"}, status=503)
    state = secrets.token_urlsafe(16)
    signed_state = signing.TimestampSigner(salt=STATE_SALT).sign(state)
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "redirect_uri": settings.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "none",
    }
    response = HttpResponseRedirect(f"{DISCORD_AUTHORIZE}?{urllib.parse.urlencode(params)}")
    response.set_signed_cookie("oauth_state", signed_state, max_age=600, httponly=True, samesite="Lax")
    return response


@require_GET
def discord_callback(request):
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if not code:
        return JsonResponse({"error": "missing code"}, status=400)
    try:
        expected = signing.TimestampSigner(salt=STATE_SALT).unsign(
            request.COOKIES.get("oauth_state", ""), max_age=600
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return JsonResponse({"error": "missing or invalid state"}, status=400)
    if not secrets.compare_digest(state, expected):
        return JsonResponse({"error": "state mismatch"}, status=400)

    form = urllib.parse.urlencode({
        "client_id": settings.DISCORD_CLIENT_ID,
        "client_secret": settings.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.DISCORD_REDIRECT_URI,
    }).encode()
    try:
        with urllib.request.urlopen(  # noqa: S310 (https endpoint, fixed)
            urllib.request.Request(
                DISCORD_TOKEN, data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
            timeout=10,
        ) as resp:
            token_data = json.loads(resp.read())
        access = token_data.get("access_token", "")
        if not access:
            return JsonResponse({"error": "token exchange failed"}, status=502)
        with urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(DISCORD_ME, headers={"Authorization": f"Bearer {access}"}),
            timeout=10,
        ) as resp:
            user = json.loads(resp.read())
    except Exception:
        return JsonResponse({"error": "Discord API unreachable"}, status=502)

    discord_id = str(user.get("id", ""))
    if not discord_id:
        return JsonResponse({"error": "no user id from Discord"}, status=502)

    member, _ = Member.objects.get_or_create(discord_id=discord_id)
    member.discord_username = user.get("username", member.discord_username)
    member.display_name = user.get("global_name") or member.display_name or user.get("username", "")
    if user.get("avatar"):
        member.avatar_url = (
            f"https://cdn.discordapp.com/avatars/{discord_id}/{user['avatar']}.png"
        )
    if discord_id in _admin_ids():
        member.is_admin = True
    member.last_login_at = timezone.now()
    member.save()

    session_token = issue_session_token(member)
    if settings.FRONTEND_URL:
        return HttpResponseRedirect(f"{settings.FRONTEND_URL}#token={session_token}")
    return JsonResponse({"token": session_token, "member": _member_payload(member)})


@require_GET
def whoami(request):
    member = getattr(request, "session_member", None)
    if not member:
        return JsonResponse({"error": "not logged in"}, status=401)
    return JsonResponse(_member_payload(member))
