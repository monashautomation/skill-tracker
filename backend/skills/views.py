"""API endpoints for the skill tracker.

Auth: middleware enforces the shared bearer token (server-to-server) or a
Discord-OAuth session token (browser). Bodies go through http.py helpers so
malformed input is a 400, never a 500, and point amounts are range-checked
before touching the DB.
"""
from datetime import date

from django.db.models import Prefetch, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .http import (
    MAX_DESCRIPTION,
    clean_int,
    clean_points,
    clean_str,
    parse_json_object_body,
    require_admin,
    require_caller,
)
from .models import (
    ActionItem,
    EquipmentGroup,
    Induction,
    InductionType,
    Lesson,
    LessonCompletion,
    Member,
    PointsEntry,
    QuizAttempt,
    RoleRule,
    SkillRating,
)
from .services import (
    award_points,
    complete_lesson,
    grade_quiz,
    points_total,
    record_quiz_attempt,
)


def _member_payload(member: Member) -> dict:
    return {
        "discord_id": member.discord_id,
        "display_name": member.display_name or member.discord_username,
        "discord_username": member.discord_username,
        "email": member.email,
        "is_admin": member.is_admin,
        "points_total": str(points_total(member)),
    }


def _rating_payload(rating: SkillRating | None) -> dict:
    if rating is None:
        return {axis: None for axis in SkillRating.AXES}
    return rating.as_dict()


def _quiz_public(quiz) -> dict:
    return {
        "id": quiz.id,
        "pass_mark": quiz.pass_mark,
        "questions": [
            {"prompt": q.get("question"), "options": q.get("options", [])}
            for q in (quiz.questions or [])
        ],
    }


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


# --------------------------------------------------------------- members

@require_POST
def upsert_member(request):
    body, err = parse_json_object_body(request)
    if err:
        return err
    discord_id, err = clean_str(body.get("discord_id"), 32, field="discord_id")
    if err or not discord_id:
        return JsonResponse({"error": err or "discord_id required"}, status=400)
    email, err = clean_str(body.get("email"), 254, field="email")
    if err:
        return JsonResponse({"error": err}, status=400)
    display, err = clean_str(body.get("display_name"), 200, field="display_name")
    if err:
        return JsonResponse({"error": err}, status=400)
    member, created = Member.objects.get_or_create(
        discord_id=discord_id,
        defaults={"email": email, "display_name": display},
    )
    if email:
        member.email = email
    if display:
        member.display_name = display
    member.save()
    return JsonResponse(_member_payload(member), status=201 if created else 200)


@require_GET
def get_member(request, discord_id: str):
    member = Member.objects.filter(discord_id=discord_id).first()
    if not member:
        return JsonResponse({"error": "not found"}, status=404)
    completions = (
        LessonCompletion.objects.filter(member=member)
        .select_related("lesson", "lesson__group")
    )
    inductions = Induction.objects.filter(member=member).select_related("induction_type")
    rating = getattr(member, "skill_rating", None)
    return JsonResponse(
        {
            **_member_payload(member),
            "skills": _rating_payload(rating),
            "lessons_completed": [
                {
                    "lesson_id": c.lesson_id,
                    "title": c.lesson.title,
                    "group": c.lesson.group.key,
                    "completed_at": c.completed_at.isoformat(),
                }
                for c in completions
            ],
            "inductions": [
                {
                    "id": i.id,
                    "type": i.induction_type.name,
                    "status": i.status,
                    "reviewed_at": i.reviewed_at.isoformat() if i.reviewed_at else None,
                }
                for i in inductions
            ],
        }
    )


@require_POST
def award_points_view(request, discord_id: str):
    member = Member.objects.filter(discord_id=discord_id).first()
    if not member:
        return JsonResponse({"error": "member not found"}, status=404)
    body, err = parse_json_object_body(request)
    if err:
        return err
    points, err = clean_points(body.get("points"))
    if err:
        return JsonResponse({"error": err}, status=400)
    description, err = clean_str(body.get("description"), MAX_DESCRIPTION, field="description")
    if err:
        return JsonResponse({"error": err}, status=400)
    source, err = clean_str(body.get("source"), 20, field="source")
    if err:
        return JsonResponse({"error": err}, status=400)
    external_ref, err = clean_str(body.get("external_ref"), 128, field="external_ref")
    if err:
        return JsonResponse({"error": err}, status=400)
    entry, created = award_points(
        member=member,
        source=source or PointsEntry.SOURCE_MANUAL,
        points=points,
        description=description,
        external_ref=external_ref or None,
    )
    return JsonResponse(
        {
            "entry_id": entry.id,
            "points_total": str(points_total(member)),
            "deduplicated": not created,
        },
        status=201 if created else 200,
    )


