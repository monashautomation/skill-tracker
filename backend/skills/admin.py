from django.contrib import admin

from .models import (
    ActionItem,
    EquipmentGroup,
    Induction,
    InductionType,
    Lesson,
    LessonCompletion,
    Member,
    PointsEntry,
    Quiz,
    QuizAttempt,
    RoleRule,
    SkillRating,
)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("discord_id", "display_name", "discord_username", "is_admin", "last_login_at")
    search_fields = ("discord_id", "discord_username", "display_name", "email")
    list_filter = ("is_admin",)


@admin.register(SkillRating)
class SkillRatingAdmin(admin.ModelAdmin):
    list_display = ("member", "leadership", "engineering", "software",
                    "electronics", "project_mgmt", "communication")


@admin.register(PointsEntry)
class PointsEntryAdmin(admin.ModelAdmin):
    list_display = ("member", "points", "source", "earned_on", "description")
    list_filter = ("source",)
    search_fields = ("member__discord_id", "description")


@admin.register(EquipmentGroup)
class EquipmentGroupAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "order", "active")


@admin.register(InductionType)
class InductionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "active")


@admin.register(Induction)
class InductionAdmin(admin.ModelAdmin):
    list_display = ("member", "induction_type", "status", "submitted_at", "reviewed_at")
    list_filter = ("status",)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("title", "group", "points", "requires_induction", "published")
    list_filter = ("group", "published", "requires_induction")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("lesson", "pass_mark")


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("member", "lesson", "score", "passed", "created_at")
    list_filter = ("passed",)


@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    list_display = ("title", "done", "member", "due_date", "points")
    list_filter = ("done",)


@admin.register(RoleRule)
class RoleRuleAdmin(admin.ModelAdmin):
    list_display = ("discord_role_name", "discord_role_id", "threshold_points", "active")


@admin.register(LessonCompletion)
class LessonCompletionAdmin(admin.ModelAdmin):
    list_display = ("member", "lesson", "completed_at", "points_awarded")
