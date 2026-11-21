"""Server-rendered website pages. Business rules live in perms.py / services.py; this only adapts forms."""
import math
from decimal import Decimal

import requests
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import perms
from .directory import DirectoryError, sync_directory
from .http import clean_int
from .models import Induction, InductionType, Member, MemberSkill, Skill, SkillRating
from .services import upsert_skill_items
from .site_auth import login_required_member

LEVELS = ["Curious", "Beginner", "Basic", "Competent", "Advanced", "Expert"]
AXIS_LABELS = {"project_mgmt": "Project mgmt"}


# ----------------------------------------------------------------- helpers

def radar_points(values, max_value=10, radius=100.0, cx=0.0, cy=0.0) -> str:
    """SVG polygon points: one vertex per value, first axis straight up, clockwise.

    >>> radar_points([10, 10, 10, 10], radius=10)
    '0.0,-10.0 10.0,0.0 0.0,10.0 -10.0,0.0'
    >>> radar_points([5, 0], radius=10)
    '0.0,-5.0 0.0,0.0'
    """
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        a = 2 * math.pi * i / n
        r = radius * v / max_value
        x, y = cx + r * math.sin(a), cy - r * math.cos(a)
        pts.append(f"{round(x, 1) + 0.0:.1f},{round(y, 1) + 0.0:.1f}")
    return " ".join(pts)


def radar_geometry(rating: SkillRating | None) -> dict:
    """Everything the template needs to draw the 6-axis graph."""
    values = [getattr(rating, a, 0) if rating else 0 for a in SkillRating.AXES]
    cx, cy, radius = 170, 140, 88
    n = len(values)
    labels = []
    for i, axis in enumerate(SkillRating.AXES):
        a = 2 * math.pi * i / n
        x, y = cx + (radius + 16) * math.sin(a), cy - (radius + 16) * math.cos(a)
        anchor = "middle" if abs(math.sin(a)) < 0.1 else ("start" if math.sin(a) > 0 else "end")
        labels.append({"x": round(x, 1), "y": round(y + 4, 1), "anchor": anchor, "value": values[i],
                       "text": AXIS_LABELS.get(axis, axis.capitalize())})
    return {
        "empty": not any(values),
        "cx": cx, "cy": cy,
        "shape": radar_points(values, 10, radius, cx, cy),
        "rings": [radar_points([k] * n, 10, radius, cx, cy) for k in (2.5, 5, 7.5, 10)],
        "spokes": [dict(zip("xy", pt.split(","))) for pt in radar_points([10] * n, 10, radius, cx, cy).split()],
        "labels": labels,
    }


def primary_position(member: Member) -> str:
    """The most senior-sounding directory position (an editor-pattern match), else the first."""
    positions = member.positions or []
    for pos in positions:
        if any(p.lower() in pos.lower() for p in settings.SKILLS_EDITOR_POSITIONS):
            return pos
    return positions[0] if positions else "Member"


def _decorate(m: Member) -> Member:
    """Attach card fields (expects skill_items/inductions/points_entries/lesson_completions prefetched)."""
    m.primary = primary_position(m)
    m.status_tag = "" if m.status == "active" or m.status.lower() == m.primary.lower() else m.status
    m.name = m.display_name or m.discord_username or "Unnamed"
    m.initials = "".join(w[0] for w in m.name.split()[:2]).upper() or "?"
    m.pts = sum((e.points for e in m.points_entries.all()), Decimal("0"))
    m.n_ind = sum(1 for i in m.inductions.all() if i.status == Induction.STATUS_APPROVED)
    m.n_skills = sum(1 for s in m.skill_items.all() if s.level > 0)
    m.n_lessons = len(m.lesson_completions.all())
    m.is_editor = perms.is_editor(m)
    return m


def _members(archived=False):
    qs = Member.objects.all() if archived else Member.objects.filter(archived_at__isnull=True)
    return qs.prefetch_related("skill_items__skill", "inductions", "points_entries", "lesson_completions") \
             .order_by("display_name")


@login_required_member
def photo(request, pk):
    """Headshot for a member. Directory photos sit in private storage, so they are fetched
    server-side with the directory API key; any other avatar URL is redirected to as-is."""
    url = get_object_or_404(Member, pk=pk).avatar_url
    if not url:
        raise Http404
    if "/avatars/" not in url:
        return redirect(url)
    # ponytail: no local cache, the browser caches for a day; add one if the directory feels the load
    try:
        r = requests.get(
            f"{settings.DIRECTORY_URL}/api/avatars/{url.split('/avatars/', 1)[1]}",
            headers={"X-API-Key": settings.DIRECTORY_API_KEY}, timeout=10,
        )
    except requests.RequestException:
        raise Http404
    if r.status_code != 200 or not r.headers.get("Content-Type", "").startswith("image/"):
        raise Http404
    resp = HttpResponse(r.content, content_type=r.headers["Content-Type"])
    resp["Cache-Control"] = "private, max-age=86400"
    return resp


