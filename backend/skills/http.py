"""Shared HTTP helpers: safe JSON body parsing, admin gating, error payloads.

Hardened against the boundary classes found in discordbot (M-038/M-040/M-041/
M-075: malformed JSON or non-object bodies 500-ing; M-036/M-094: unvalidated
point amounts): every body parse returns a 400 instead of raising, and every
numeric input is range-checked before touching the DB.
"""
import json
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse

MAX_POINTS = Decimal("9999.99")
MAX_DESCRIPTION = 300


def parse_json_object_body(request):
    """Return (body_dict, None) or (None, error_response). Never raises."""
    try:
        raw = request.body
    except Exception:
        return None, JsonResponse({"error": "unreadable body"}, status=400)
    if not raw:
        return {}, None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, JsonResponse({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return None, JsonResponse({"error": "body must be a JSON object"}, status=400)
    return body, None


def clean_points(value):
    """Return (Decimal, None) or (None, error_message). Positive, bounded."""
    if isinstance(value, bool) or value is None:
        return None, "points must be numeric"
    try:
        points = Decimal(str(value))
    except InvalidOperation:
        return None, "points must be numeric"
    if points <= 0:
        return None, "points must be positive"
    if points > MAX_POINTS:
        return None, f"points must be <= {MAX_POINTS}"
    return points, None


def clean_str(value, max_len: int, *, field: str):
    """Return (str, None) or (None, error_message)."""
    if value is None:
        return "", None
    if not isinstance(value, str):
        return None, f"{field} must be a string"
    if len(value) > max_len:
        return None, f"{field} must be <= {max_len} chars"
    return value, None


def clean_int(value, lo: int, hi: int, *, field: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{field} must be an integer"
    iv = int(value)
    if iv < lo or iv > hi:
        return None, f"{field} must be between {lo} and {hi}"
    return iv, None


def require_caller(request):
    caller = getattr(request, "caller_member", None) or getattr(request, "session_member", None)
    if not caller:
        return None, JsonResponse(
            {"error": "caller required (X-Caller-Member header or X-Auth-Token session)"},
            status=400,
        )
    return caller, None


def require_admin(request):
    caller, err = require_caller(request)
    if err:
        return None, err
    if not caller.is_admin:
        return None, JsonResponse({"error": "admin only"}, status=403)
    return caller, None
