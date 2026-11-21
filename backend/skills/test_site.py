"""Tests for directory sync, permissions, skill-item API and website login."""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from skills import auth_authentik, perms
from skills.directory import DirectoryError, sync_directory, sync_person_by_email
from skills.models import Member, MemberSkill, Skill, SkillRating

TOKEN = "test-token-123"  # noqa: S105


def person(i, **kw):
    return {"id": f"p{i}", "email": f"u{i}@example.test", "name": f"User {i}", "avatarUrl": "/a.png",
            "discordUserId": f"d{i}", "discordUsername": f"user{i}", "status": "active",
            "positions": ["Full Member"], **kw}


def fake_session(people, roles=None, fail_lookup=False):
    """requests.Session stand-in; roles maps email -> v1 fields."""
    roles = roles or {}
    session = MagicMock()

    def get(url, params=None, timeout=None):
        r = MagicMock()
        r.raise_for_status.return_value = None
        if url.endswith("/api/people"):
            r.json.return_value = {"people": people}
        elif fail_lookup:
            import requests
            r.raise_for_status.side_effect = requests.HTTPError("boom")
        else:
            email = params["email"]
            r.json.return_value = {"users": [{"email": email, "role": "user", "accountKind": "person",
                                              **roles.get(email, {})}]}
        return r

    session.get.side_effect = get
    return session


def run_sync(people, **kw):
    with patch("skills.directory._session", return_value=fake_session(people, **kw)):
        return sync_directory()


@override_settings(DIRECTORY_URL="https://dir.example.test", DIRECTORY_API_KEY="k")
class DirectorySyncTests(TestCase):
    def test_create_update_and_profile(self):
        c = run_sync([person(1), person(2)], roles={"u2@example.test": {"role": "admin"}})
        self.assertEqual(c, {"created": 2, "updated": 0, "archived": 0, "restored": 0, "skipped": 0})
        m = Member.objects.get(directory_id="p2")
        self.assertTrue(m.is_admin)
        self.assertEqual(m.avatar_url, "https://dir.example.test/a.png")
        self.assertTrue(SkillRating.objects.filter(member=m).exists())
        c = run_sync([person(1, name="Renamed"), person(2)])
        self.assertEqual((c["created"], c["updated"]), (0, 2))
        self.assertEqual(Member.objects.get(directory_id="p1").display_name, "Renamed")

    def test_links_existing_by_discord_then_email(self):
        by_discord = Member.objects.create(discord_id="d1")
        by_email = Member.objects.create(email="U2@Example.test")
        run_sync([person(1), person(2)])
        self.assertEqual(Member.objects.count(), 2)
        self.assertEqual(Member.objects.get(pk=by_discord.pk).directory_id, "p1")
        self.assertEqual(Member.objects.get(pk=by_email.pk).directory_id, "p2")

    def test_discord_collision_does_not_crash(self):
        Member.objects.create(discord_id="d1", directory_id="other")
        run_sync([person(1), person(5, discordUserId="d1"), person(6), person(7)])
        self.assertIsNone(Member.objects.get(directory_id="p5").discord_id)

    def test_archive_missing_former_and_restore(self):
        run_sync([person(1), person(2), person(3)])
        c = run_sync([person(1), person(2, status="former")])
        self.assertEqual(c["archived"], 2)
        self.assertIsNotNone(Member.objects.get(directory_id="p2").archived_at)
        self.assertIsNotNone(Member.objects.get(directory_id="p3").archived_at)
        c = run_sync([person(1), person(2), person(3)])
        self.assertEqual(c["restored"], 2)
        self.assertEqual(Member.objects.filter(archived_at__isnull=False).count(), 0)
        self.assertEqual(Member.objects.count(), 3)  # never deleted

    def test_role_account_skipped(self):
        c = run_sync([person(1), person(2)], roles={"u2@example.test": {"accountKind": "role"}})
        self.assertEqual((c["created"], c["skipped"]), (1, 1))
        self.assertFalse(Member.objects.filter(directory_id="p2").exists())

    def test_lookup_failure_keeps_admin_flag(self):
        run_sync([person(1)], roles={"u1@example.test": {"role": "admin"}})
        c = run_sync([person(1)], fail_lookup=True)
        self.assertEqual(c["updated"], 1)
        self.assertTrue(Member.objects.get(directory_id="p1").is_admin)

    def test_safety_guard(self):
        run_sync([person(i) for i in range(4)])
        with self.assertRaises(DirectoryError):
            run_sync([person(0)])
        self.assertEqual(Member.objects.filter(archived_at__isnull=False).count(), 0)

    def test_fetch_error_and_missing_key(self):
        session = MagicMock()
        import requests
        session.get.side_effect = requests.ConnectionError("x")
        with patch("skills.directory._session", return_value=session):
            with self.assertRaises(DirectoryError):
                sync_directory()
        with override_settings(DIRECTORY_API_KEY=""), self.assertRaises(DirectoryError):
            sync_directory()

    def test_sync_person_by_email(self):
        roles = {"u9@example.test": {"id": "p9", "name": "Nine", "positions": ["Project Lead"]}}
        with patch("skills.directory._session", return_value=fake_session([], roles=roles)):
            m = sync_person_by_email("u9@example.test")
        self.assertEqual((m.directory_id, m.positions), ("p9", ["Project Lead"]))
        self.assertTrue(SkillRating.objects.filter(member=m).exists())
        with patch("skills.directory._session", return_value=fake_session([], fail_lookup=True)):
            self.assertIsNone(sync_person_by_email("zz@example.test"))


