"""Shared business logic used by both the API and the training-site views."""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    LessonCompletion,
    MemberSkill,
    PointsClaim,
    PointsEntry,
    QuizAttempt,
    RoleRule,
    Skill,
)


def points_total(member) -> Decimal:
    total = member.points_entries.aggregate(t=Sum("points"))["t"]
    return (total or Decimal("0")).quantize(Decimal("0.01"))


def award_points(*, member, source, points, description="", earned_on=None,
                 external_ref=None, logged_by=None):
    """Create a ledger entry; dedup on external_ref (returns existing row)."""
    if external_ref:
        existing = PointsEntry.objects.filter(external_ref=external_ref).first()
        if existing:
            return existing, False
    entry = PointsEntry.objects.create(
        member=member, source=source, points=points,
        description=description, earned_on=earned_on or date.today(),
        external_ref=external_ref, logged_by=logged_by,
    )
    return entry, True


def submit_points_claim(*, member, points, description):
    """A member's own claim; counts only once leadership approves it."""
    return PointsClaim.objects.create(member=member, points=points, description=description)


def review_points_claim(claim, *, decision, reviewer, note=""):
    """Approve (creates the PointsEntry) or reject a pending claim. Returns False if already reviewed."""
    with transaction.atomic():
        claim = PointsClaim.objects.select_for_update().get(pk=claim.pk)
        if claim.status != PointsClaim.STATUS_PENDING:
            return False
        if decision == PointsClaim.STATUS_APPROVED:
            claim.entry, _ = award_points(
                member=claim.member, source=PointsEntry.SOURCE_MANUAL, points=claim.points,
                description=claim.description, logged_by=reviewer,
            )
        claim.status, claim.reviewed_by, claim.reviewed_at = decision, reviewer, timezone.now()
        claim.review_note = note[:300]
        claim.save()
    return True


def grade_quiz(quiz, answers: list) -> tuple[int, bool]:
    """answers: list of chosen option index per question. Returns (percent, passed)."""
    questions = quiz.questions or []
    if not questions:
        return 0, False
    correct = 0
    for i, q in enumerate(questions):
        if i < len(answers) and answers[i] == q.get("answer_index"):
            correct += 1
    score = round(correct * 100 / len(questions))
    return score, score >= quiz.pass_mark


def record_quiz_attempt(member, lesson, score, passed, answers) -> QuizAttempt:
    return QuizAttempt.objects.create(
        member=member, lesson=lesson, score=score, passed=passed, answers=answers,
    )


def complete_lesson(member, lesson, *, quiz_score=None):
    """Award lesson points once; returns (completion, created)."""
    completion, created = LessonCompletion.objects.get_or_create(
        member=member, lesson=lesson,
        defaults={"quiz_score": quiz_score, "points_awarded": lesson.points},
    )
    if created and completion.points_awarded:
        award_points(
            member=member, source=PointsEntry.SOURCE_LESSON,
            points=completion.points_awarded,
            description=f"Completed lesson: {lesson.title}",
            external_ref=f"lesson:{lesson.id}:{member.id}",
        )
    return completion, created


def role_for_member(member):
    """Highest active RoleRule the member's points qualify for."""
    total = points_total(member)
    return (
        RoleRule.objects.filter(active=True, threshold_points__lte=total)
        .order_by("-threshold_points")
        .first()
    )


def upsert_skill_items(member, items, *, actor=None, source=MemberSkill.SOURCE_MANUAL):
    """Upsert validated [{name, category, level?, wants_to_learn?}] onto a member.

    Catalog names are deduped case-insensitively; only the fields present in an
    item are changed on an existing row. Returns the MemberSkill rows.
    """
    rows = []
    for item in items:
        skill = Skill.objects.filter(name__iexact=item["name"]).first()
        if not skill:
            skill = Skill.objects.create(name=item["name"], category=item.get("category", ""))
        elif item.get("category") and not skill.category:
            skill.category = item["category"]
            skill.save(update_fields=["category"])
        fields = {k: item[k] for k in ("level", "wants_to_learn") if k in item}
        row, _ = MemberSkill.objects.update_or_create(
            member=member, skill=skill,
            defaults={**fields, "source": source, "updated_by": actor},
        )
        rows.append(row)
    return rows