@require_GET
def leaderboard(request):
    members = (
        Member.objects.annotate(points=Sum("points_entries__points"))
        .filter(points__gt=0)
        .order_by("-points")[:25]
    )
    return JsonResponse(
        {
            "leaders": [
                {
                    "discord_id": m.discord_id,
                    "display_name": m.display_name or m.discord_username,
                    "points": str(m.points),
                }
                for m in members
            ]
        }
    )


@require_http_methods(["GET", "PUT"])
def skill_rating(request, discord_id: str):
    member = Member.objects.filter(discord_id=discord_id).first()
    if not member:
        return JsonResponse({"error": "member not found"}, status=404)
    if request.method == "GET":
        return JsonResponse(_rating_payload(getattr(member, "skill_rating", None)))
    caller, err = require_admin(request)
    if err:
        return err
    body, err = parse_json_object_body(request)
    if err:
        return err
    rating, _ = SkillRating.objects.get_or_create(member=member)
    updates = {}
    for axis in SkillRating.AXES:
        if axis in body:
            value, err = clean_int(body[axis], 0, 10, field=axis)
            if err:
                return JsonResponse({"error": err}, status=400)
            updates[axis] = value
    for axis, value in updates.items():
        setattr(rating, axis, value)
    if updates:
        rating.updated_by = caller
        rating.save(update_fields=[*updates, "updated_by"])
    return JsonResponse(_rating_payload(rating))


# -------------------------------------------------------------- training

@require_GET
def catalog(request):
    """Public course catalog: equipment groups -> published lessons."""
    groups = (
        EquipmentGroup.objects.filter(active=True)
        .prefetch_related(
            Prefetch("lessons", queryset=Lesson.objects.filter(published=True))
        )
    )
    return JsonResponse(
        {
            "equipment": [
                {
                    "key": g.key,
                    "name": g.name,
                    "lessons": [
                        {
                            "id": l.id,
                            "slug": l.slug,
                            "title": l.title,
                            "summary": l.summary,
                            "points": str(l.points),
                            "requires_induction": (
                                l.requires_induction.name if l.requires_induction else None
                            ),
                        }
                        for l in g.lessons.all()
                    ],
                }
                for g in groups
            ],
            "induction_types": [
                {"id": t.id, "name": t.name}
                for t in InductionType.objects.filter(active=True)
            ],
        }
    )


@require_GET
def lesson_detail(request, lesson_id: int):
    lesson = Lesson.objects.filter(pk=lesson_id, published=True).select_related("group").first()
    if not lesson:
        return JsonResponse({"error": "not found"}, status=404)
    quiz = getattr(lesson, "quiz", None)
    return JsonResponse(
        {
            "id": lesson.id,
            "slug": lesson.slug,
            "title": lesson.title,
            "summary": lesson.summary,
            "body_markdown": lesson.body_markdown,
            "points": str(lesson.points),
            "requires_induction": (
                lesson.requires_induction.name if lesson.requires_induction else None
            ),
            "group": lesson.group.key,
            "quiz": _quiz_public(quiz) if quiz else None,
        }
    )


@require_POST
def complete_lesson_view(request, lesson_id: int):
    caller, err = require_caller(request)
    if err:
        return err
    lesson = Lesson.objects.filter(pk=lesson_id, published=True).first()
    if not lesson:
        return JsonResponse({"error": "lesson not found"}, status=404)
    if lesson.requires_induction_id:
        approved = Induction.objects.filter(
            member=caller,
            induction_type_id=lesson.requires_induction_id,
            status=Induction.STATUS_APPROVED,
        ).exists()
        if not approved:
            return JsonResponse(
                {"error": "induction required before completing this lesson"}, status=403
            )
    quiz = getattr(lesson, "quiz", None)
    quiz_score = None
    if quiz is not None:
        attempt = (
            QuizAttempt.objects.filter(member=caller, lesson=lesson, passed=True)
            .order_by("-score")
            .first()
        )
        if not attempt:
            return JsonResponse({"error": "quiz must be passed before completing"}, status=403)
        quiz_score = attempt.score
    completion, created = complete_lesson(caller, lesson, quiz_score=quiz_score)
    if not created:
        return JsonResponse(
            {"error": "already completed", "completed_at": completion.completed_at.isoformat()},
            status=409,
        )
    return JsonResponse(
        {
            "completed": True,
            "points_awarded": str(completion.points_awarded),
            "points_total": str(points_total(caller)),
        },
        status=201,
    )


