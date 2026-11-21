"""Authentik OIDC login for the website (hand-rolled authorization-code flow).

Plain `requests` on purpose: Cloudflare in front of the org's Authentik blocks
some OIDC client libraries. The redirect URI is built from SITE_URL, never from
the request host (the pod's proxy does not forward Host).
"""
import logging
import secrets
import urllib.parse

import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods

from .directory import sync_person_by_email
from .models import Member

log = logging.getLogger(__name__)
TIMEOUT = 10
_oidc_config: dict = {}


def _oidc() -> dict:
    """Discovery document, cached in-process."""
    if not _oidc_config:
        url = settings.AUTHENTIK_ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        _oidc_config.update(r.json())
    return _oidc_config


def _redirect_uri() -> str:
    return settings.SITE_URL + "/auth/callback/"


def _safe_next(value: str) -> str:
    ok = value.startswith("/") and url_has_allowed_host_and_scheme(value, allowed_hosts=None)
    return value if ok else "/"


def not_in_directory():
    return HttpResponse("You're not in the Monash Automation directory.", status=403,
                        content_type="text/plain; charset=utf-8")


def _log_in(request, member: Member, next_url: str = "/"):
    request.session.cycle_key()
    request.session["member_id"] = member.pk
    member.last_login_at = timezone.now()
    member.save(update_fields=["last_login_at"])
    return redirect(next_url)


def _member_for_email(email: str) -> Member | None:
    """Active member by email, pulling them from the directory if not yet synced."""
    member = Member.objects.filter(email__iexact=email, archived_at__isnull=True).first()
    if not member:
        member = sync_person_by_email(email)
    return member if member and not member.archived_at else None


@require_GET
def login(request):
    next_url = _safe_next(request.GET.get("next", ""))
    if settings.DEBUG and settings.DEV_LOGIN_EMAIL:  # dev shortcut; never on in production
        member = _member_for_email(settings.DEV_LOGIN_EMAIL)
        return _log_in(request, member, next_url) if member else not_in_directory()
    if not (settings.AUTHENTIK_ISSUER and settings.AUTHENTIK_CLIENT_ID):
        return HttpResponse("Authentik login is not configured.", status=503)
    try:
        authorize = _oidc()["authorization_endpoint"]
    except (requests.RequestException, ValueError, KeyError):
        return HttpResponse("Login provider unreachable.", status=502)
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    request.session["oauth_next"] = next_url
    params = {
        "client_id": settings.AUTHENTIK_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return HttpResponseRedirect(f"{authorize}?{urllib.parse.urlencode(params)}")


@require_GET
def callback(request):
    expected = request.session.pop("oauth_state", "")
    next_url = _safe_next(request.session.pop("oauth_next", "/"))
    code, state = request.GET.get("code", ""), request.GET.get("state", "")
    if not (code and expected and secrets.compare_digest(state.encode(), expected.encode())):
        return HttpResponse("Invalid login state. Please try again.", status=400)
    try:
        cfg = _oidc()
        token = requests.post(cfg["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(),
            "client_id": settings.AUTHENTIK_CLIENT_ID,
            "client_secret": settings.AUTHENTIK_CLIENT_SECRET,
        }, timeout=TIMEOUT)
        token.raise_for_status()
        info = requests.get(cfg["userinfo_endpoint"], timeout=TIMEOUT, headers={
            "Authorization": f"Bearer {token.json()['access_token']}"})
        info.raise_for_status()
        email = (info.json().get("email") or "").strip()
    except (requests.RequestException, ValueError, KeyError):
        log.warning("authentik: code exchange failed")
        return HttpResponse("Login failed. Please try again.", status=502)
    member = _member_for_email(email) if email else None
    return _log_in(request, member, next_url) if member else not_in_directory()


@require_http_methods(["GET", "POST"])
def logout(request):
    request.session.flush()
    return redirect("login")
