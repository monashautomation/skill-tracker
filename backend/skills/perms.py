"""Who may change what. Roles come from the People Directory (Member.positions / is_admin)."""
from django.conf import settings

from .models import Member


def is_editor(member: Member) -> bool:
    """Admin, or holds a directory position matching SKILLS_EDITOR_POSITIONS (substring, case-insensitive)."""
    if member.is_admin:
        return True
    patterns = [p.lower() for p in settings.SKILLS_EDITOR_POSITIONS]
    return any(p in pos.lower() for pos in member.positions or [] for p in patterns)


def can_edit_skills(actor: Member, target: Member) -> bool:
    """Granular skills: own profile, or any editor."""
    return actor.pk == target.pk or is_editor(actor)


def can_edit_ratings(actor: Member, target: Member) -> bool:
    """6-axis hexagon is assessed by editors, never self-set."""
    return is_editor(actor)


def can_review_inductions(actor: Member) -> bool:
    return actor.is_admin


def editors_summary() -> dict:
    """For the UI: who can manually change things (non-archived members only)."""
    active = Member.objects.filter(archived_at__isnull=True).order_by("display_name")
    return {
        "positions": list(settings.SKILLS_EDITOR_POSITIONS),
        "admins": list(active.filter(is_admin=True)),
        "editors": [m for m in active if is_editor(m)],
    }