@require_GET
def lesson_quiz(request, lesson_id: int):
    """Quiz questions without answer keys."""
    lesson = Lesson.objects.filter(pk=lesson_id, published=True).first()
    quiz = getattr(lesson, "quiz", None) if lesson else None
    if not quiz:
        return JsonResponse({"error": "no quiz"}, status=404)
    return JsonResponse(_quiz_public(quiz))


@require_POST
def quiz_attempt_view(request, lesson_id: int):
    """Server-side grading: answers = [option index per question]."""
    caller, err = require_caller(request)
    if err:
        return err
    lesson = Lesson.objects.filter(pk=lesson_id, published=True).first()
    quiz = getattr(lesson, "quiz", None) if lesson else None
    if not quiz:
        return JsonResponse({"error": "no quiz"}, status=404)
    body, err = parse_json_object_body(request)
    if err:
        return err
    answers = body.get("answers")
    if not isinstance(answers, list) or len(answers) != len(quiz.questions or []):
        return JsonResponse({"error": "answers must list one option index per question"}, status=400)
    for i, a in enumerate(answers):
        _, err = clean_int(a, 0, 25, field=f"answers[{i}]")
        if err:
            return JsonResponse({"error": err}, status=400)
    score, passed = grade_quiz(quiz, answers)
    record_quiz_attempt(caller, lesson, score, passed, answers)
    return JsonResponse({"score": score, "passed": passed, "pass_mark": quiz.pass_mark})


# ------------------------------------------------------------ inductions

@require_POST
def request_induction(request):
    caller, err = require_caller(request)
    if err:
        return err
    body, err = parse_json_object_body(request)
    if err:
        return err
    type_id, err = clean_int(body.get("induction_type_id"), 1, 10**9, field="induction_type_id")
    if err:
        return JsonResponse({"error": err}, status=400)
    itype = InductionType.objects.filter(pk=type_id, active=True).first()
    if not itype:
        return JsonResponse({"error": "induction type not found"}, status=404)
    induction, created = Induction.objects.get_or_create(
        member=caller, induction_type=itype,
        defaults={"status": Induction.STATUS_PENDING},
    )
    return JsonResponse(
        {"induction_id": induction.id, "status": induction.status},
        status=201 if created else 200,
    )


@require_GET
def list_inductions(request):
    caller, err = require_caller(request)
    if err:
        return err
    qs = Induction.objects.select_related("member", "induction_type")
    if not caller.is_admin:
        qs = qs.filter(member=caller)
    return JsonResponse(
        {
            "inductions": [
                {
                    "id": i.id,
                    "member": i.member.discord_id,
                    "type": i.induction_type.name,
                    "status": i.status,
                    "submitted_at": i.submitted_at.isoformat(),
                    "reviewed_at": i.reviewed_at.isoformat() if i.reviewed_at else None,
                }
                for i in qs
            ]
        }
    )


@require_http_methods(["PATCH"])
def review_induction(request, induction_id: int):
    caller, err = require_admin(request)
    if err:
        return err
    body, err = parse_json_object_body(request)
    if err:
        return err
    decision, err = clean_str(body.get("decision"), 10, field="decision")
    if err or decision not in (Induction.STATUS_APPROVED, Induction.STATUS_REJECTED):
        return JsonResponse({"error": "decision must be approved or rejected"}, status=400)
    notes, err = clean_str(body.get("notes"), 2000, field="notes")
    if err:
        return JsonResponse({"error": err}, status=400)
    induction = Induction.objects.filter(pk=induction_id).first()
    if not induction:
        return JsonResponse({"error": "not found"}, status=404)
    induction.status = decision
    induction.reviewed_at = timezone.now()
    induction.reviewed_by = caller
    if notes:
        induction.notes = notes
    induction.save()
    return JsonResponse({"induction_id": induction.id, "status": induction.status})


# ----------------------------------------------------------- action items