class PermsTests(TestCase):
    def test_roles(self):
        lead = Member.objects.create(discord_id="1", positions=["Software Lead"])
        plain = Member.objects.create(discord_id="2", positions=["Full Member"])
        admin = Member.objects.create(discord_id="3", is_admin=True)
        self.assertTrue(perms.is_editor(lead) and perms.is_editor(admin))
        self.assertFalse(perms.is_editor(plain))
        self.assertTrue(perms.can_edit_skills(plain, plain))
        self.assertFalse(perms.can_edit_skills(plain, lead))
        self.assertTrue(perms.can_edit_skills(lead, plain))
        self.assertFalse(perms.can_edit_ratings(plain, plain))
        self.assertTrue(perms.can_edit_ratings(lead, plain))
        self.assertFalse(perms.can_review_inductions(lead))
        s = perms.editors_summary()
        self.assertEqual(set(s["editors"]), {lead, admin})
        self.assertEqual(s["admins"], [admin])


@override_settings(SKILL_TRACKER_TOKEN=TOKEN)
class SkillApiTests(TestCase):
    def setUp(self):
        self.lead = Member.objects.create(discord_id="1", positions=["Project Lead"])
        self.a = Member.objects.create(discord_id="2", directory_id="dir-a", email="a@example.test")
        self.b = Member.objects.create(discord_id="3")

    def h(self, caller=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
        if caller:
            h["HTTP_X_CALLER_MEMBER"] = caller.discord_id
        return h

    def put(self, ident, skills, caller=None):
        return self.client.put(f"/api/members/{ident}/skill-items/", data={"skills": skills},
                               content_type="application/json", **self.h(caller))

    def test_upsert_by_every_identifier_and_dedup(self):
        for ident in ("2", "dir-a", "a@example.test"):
            self.assertEqual(self.put(ident, [{"name": "CAD", "level": 3}]).status_code, 200)
        r = self.put("2", [{"name": "cad", "wants_to_learn": True}])
        item = r.json()["skill_items"][0]
        self.assertEqual((Skill.objects.count(), item["level"], item["wants_to_learn"], item["source"]),
                         (1, 3, True, "api"))
        self.assertEqual(self.client.get("/api/skills/", **self.h()).json()["skills"][0]["name"], "CAD")
        prof = self.client.get("/api/members/dir-a/", **self.h()).json()
        self.assertEqual(prof["skill_items"][0]["name"], "CAD")
        self.assertEqual((prof["directory_id"], prof["archived"]), ("dir-a", False))

    def test_validation(self):
        for bad in ([], [{"name": ""}], [{"name": "x", "level": 6}], [{"name": "x", "level": "a"}],
                    [{"name": "x", "wants_to_learn": "yes"}], ["x"], [{"name": 5}]):
            self.assertEqual(self.put("2", bad).status_code, 400, bad)
        self.assertEqual(self.put("nobody", [{"name": "x"}]).status_code, 404)

    def test_permissions_and_delete(self):
        self.assertEqual(self.put("2", [{"name": "ROS"}], caller=self.a).status_code, 200)  # self
        self.assertEqual(self.put("2", [{"name": "ROS"}], caller=self.b).status_code, 403)
        self.assertEqual(self.put("2", [{"name": "ROS"}], caller=self.lead).status_code, 200)
        sid = Skill.objects.get(name="ROS").id
        self.assertEqual(self.client.delete(f"/api/members/2/skill-items/{sid}/", **self.h(self.b)).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/members/2/skill-items/{sid}/", **self.h(self.a)).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/members/2/skill-items/{sid}/", **self.h(self.a)).status_code, 404)

    def test_ratings_editors_only(self):
        url = "/api/members/3/skills/"
        put = lambda c: self.client.put(url, data={"software": 7}, content_type="application/json", **self.h(c))
        self.assertEqual(put(self.a).status_code, 403)
        self.assertEqual(put(self.lead).status_code, 200)

    def test_archived_reads_ok_writes_409(self):
        Member.objects.filter(pk=self.a.pk).update(archived_at="2026-01-01T00:00:00Z")
        self.assertEqual(self.client.get("/api/members/2/", **self.h()).json()["archived"], True)
        self.assertEqual(self.put("2", [{"name": "x"}]).status_code, 409)
        r = self.client.post("/api/members/2/points/", data={"points": 5}, content_type="application/json", **self.h())
        self.assertEqual(r.status_code, 409)

    def test_people_list(self):
        Member.objects.filter(pk=self.a.pk).update(archived_at="2026-01-01T00:00:00Z")
        ids = [p["discord_id"] for p in self.client.get("/api/people/", **self.h()).json()["people"]]
        self.assertEqual(sorted(ids), ["1", "3"])
        r = self.client.get("/api/people/?archived=1", **self.h())
        self.assertEqual(len(r.json()["people"]), 3)
        self.assertEqual(self.client.get("/api/people/?archived=1", **self.h(self.b)).status_code, 403)

    def test_directory_sync_endpoint(self):
        with patch("skills.views.sync_directory", return_value={"created": 1}):
            r = self.client.post("/api/directory/sync/", **self.h())
            self.assertEqual(r.json(), {"created": 1})
            self.assertEqual(self.client.post("/api/directory/sync/", **self.h(self.b)).status_code, 403)
        self.assertEqual(self.client.post("/api/directory/sync/").status_code, 401)