# ------------------------------------------------------------------- pages

def login_page(request):
    if request.member:
        return redirect("roster")
    return render(request, "skills/login.html", {"next": request.GET.get("next", "")})


@login_required_member
def roster(request):
    viewer = request.member
    show_archived = viewer.is_admin and request.GET.get("archived") == "1"
    q = request.GET.get("q", "").strip()
    position = request.GET.get("position", "")
    skill = request.GET.get("skill", "")
    mode = "learn" if request.GET.get("mode") == "learn" else "can"
    everyone = [_decorate(m) for m in _members(show_archived)]
    positions = sorted({p for m in everyone for p in m.positions or []})

    def keep(m):
        if q and q.lower() not in m.name.lower():
            return False
        if position and position not in (m.positions or []):
            return False
        if skill:
            hit = [s for s in m.skill_items.all() if s.skill.name == skill]
            return bool(hit) and (hit[0].wants_to_learn if mode == "learn" else hit[0].level > 0)
        return True

    people = [m for m in everyone if keep(m)]
    skill_names = Skill.objects.filter(member_skills__isnull=False).distinct().values_list("name", flat=True)
    return render(request, "skills/roster.html", {
        "people": people, "total": len(everyone), "positions": positions, "skill_names": skill_names,
        "q": q, "position": position, "skill": skill, "mode": mode, "show_archived": show_archived,
        "filtered": bool(q or position or skill),
    })


@login_required_member
def profile(request, pk):
    viewer = request.member
    target = get_object_or_404(_members(archived=True), pk=pk)
    if target.archived_at and not viewer.is_admin:
        raise Http404
    _decorate(target)
    own = viewer.pk == target.pk
    editable = not target.archived_at
    items = sorted(target.skill_items.all(), key=lambda s: (-s.level, s.skill.name.lower()))
    inductions = [i for i in target.inductions.all()
                  if i.status != Induction.STATUS_REJECTED or own or viewer.is_admin]
    held = {i.induction_type_id for i in target.inductions.all()}
    summary = perms.editors_summary()
    return render(request, "skills/profile.html", {
        "p": target, "own": own,
        "rating": getattr(target, "skill_rating", None),
        "radar": radar_geometry(getattr(target, "skill_rating", None)),
        "items": items, "levels": list(enumerate(LEVELS)),
        "catalog": Skill.objects.all(),
        "categories": sorted({c for c in Skill.objects.values_list("category", flat=True) if c}),
        "inductions": sorted(inductions, key=lambda i: (i.status != Induction.STATUS_APPROVED, i.induction_type.name)),
        "available_types": InductionType.objects.filter(active=True).exclude(pk__in=held),
        "recent_points": list(target.points_entries.all()[:5]),
        "can_skills": editable and perms.can_edit_skills(viewer, target),
        "can_ratings": editable and perms.can_edit_ratings(viewer, target),
        "can_request": editable and own,
        "axes": [(a, AXIS_LABELS.get(a, a.capitalize()), getattr(target, "skill_rating", None) and
                  getattr(target.skill_rating, a)) for a in SkillRating.AXES],
        "editors": summary, "editor_ids": {m.pk for m in summary["editors"]},
        "patterns": ", ".join(summary["positions"]),
    })


@login_required_member
def skills_map(request):
    groups: dict[str, dict] = {}
    for m in map(_decorate, _members()):
        for ms in m.skill_items.all():
            if ms.level == 0 and not ms.wants_to_learn:
                continue
            skill = ms.skill
            g = groups.setdefault(skill.category or "Uncategorised", {}).setdefault(
                skill.pk, {"skill": skill, "can": [], "learn": []})
            if ms.level > 0:
                g["can"].append((ms.level, m))
            if ms.wants_to_learn:
                g["learn"].append(m)
    out = []
    for cat in sorted(groups, key=lambda c: (c == "Uncategorised", c.lower())):
        rows = sorted(groups[cat].values(), key=lambda g: g["skill"].name.lower())
        for r in rows:
            r["can"].sort(key=lambda t: (-t[0], t[1].name))
        out.append({"category": cat, "rows": rows})
    return render(request, "skills/skills_map.html", {"groups": out})


@login_required_member
def inductions(request):
    viewer = request.member
    qs = Induction.objects.select_related("member", "induction_type", "reviewed_by")
    ctx = {"available_types": InductionType.objects.filter(active=True).exclude(
        pk__in=viewer.inductions.values_list("induction_type_id", flat=True))}
    if perms.can_review_inductions(viewer):
        ctx["pending"] = qs.filter(status=Induction.STATUS_PENDING, member__archived_at__isnull=True).order_by("submitted_at")
        ctx["reviewed"] = qs.exclude(status=Induction.STATUS_PENDING).order_by("-reviewed_at")[:15]
    ctx["mine"] = qs.filter(member=viewer)
    return render(request, "skills/inductions.html", ctx)


