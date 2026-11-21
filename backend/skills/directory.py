"""People Directory sync: the directory is the source of truth for who exists and their org roles.

Polling only (the directory has no webhooks). Members are archived, never deleted.
"""
import logging
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Member, SkillRating

log = logging.getLogger(__name__)
TIMEOUT = 20


class DirectoryError(Exception):
    pass


def _session() -> requests.Session:
    if not settings.DIRECTORY_API_KEY:
        raise DirectoryError("DIRECTORY_API_KEY is not set")
    s = requests.Session()
    # /api/people takes X-API-Key, /api/v1/users takes a bearer token; same key.
    s.headers.update({
        "X-API-Key": settings.DIRECTORY_API_KEY,
        "Authorization": f"Bearer {settings.DIRECTORY_API_KEY}",
    })
    return s


def _lookup(session, email: str) -> dict | None:
    """v1 user record for an email (has role/accountKind), or None if it can't be fetched."""
    try:
        r = session.get(f"{settings.DIRECTORY_URL}/api/v1/users", params={"email": email}, timeout=TIMEOUT)
        r.raise_for_status()
        users = r.json().get("users", [])
    except (requests.RequestException, ValueError):
        log.warning("directory: per-person lookup failed")
        return None
    for u in users:
        if (u.get("email") or "").lower() == email.lower():
            return u
    return users[0] if len(users) == 1 else None


def _apply(person: dict, info: dict | None) -> tuple[Member, str]:
    """Upsert one person; returns (member, "created"|"updated"). `info` is the v1 record or None."""
    did = str(person["id"])
    email = (person.get("email") or "").strip()
    discord_id = str(person.get("discordUserId") or "") or None
    member = Member.objects.filter(directory_id=did).first()
    if not member and discord_id:
        member = Member.objects.filter(discord_id=discord_id, directory_id__isnull=True).first()
    if not member and email:
        member = Member.objects.filter(email__iexact=email, directory_id__isnull=True).first()
    created = member is None
    if created:
        member = Member(directory_id=did)
    member.directory_id = did
    if discord_id and Member.objects.filter(discord_id=discord_id).exclude(pk=member.pk).exists():
        log.warning("directory: discord_id collision for person %s; leaving discord_id unchanged", did)
        discord_id = None
    if discord_id:
        member.discord_id = discord_id
    if person.get("discordUsername"):
        member.discord_username = person["discordUsername"]
    if email:
        member.email = email
    member.display_name = person.get("name") or member.display_name
    avatar = person.get("avatarUrl") or ""
    if avatar:
        member.avatar_url = urljoin(settings.DIRECTORY_URL + "/", avatar)
    member.positions = person.get("positions") or []
    member.status = person.get("status") or member.status
    if info is not None:
        member.is_admin = info.get("role") == "admin"
    if member.status == "former":
        member.archived_at = member.archived_at or timezone.now()
    else:
        member.archived_at = None
    member.save()
    if created:
        SkillRating.objects.get_or_create(member=member)
    return member, "created" if created else "updated"


def sync_directory() -> dict:
    """Full sync. Returns {created, updated, archived, restored, skipped}; aborts (DirectoryError) if the response looks wrong."""
    session = _session()
    try:
        r = session.get(f"{settings.DIRECTORY_URL}/api/people", timeout=TIMEOUT)
        r.raise_for_status()
        people = r.json()["people"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise DirectoryError(f"directory fetch failed: {type(exc).__name__}") from exc
    linked = Member.objects.filter(directory_id__isnull=False, archived_at__isnull=True).count()
    if len(people) * 2 < linked:
        raise DirectoryError(f"directory returned {len(people)} people but {linked} are linked; refusing to sync")

    infos = {str(p["id"]): _lookup(session, p["email"]) if p.get("email") else None for p in people}
    counts = {"created": 0, "updated": 0, "archived": 0, "restored": 0, "skipped": 0}
    seen = set()
    with transaction.atomic():
        for p in people:
            info = infos[str(p["id"])]
            if info and info.get("accountKind") == "role":
                counts["skipped"] += 1
                continue
            seen.add(str(p["id"]))
            was = Member.objects.filter(directory_id=str(p["id"])).values_list("archived_at", flat=True).first()
            member, outcome = _apply(p, info)
            counts[outcome] += 1
            if was and not member.archived_at:
                counts["restored"] += 1
            elif member.archived_at and not was:
                counts["archived"] += 1
        gone = Member.objects.filter(directory_id__isnull=False, archived_at__isnull=True).exclude(directory_id__in=seen)
        counts["archived"] += gone.update(archived_at=timezone.now())
    return counts


def sync_person_by_email(email: str) -> Member | None:
    """Pull one person from the directory (first login before the next full sync). None if unknown/unreachable."""
    if not email:
        return None
    try:
        info = _lookup(_session(), email)
    except DirectoryError:
        return None
    if not info or info.get("accountKind") == "role" or not info.get("id"):
        return None
    return _apply(info, info)[0]
