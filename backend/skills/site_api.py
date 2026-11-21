"""Admin page for API keys + API reference. Admins only; the raw key is shown once."""
import hashlib
import secrets

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .http import clean_str
from .models import ApiKey
from .site_auth import login_required_member


@login_required_member
def api_keys(request):
    if not request.member.is_admin:
        return HttpResponseForbidden("Admins only.")
    return render(request, "skills/api.html", {
        "keys": ApiKey.objects.select_related("created_by").order_by("-created_at"),
        "new_key": request.session.pop("new_api_key", None),
    })


@login_required_member
@require_POST
def api_key_create(request):
    if not request.member.is_admin:
        return HttpResponseForbidden("Admins only.")
    name, err = clean_str(request.POST.get("name", "").strip(), 100, field="name")
    scope = request.POST.get("scope")
    if err or not name or scope not in (ApiKey.SCOPE_READ, ApiKey.SCOPE_WRITE):
        messages.error(request, "Give the key a name (up to 100 characters) and a scope.")
        return redirect("api_keys")
    raw = "stk_" + secrets.token_hex(24)
    ApiKey.objects.create(
        name=name, scope=scope, created_by=request.member, key_prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    request.session["new_api_key"] = raw
    return redirect("api_keys")


@login_required_member
@require_POST
def api_key_revoke(request, pk):
    if not request.member.is_admin:
        return HttpResponseForbidden("Admins only.")
    if ApiKey.objects.filter(pk=pk, revoked_at__isnull=True).update(revoked_at=timezone.now()):
        messages.success(request, "Key revoked.")
    return redirect("api_keys")
