"""Skill-tracker domain models.

Extracted from discordbot (discordapp.models) and extended for the training
site scope agreed in the 2026-10-25 kickoff:

- Member / MemberRoster: identity, Discord-linked or Notion-only.
- SkillRating: admin-set 0-10 ratings across 6 fixed axes (hexagon graph).
- PointsEntry: points ledger (bounty, engagement, lesson, action-item).
- InductionType / Induction: physical induction gate (admin-approved) that
  unlocks virtual lessons.
- EquipmentGroup / Lesson / Quiz / LessonCompletion: scalable training
  content, KUKA first, then CNC / Prusa / Doosan.
- ActionItem: Notion action item mirrored; completion awards points.
- RoleRule: points threshold -> Discord role upgrade, applied by the bot.
"""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Member(models.Model):
    """A member, keyed by Discord snowflake when linked (discordbot DiscordProfile)."""

    discord_id = models.CharField(max_length=32, unique=True, blank=True, null=True)
    discord_username = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    display_name = models.CharField(max_length=200, blank=True)
    avatar_url = models.URLField(blank=True)
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.display_name or self.discord_username or str(self.discord_id)


class MemberRoster(models.Model):
    """Notion 'Members' database mirror; resolves to a Member when matched."""

    notion_page_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200, blank=True)
    discord_username_hint = models.CharField(max_length=100, blank=True, db_index=True)
    monash_email = models.EmailField(blank=True, db_index=True)
    positions = models.JSONField(default=list, blank=True)
    member = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="roster_entries"
    )
    last_synced_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name or self.notion_page_id


class SkillRating(models.Model):
    """Admin-set 0-10 ratings across 6 fixed axes (hexagonal skill graph)."""

    _score = {"default": 0, "validators": [MinValueValidator(0), MaxValueValidator(10)]}

    member = models.OneToOneField(Member, on_delete=models.CASCADE, related_name="skill_rating")
    leadership = models.PositiveSmallIntegerField(**_score)
    engineering = models.PositiveSmallIntegerField(**_score)
    software = models.PositiveSmallIntegerField(**_score)
    electronics = models.PositiveSmallIntegerField(**_score)
    project_mgmt = models.PositiveSmallIntegerField(**_score)
    communication = models.PositiveSmallIntegerField(**_score)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    AXES = ("leadership", "engineering", "software", "electronics", "project_mgmt", "communication")

    def as_dict(self) -> dict:
        return {axis: getattr(self, axis) for axis in self.AXES}

    def __str__(self) -> str:
        return f"{self.member} skills"


class PointsEntry(models.Model):
    """One ledger entry contributing to a member's points total."""

    SOURCE_BOUNTY = "bounty"
    SOURCE_ENGAGEMENT = "engagement"
    SOURCE_LESSON = "lesson"
    SOURCE_ACTION_ITEM = "action_item"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_BOUNTY, "Bounty Board"),
        (SOURCE_ENGAGEMENT, "Engagement Hours"),
        (SOURCE_LESSON, "Training Lesson"),
        (SOURCE_ACTION_ITEM, "Notion Action Item"),
        (SOURCE_MANUAL, "Manual Adjustment"),
    ]

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="points_entries")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, db_index=True)
    points = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.CharField(max_length=300, blank=True)
    earned_on = models.DateField(db_index=True)
    external_ref = models.CharField(
        max_length=128, blank=True, null=True, unique=True,
        help_text="Dedup key for synced rows (Notion page id etc); blank for manual entries",
    )
    logged_by = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="logged_points"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.member} +{self.points} ({self.source})"

    class Meta:
        ordering = ["-earned_on"]


class InductionType(models.Model):
    """Catalog of physical inductions (mirrors Notion 'Inductions', e.g. D&B, Makerspace)."""

    notion_page_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        ordering = ["name"]


class Induction(models.Model):
    """One member's completion record for one induction type (the physical gate)."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="inductions")
    induction_type = models.ForeignKey(InductionType, on_delete=models.CASCADE, related_name="completions")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.member} — {self.induction_type.name} ({self.status})"

    class Meta:
        unique_together = ("member", "induction_type")
        ordering = ["-submitted_at"]


class EquipmentGroup(models.Model):
    """Scalable equipment grouping for training content: KUKA first, then CNC, Prusa, Doosan."""

    key = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        ordering = ["order", "name"]


class Lesson(models.Model):
    """A training lesson under an equipment group; admin-editable content."""

    group = models.ForeignKey(EquipmentGroup, on_delete=models.CASCADE, related_name="lessons")
    slug = models.SlugField(max_length=300, unique=True, blank=True)
    title = models.CharField(max_length=300)
    summary = models.CharField(max_length=500, blank=True)
    body_markdown = models.TextField(blank=True)
    points = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    requires_induction = models.ForeignKey(
        InductionType, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Member needs an approved Induction of this type before starting the lesson",
    )
    order = models.PositiveSmallIntegerField(default=0)
    published = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self) -> str:
        return f"[{self.group.key}] {self.title}"

    class Meta:
        ordering = ["group__order", "group__name", "order", "title"]


class Quiz(models.Model):
    """End-of-lesson quiz. Questions stored as JSON: [{question, options, answer_index}]."""

    lesson = models.OneToOneField(Lesson, on_delete=models.CASCADE, related_name="quiz")
    questions = models.JSONField(default=list)
    pass_mark = models.PositiveSmallIntegerField(default=60, help_text="Percent needed to pass")

    def __str__(self) -> str:
        return f"Quiz — {self.lesson}"


class LessonCompletion(models.Model):
    """A member finished a lesson (and passed its quiz, if any)."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="lesson_completions")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="completions")
    completed_at = models.DateTimeField(auto_now_add=True)
    quiz_score = models.PositiveSmallIntegerField(null=True, blank=True)
    points_awarded = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        unique_together = ("member", "lesson")
        ordering = ["-completed_at"]


class QuizAttempt(models.Model):
    """One graded attempt at a lesson quiz (server-side grading)."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="quiz_attempts")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="quiz_attempts")
    score = models.PositiveSmallIntegerField(help_text="Percent correct")
    passed = models.BooleanField(default=False)
    answers = models.JSONField(default=list, help_text="Answer index per question")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.member} — {self.lesson} ({self.score}%)"


class ActionItem(models.Model):
    """Notion action item mirrored; completion awards the member points.

    Locally-created items (via the API) have no notion_page_id until synced.
    """

    notion_page_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    member = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="action_items"
    )
    title = models.CharField(max_length=300)
    project = models.CharField(max_length=200, blank=True)
    due_date = models.DateField(null=True, blank=True)
    done = models.BooleanField(default=False)
    points = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title


class RoleRule(models.Model):
    """Points threshold -> Discord role upgrade, applied by the Discord bot."""

    threshold_points = models.DecimalField(max_digits=8, decimal_places=2)
    discord_role_id = models.CharField(max_length=32)
    discord_role_name = models.CharField(max_length=100, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.threshold_points}pts -> {self.discord_role_name or self.discord_role_id}"

    class Meta:
        ordering = ["threshold_points"]