@require_http_methods(["GET", "POST"])
def action_items(request):
    caller, err = require_caller(request)
    if err:
        return err
    if request.method == "GET":
        qs = ActionItem.objects.select_related("member")
        status = request.GET.get("status")
        if status == "open":
            qs = qs.filter(done=False)
        elif status == "done":
            qs = qs.filter(done=True)
        return JsonResponse(
            {
                "items": [
                    {
                        "id": a.id,
                        "title": a.title,
                        "done": a.done,
                        "points": str(a.points),
                        "due_date": a.due_date.isoformat() if a.due_date else None,
                        "member": a.member.discord_id if a.member else None,
                        "notion_page_id": a.notion_page_id,
                    }
                    for a in qs[:200]
                ]
            }
        )
    body, err = parse_json_object_body(request)
    if err:
        return err
    title, err = clean_str(body.get("title"), 300, field="title")
    if err or not title:
        return JsonResponse({"error": err or "title required"}, status=400)
    due_raw, err = clean_str(body.get("due_date"), 10, field="due_date")
    if err:
        return JsonResponse({"error": err}, status=400)
    assignee = caller
    assignee_id, err = clean_str(body.get("member_discord_id"), 32, field="member_discord_id")
    if err:
        return JsonResponse({"error": err}, status=400)
    if assignee_id and assignee_id != caller.discord_id:
        if not caller.is_admin:
            return JsonResponse({"error": "only admins can assign to other members"}, status=403)
        assignee = Member.objects.filter(discord_id=assignee_id).first()
        if not assignee:
            return JsonResponse({"error": "member not found"}, status=404)
    points = None
    if body.get("points") is not None:
        points, err = clean_points(body.get("points"))
        if err:
            return JsonResponse({"error": err}, status=400)
    item = ActionItem(title=title, member=assignee, points=points)
    if due_raw:
        try:
            item.due_date = date.fromisoformat(due_raw)
        except ValueError:
            return JsonResponse({"error": "due_date must be YYYY-MM-DD"}, status=400)
    item.save()
    return JsonResponse({"id": item.id, "title": item.title, "done": item.done}, status=201)


@require_http_methods(["PATCH"])
def action_item_update(request, item_id: int):
    """Mark an action item done -> awards its points once (dedup via external_ref)."""
    caller, err = require_caller(request)
    if err:
        return err
    item = ActionItem.objects.filter(pk=item_id).select_related("member").first()
    if not item:
        return JsonResponse({"error": "not found"}, status=404)
    if not caller.is_admin and (item.member_id is None or item.member_id != caller.id):
        return JsonResponse({"error": "forbidden"}, status=403)
    body, err = parse_json_object_body(request)
    if err:
        return err
    done = body.get("done")
    if not isinstance(done, bool):
        return JsonResponse({"error": "done must be a boolean"}, status=400)
    if done and not item.done:
        item.done = True
        item.completed_at = timezone.now()
        item.save()
        if item.points and item.member:
            award_points(
                member=item.member,
                source=PointsEntry.SOURCE_ACTION_ITEM,
                points=item.points,
                description=f"Action item: {item.title}",
                external_ref=f"action_item:{item.id}",
                logged_by=caller,
            )
    elif not done:
        item.done = False
        item.completed_at = None
        item.save()
    return JsonResponse({"id": item.id, "done": item.done})


# ------------------------------------------------------------- role rules

@require_http_methods(["GET", "POST"])
def role_rules(request):
    if request.method == "GET":
        return JsonResponse(
            {
                "rules": [
                    {"id": r.id, "discord_role_id": r.discord_role_id,
                     "discord_role_name": r.discord_role_name,
                     "threshold_points": str(r.threshold_points), "active": r.active}
                    for r in RoleRule.objects.all()
                ]
            }
        )
    caller, err = require_admin(request)
    if err:
        return err
    body, err = parse_json_object_body(request)
    if err:
        return err
    role_id, err = clean_str(body.get("discord_role_id"), 32, field="discord_role_id")
    if err or not role_id:
        return JsonResponse({"error": err or "discord_role_id required"}, status=400)
    points, err = clean_points(body.get("threshold_points"))
    if err:
        return JsonResponse({"error": err}, status=400)
    name, err = clean_str(body.get("discord_role_name"), 100, field="discord_role_name")
    if err:
        return JsonResponse({"error": err}, status=400)
    rule, created = RoleRule.objects.get_or_create(
        discord_role_id=role_id,
        defaults={"threshold_points": points, "discord_role_name": name},
    )
    if not created:
        rule.threshold_points = points
        rule.discord_role_name = name
        rule.active = True
        rule.save()
    return JsonResponse(
        {"id": rule.id, "threshold_points": str(rule.threshold_points)},
        status=201 if created else 200,
    )


@require_GET
def member_quiz_attempts(request, discord_id: str):
    caller, err = require_caller(request)
    if err:
        return err
    if caller.discord_id != discord_id and not caller.is_admin:
        return JsonResponse({"error": "forbidden"}, status=403)
    attempts = QuizAttempt.objects.filter(member__discord_id=discord_id).select_related("lesson")[:50]
    return JsonResponse(
        {
            "attempts": [
                {"lesson": a.lesson.title, "score": a.score, "passed": a.passed,
                 "created_at": a.created_at.isoformat()}
                for a in attempts
            ]
        }
    )
