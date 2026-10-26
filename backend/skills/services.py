"""Shared business logic used by both the API and the training-site views."""
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import LessonCompletion, PointsEntry, QuizAttempt, RoleRule


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