# ----------------------------------------------------------- POST actions
# Every view re-checks permission; templates only hide buttons.

def _editable_target(request, pk):
    target = get_object_or_404(Member, pk=pk)
    if target.archived_at:
        messages.error(request, "Archived profiles can't be edited.")
        return None
    return target


@login_required_member
@require_POST
def skill_save(request, pk):
    target = get_object_or_404(Member, pk=pk)
    if not perms.can_edit_skills(request.member, target):
        messages.error(request, "You can't edit this person's skills.")
        return redirect("profile", pk)
    if _editable_target(request, pk) is None:
        return redirect("profile", pk)
    name = request.POST.get("name", "").strip()
    category = request.POST.get("category", "").strip()
    try:
        level = int(request.POST.get("level", ""))
    except ValueError:
        level = -1
    if not name or len(name) > 100 or len(category) > 100 or clean_int(level, 0, 5, field="level")[1]:
        messages.error(request, "Give the skill a name (up to 100 characters) and a level from 0 to 5.")
    else:
        upsert_skill_items(target, [{"name": name, "category": category, "level": level,
                                     "wants_to_learn": request.POST.get("wants_to_learn") == "on"}],
                           actor=request.member, source=MemberSkill.SOURCE_MANUAL)
        messages.success(request, f"Saved {name}.")
    return redirect("profile", pk)


@login_required_member
@require_POST
def skill_remove(request, pk, skill_id):
    target = get_object_or_404(Member, pk=pk)
    if not perms.can_edit_skills(request.member, target):
        messages.error(request, "You can't edit this person's skills.")
    elif _editable_target(request, pk) is not None:
        deleted, _ = MemberSkill.objects.filter(member=target, skill_id=skill_id).delete()
        messages.success(request, "Skill removed." if deleted else "That skill was already gone.")
    return redirect("profile", pk)


@login_required_member
@require_POST
def ratings_save(request, pk):
    target = get_object_or_404(Member, pk=pk)
    if not perms.can_edit_ratings(request.member, target):
        messages.error(request, "Only leads and admins can set ratings.")
        return redirect("profile", pk)
    if _editable_target(request, pk) is None:
        return redirect("profile", pk)
    values = {}
    for axis in SkillRating.AXES:
        try:
            raw = int(request.POST.get(axis, ""))
        except ValueError:
            raw = -1
        v, err = clean_int(raw, 0, 10, field=axis)
        if err:
            messages.error(request, "Ratings must be whole numbers from 0 to 10.")
            return redirect("profile", pk)
        values[axis] = v
    rating, _ = SkillRating.objects.get_or_create(member=target)
    for axis, v in values.items():
        setattr(rating, axis, v)
    rating.updated_by = request.member
    rating.save()
    messages.success(request, "Ratings updated.")
    return redirect("profile", pk)


@login_required_member
@require_POST
def induction_request(request):
    """Request an induction for yourself (same rules as POST /api/inductions/)."""
    itype = InductionType.objects.filter(pk=request.POST.get("induction_type") or 0, active=True).first()
    if not itype:
        messages.error(request, "Pick an induction from the list.")
    else:
        ind, created = Induction.objects.get_or_create(member=request.member, induction_type=itype)
        messages.success(request, f"Requested {itype.name}." if created
                         else f"{itype.name} is already {ind.status}.")
    if request.POST.get("back") == "inductions":
        return redirect("inductions")
    return redirect("profile", request.member.pk)


@login_required_member
@require_POST
def induction_review(request, pk):
    if not perms.can_review_inductions(request.member):
        messages.error(request, "Only admins can review inductions.")
        return redirect("inductions")
    decision = request.POST.get("decision")
    if decision not in (Induction.STATUS_APPROVED, Induction.STATUS_REJECTED):
        messages.error(request, "Choose approve or reject.")
        return redirect("inductions")
    ind = get_object_or_404(Induction, pk=pk)
    ind.status, ind.reviewed_at, ind.reviewed_by = decision, timezone.now(), request.member
    notes = request.POST.get("notes", "").strip()[:2000]
    if notes:
        ind.notes = notes
    ind.save()
    messages.success(request, f"{ind.induction_type.name} for {ind.member}: {decision}.")
    return redirect("inductions")


@login_required_member
@require_POST
def directory_sync_now(request):
    if not request.member.is_admin:
        messages.error(request, "Only admins can sync the directory.")
        return redirect("roster")
    try:
        c = sync_directory()
        messages.success(request, "Directory synced: " + ", ".join(f"{v} {k}" for k, v in c.items()) + ".")
    except DirectoryError as exc:
        messages.error(request, f"Directory sync failed: {exc}")
    return redirect("roster")
