from django.urls import path

from . import auth_discord, views

urlpatterns = [
    path("health/", views.health),
    # auth (open; rate-limited by middleware)
    path("auth/discord/login/", auth_discord.discord_login),
    path("auth/discord/callback/", auth_discord.discord_callback),
    path("auth/whoami/", auth_discord.whoami),
    # members & points
    path("members/", views.upsert_member),
    path("members/<str:ident>/", views.get_member),
    path("members/<str:ident>/points/", views.award_points_view),
    path("members/<str:ident>/skills/", views.skill_rating),
    path("members/<str:ident>/skill-items/", views.member_skill_items),
    path("members/<str:ident>/skill-items/<int:skill_id>/", views.member_skill_item_delete),
    path("members/<str:ident>/quiz-attempts/", views.member_quiz_attempts),
    path("skills/", views.skills_catalog),
    path("people/", views.people),
    path("directory/sync/", views.directory_sync),
    path("leaderboard/", views.leaderboard),
    # training catalog
    path("catalog/", views.catalog),
    path("lessons/<int:lesson_id>/", views.lesson_detail),
    path("lessons/<int:lesson_id>/complete/", views.complete_lesson_view),
    path("lessons/<int:lesson_id>/quiz/", views.lesson_quiz),
    path("lessons/<int:lesson_id>/quiz/attempt/", views.quiz_attempt_view),
    # inductions
    path("inductions/", views.request_induction),
    path("inductions/list/", views.list_inductions),
    path("inductions/<int:induction_id>/review/", views.review_induction),
    # action items & roles
    path("action-items/", views.action_items),
    path("action-items/<int:item_id>/", views.action_item_update),
    path("role-rules/", views.role_rules),
]
