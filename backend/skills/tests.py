"""End-to-end tests for the skill tracker API.

Run: python manage.py test skills
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from skills.middleware import issue_session_token
from skills.models import (
    ActionItem,
    EquipmentGroup,
    Induction,
    InductionType,
    Lesson,
    Member,
    PointsEntry,
    Quiz,
    QuizAttempt,
    RoleRule,
    SkillRating,
)
from skills.services import grade_quiz, points_total, role_for_member

TEST_TOKEN = "test-token-123"  # noqa: S105


@override_settings(SKILL_TRACKER_TOKEN=TEST_TOKEN, DISCORD_ADMIN_IDS="999")
class ApiTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(discord_id="111", display_name="Test")
        self.admin = Member.objects.create(discord_id="999", display_name="Admin", is_admin=True)
        self.group = EquipmentGroup.objects.create(key="kuka", name="KUKA Arm")
        self.induction_type = InductionType.objects.create(name="KUKA Physical Induction")
        self.lesson = Lesson.objects.create(
            group=self.group, slug="kuka-safety", title="KUKA Safety Basics",
            points=Decimal("10"), requires_induction=self.induction_type,
            published=True, body_markdown="# safety",
        )
        self.quiz = Quiz.objects.create(
            lesson=self.lesson, pass_mark=60,
            questions=[
                {"question": "Q1?", "options": ["a", "b"], "answer_index": 0},
                {"question": "Q2?", "options": ["a", "b"], "answer_index": 1},
            ],
        )

    def auth(self, member=None):
        """Service-token headers acting on behalf of a member."""
        headers = {"HTTP_AUTHORIZATION": f"Bearer {TEST_TOKEN}"}
        if member:
            headers["HTTP_X_CALLER_MEMBER"] = member.discord_id
        return headers

    # ---- auth ----

    def test_health_open(self):
        r = self.client.get("/api/health/")
        self.assertEqual(r.status_code, 200)

    def test_no_token_401(self):
        r = self.client.get("/api/leaderboard/")
        self.assertEqual(r.status_code, 401)

    def test_session_token_auth(self):
        token = issue_session_token(self.member)
        r = self.client.get("/api/members/111/", HTTP_X_AUTH_TOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["discord_id"], "111")

    def test_bad_session_token_401(self):
        r = self.client.get("/api/members/111/", HTTP_X_AUTH_TOKEN="garbage")
        self.assertEqual(r.status_code, 401)

    # ---- members & points ----

    def test_upsert_member(self):
        r = self.client.post("/api/members/", data={"discord_id": "222", "display_name": "B"},
                             content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 201)
        r2 = self.client.post("/api/members/", data={"discord_id": "222", "display_name": "B2"},
                              content_type="application/json", **self.auth())
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(Member.objects.filter(discord_id="222").count(), 1)

    def test_malformed_json_400(self):
        r = self.client.post("/api/members/", data="{not json", content_type="application/json",
                             **self.auth())
        self.assertEqual(r.status_code, 400)

    def test_non_object_json_400(self):
        r = self.client.post("/api/members/", data="[1,2]", content_type="application/json",
                             **self.auth())
        self.assertEqual(r.status_code, 400)

    def test_award_points_and_dedup(self):
        r = self.client.post("/api/members/111/points/",
                             data={"points": 50, "source": "bounty", "external_ref": "b:1"},
                             content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["points_total"], "50.00")
        r2 = self.client.post("/api/members/111/points/",
                              data={"points": 50, "source": "bounty", "external_ref": "b:1"},
                              content_type="application/json", **self.auth())
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["deduplicated"])
        self.assertEqual(PointsEntry.objects.count(), 1)

    def test_negative_points_400(self):
        r = self.client.post("/api/members/111/points/", data={"points": -5},
                             content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 400)

    def test_huge_points_400(self):
        r = self.client.post("/api/members/111/points/", data={"points": 99999999},
                             content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 400)

    def test_leaderboard(self):
        PointsEntry.objects.create(member=self.member, source="manual", points=Decimal("5"),
                                   earned_on="2026-10-01")
        r = self.client.get("/api/leaderboard/", **self.auth())
        leaders = r.json()["leaders"]
        self.assertEqual(len(leaders), 1)
        self.assertEqual(leaders[0]["discord_id"], "111")

    # ---- skill ratings ----

    def test_skill_rating_put_requires_admin(self):
        r = self.client.put("/api/members/111/skills/", data={"software": 8},
                            content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 403)
        r2 = self.client.put("/api/members/111/skills/", data={"software": 8},
                             content_type="application/json", **self.auth(self.admin))
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["software"], 8)

    def test_skill_rating_out_of_range(self):
        r = self.client.put("/api/members/111/skills/", data={"software": 11},
                            content_type="application/json", **self.auth(self.admin))
        self.assertEqual(r.status_code, 400)

    # ---- training flow ----

    def test_catalog_public(self):
        r = self.client.get("/api/catalog/", **self.auth())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["equipment"][0]["lessons"][0]["slug"], "kuka-safety")

    def test_quiz_hides_answers(self):
        r = self.client.get(f"/api/lessons/{self.lesson.id}/quiz/", **self.auth())
        q = r.json()["questions"][0]
        self.assertNotIn("answer_index", q)

    def test_lesson_blocked_without_induction(self):
        r = self.client.post(f"/api/lessons/{self.lesson.id}/complete/",
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 403)

    def test_lesson_blocked_without_quiz_pass(self):
        Induction.objects.create(member=self.member, induction_type=self.induction_type,
                                 status=Induction.STATUS_APPROVED)
        r = self.client.post(f"/api/lessons/{self.lesson.id}/complete/",
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 403)

    def test_full_lesson_flow(self):
        Induction.objects.create(member=self.member, induction_type=self.induction_type,
                                 status=Induction.STATUS_APPROVED)
        # fail quiz first
        r = self.client.post(f"/api/lessons/{self.lesson.id}/quiz/attempt/",
                             data={"answers": [1, 1]}, content_type="application/json",
                             **self.auth(self.member))
        self.assertEqual(r.json()["passed"], False)
        # pass quiz
        r = self.client.post(f"/api/lessons/{self.lesson.id}/quiz/attempt/",
                             data={"answers": [0, 1]}, content_type="application/json",
                             **self.auth(self.member))
        self.assertEqual(r.json()["passed"], True)
        # complete
        r = self.client.post(f"/api/lessons/{self.lesson.id}/complete/",
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["points_total"], "10.00")
        # duplicate -> 409
        r = self.client.post(f"/api/lessons/{self.lesson.id}/complete/",
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(PointsEntry.objects.filter(source="lesson").count(), 1)

    def test_quiz_attempt_bad_length(self):
        r = self.client.post(f"/api/lessons/{self.lesson.id}/quiz/attempt/",
                             data={"answers": [0]}, content_type="application/json",
                             **self.auth(self.member))
        self.assertEqual(r.status_code, 400)

    # ---- inductions ----

    def test_induction_request_and_admin_review(self):
        r = self.client.post("/api/inductions/",
                             data={"induction_type_id": self.induction_type.id},
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 201)
        induction_id = r.json()["induction_id"]
        # member cannot self-approve
        r = self.client.patch(f"/api/inductions/{induction_id}/review/",
                              data={"decision": "approved"}, content_type="application/json",
                              **self.auth(self.member))
        self.assertEqual(r.status_code, 403)
        # admin approves
        r = self.client.patch(f"/api/inductions/{induction_id}/review/",
                              data={"decision": "approved"}, content_type="application/json",
                              **self.auth(self.admin))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Induction.objects.get(pk=induction_id).status, "approved")

    def test_induction_list_scoping(self):
        Induction.objects.create(member=self.member, induction_type=self.induction_type)
        r = self.client.get("/api/inductions/list/", **self.auth(self.member))
        self.assertEqual(len(r.json()["inductions"]), 1)
        r = self.client.get("/api/inductions/list/", **self.auth(self.admin))
        self.assertEqual(len(r.json()["inductions"]), 1)

    # ---- action items ----

    def test_action_item_complete_awards_points(self):
        item = ActionItem.objects.create(title="Fix gripper", member=self.member,
                                         points=Decimal("20"))
        r = self.client.patch(f"/api/action-items/{item.id}/", data={"done": True},
                              content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(points_total(self.member), Decimal("20"))
        # idempotent
        self.client.patch(f"/api/action-items/{item.id}/", data={"done": True},
                          content_type="application/json", **self.auth(self.member))
        self.assertEqual(points_total(self.member), Decimal("20"))

    def test_action_item_other_member_forbidden(self):
        item = ActionItem.objects.create(title="x", member=self.member)
        r = self.client.patch(f"/api/action-items/{item.id}/", data={"done": True},
                              content_type="application/json", **self.auth(self.admin))
        # admin allowed
        self.assertEqual(r.status_code, 200)
        item2 = ActionItem.objects.create(title="y", member=self.member)
        other = Member.objects.create(discord_id="333")
        r = self.client.patch(f"/api/action-items/{item2.id}/", data={"done": True},
                              content_type="application/json", **self.auth(other))
        self.assertEqual(r.status_code, 403)

    def test_action_item_assignee_and_points(self):
        r = self.client.post("/api/action-items/",
                             data={"title": "Assigned", "member_discord_id": "111",
                                   "points": 15},
                             content_type="application/json", **self.auth(self.admin))
        self.assertEqual(r.status_code, 201)
        item = ActionItem.objects.get(pk=r.json()["id"])
        self.assertEqual(item.member_id, self.member.id)
        self.assertEqual(item.points, Decimal("15"))
        # non-admin cannot assign to someone else
        r = self.client.post("/api/action-items/",
                             data={"title": "Sneaky", "member_discord_id": "999"},
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 403)

    # ---- role rules ----

    def test_role_rule_upsert_admin_only(self):
        r = self.client.post("/api/role-rules/",
                             data={"discord_role_id": "r1", "threshold_points": 100,
                                   "discord_role_name": "Contributor"},
                             content_type="application/json", **self.auth(self.member))
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/api/role-rules/",
                             data={"discord_role_id": "r1", "threshold_points": 100,
                                   "discord_role_name": "Contributor"},
                             content_type="application/json", **self.auth(self.admin))
        self.assertEqual(r.status_code, 201)

    # ---- discord oauth ----

    @override_settings(DISCORD_CLIENT_ID="cid", DISCORD_CLIENT_SECRET="secret")  # noqa: S105
    def test_oauth_callback_creates_member(self):
        import json as _json
        from unittest.mock import patch
        fake_user = {"id": "555", "username": "newbie", "global_name": "Newbie", "avatar": None}
        with patch("skills.auth_discord.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.side_effect = [
                b'{"access_token": "at"}',
                _json.dumps(fake_user).encode(),
            ]
            from django.core import signing
            from skills.auth_discord import STATE_SALT
            state = "abc123"
            signed = signing.TimestampSigner(salt=STATE_SALT).sign(state)
            self.client.cookies["oauth_state"] = signed
            r = self.client.get(f"/api/auth/discord/callback/?code=c&state={state}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Member.objects.filter(discord_id="555").count(), 1)
        self.assertIn("token", r.json())

    def test_oauth_login_not_configured(self):
        r = self.client.get("/api/auth/discord/login/")
        self.assertEqual(r.status_code, 503)


class ServiceTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(discord_id="111")
        self.lesson = Lesson.objects.create(
            group=EquipmentGroup.objects.create(key="k"), title="L", points=Decimal("5"),
        )
        self.quiz = Quiz.objects.create(
            lesson=self.lesson, pass_mark=50,
            questions=[{"question": "q", "options": ["a", "b"], "answer_index": 1}] * 2,
        )

    def test_grade_quiz(self):
        score, passed = grade_quiz(self.quiz, [1, 0])
        self.assertEqual(score, 50)
        self.assertTrue(passed)

    def test_role_for_member(self):
        RoleRule.objects.create(discord_role_id="r1", threshold_points=Decimal("10"),
                                discord_role_name="One")
        RoleRule.objects.create(discord_role_id="r2", threshold_points=Decimal("100"),
                                discord_role_name="Two")
        PointsEntry.objects.create(member=self.member, source="manual", points=Decimal("50"),
                                   earned_on="2026-10-01")
        rule = role_for_member(self.member)
        self.assertEqual(rule.discord_role_name, "One")