@override_settings(AUTHENTIK_ISSUER="https://idp.example.test/o/skills/", AUTHENTIK_CLIENT_ID="cid",
                   AUTHENTIK_CLIENT_SECRET="sec", SITE_URL="https://skills.example.test", DEBUG=False)
class LoginTests(TestCase):
    def setUp(self):
        auth_authentik._oidc_config.clear()
        auth_authentik._oidc_config.update(
            authorization_endpoint="https://idp.example.test/authorize",
            token_endpoint="https://idp.example.test/token",
            userinfo_endpoint="https://idp.example.test/userinfo")
        self.m = Member.objects.create(discord_id="1", email="Me@Example.test")

    def start(self):
        r = self.client.get("/auth/login/?next=/people/")
        self.assertIn("redirect_uri=https%3A%2F%2Fskills.example.test%2Fauth%2Fcallback%2F", r["Location"])
        return self.client.session["oauth_state"]

    def callback(self, email, state=None):
        state = state or self.start()
        with patch("skills.auth_authentik.requests") as rq:
            rq.post.return_value.json.return_value = {"access_token": "t"}
            rq.get.return_value.json.return_value = {"email": email}
            rq.RequestException = Exception
            return self.client.get(f"/auth/callback/?code=c&state={state}")

    def test_login_flow(self):
        r = self.callback("me@example.test")
        self.assertEqual((r.status_code, r["Location"]), (302, "/people/"))
        self.assertEqual(self.client.session["member_id"], self.m.pk)

    def test_bad_state(self):
        self.start()
        self.assertEqual(self.callback("me@example.test", state="wrong").status_code, 400)
        self.assertNotIn("member_id", self.client.session)

    def test_unknown_and_archived_get_403(self):
        with patch("skills.auth_authentik.sync_person_by_email", return_value=None):
            self.assertEqual(self.callback("nobody@example.test").status_code, 403)
        Member.objects.filter(pk=self.m.pk).update(archived_at="2026-01-01T00:00:00Z")
        with patch("skills.auth_authentik.sync_person_by_email", return_value=None):
            self.assertEqual(self.callback("me@example.test").status_code, 403)

    def test_next_is_same_site_only(self):
        self.assertEqual(auth_authentik._safe_next("//evil.test"), "/")
        self.assertEqual(auth_authentik._safe_next("https://evil.test"), "/")
        self.assertEqual(auth_authentik._safe_next("/ok"), "/ok")

    def test_dev_login_only_in_debug(self):
        with override_settings(DEV_LOGIN_EMAIL="me@example.test", DEBUG=False):
            self.assertIn("authorize", self.client.get("/auth/login/")["Location"])
        with override_settings(DEV_LOGIN_EMAIL="me@example.test", DEBUG=True):
            r = self.client.get("/auth/login/")
            self.assertEqual(r["Location"], "/")
            self.assertEqual(self.client.session["member_id"], self.m.pk)

    def test_logout_and_request_member(self):
        s = self.client.session
        s["member_id"] = self.m.pk
        s.save()
        self.client.get("/auth/logout/")
        self.assertNotIn("member_id", self.client.session)

    def test_csrf_enforced_on_site_not_api(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.post("/auth/logout/").status_code, 403)
        self.assertEqual(c.post("/api/members/", data={}, content_type="application/json").status_code, 401)
