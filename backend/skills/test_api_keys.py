"""API keys: auth, scopes, hash-only storage, /api/v1/ alias, people filters, admin page."""
import hashlib
import re

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from skills.models import ApiKey, Member, MemberSkill, Skill, SkillRating

PLAIN_STATIC = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
TOKEN = "shared-token"  # noqa: S105


def make(name, **kw):
    m = Member.objects.create(display_name=name, email=f"{name.lower().replace(' ', '')}@example.test", **kw)
    SkillRating.objects.create(member=m)
    return m


def make_key(scope="read", raw="stk_" + "a" * 48, **kw):
    return ApiKey.objects.create(name="k", scope=scope, key_prefix=raw[:12],
                                 key_hash=hashlib.sha256(raw.encode()).hexdigest(), **kw), raw


@override_settings(SKILL_TRACKER_TOKEN=TOKEN)
class KeyAuthTests(TestCase):
    def test_x_api_key_and_bearer_authenticate(self):
        _, raw = make_key()
        self.assertEqual(self.client.get("/api/people/", HTTP_X_API_KEY=raw).status_code, 200)
        self.assertEqual(self.client.get("/api/people/", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 200)

    def test_unknown_missing_and_revoked_are_401(self):
        key, raw = make_key()
        self.assertEqual(self.client.get("/api/people/", HTTP_X_API_KEY="stk_nope").status_code, 401)
        self.assertEqual(self.client.get("/api/people/").status_code, 401)
        key.revoked_at = timezone.now()
        key.save()
        self.assertEqual(self.client.get("/api/people/", HTTP_X_API_KEY=raw).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/people/", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 401)

    def test_read_key_is_get_only(self):
        _, raw = make_key("read")
        for method in ("post", "put", "patch", "delete"):
            r = getattr(self.client, method)("/api/members/", content_type="application/json", HTTP_X_API_KEY=raw)
            self.assertEqual(r.status_code, 403, method)
            self.assertEqual(r.json(), {"error": "this key is read-only"})

    def test_write_key_can_write(self):
        _, raw = make_key("write")
        r = self.client.post("/api/members/", {"discord_id": "9", "display_name": "N"},
                             content_type="application/json", HTTP_X_API_KEY=raw)
        self.assertIn(r.status_code, (200, 201))
        self.assertTrue(Member.objects.filter(discord_id="9").exists())

    def test_shared_token_still_works(self):
        self.assertEqual(self.client.get("/api/people/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}").status_code, 200)
        r = self.client.post("/api/members/", {"discord_id": "8"}, content_type="application/json",
                             HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertIn(r.status_code, (200, 201))

    def test_caller_header_honoured_and_open_paths(self):
        m = make("Cal", discord_id="55")
        _, raw = make_key("write")
        r = self.client.get("/api/inductions/list/", HTTP_X_API_KEY=raw, HTTP_X_CALLER_MEMBER=str(m.discord_id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/health/").status_code, 200)

    def test_last_used_set(self):
        key, raw = make_key()
        self.assertIsNone(key.last_used_at)
        self.client.get("/api/v1/people/", HTTP_X_API_KEY=raw)
        key.refresh_from_db()
        first = key.last_used_at
        self.assertIsNotNone(first)
        self.client.get("/api/v1/people/", HTTP_X_API_KEY=raw)  # within a minute: no rewrite
        key.refresh_from_db()
        self.assertEqual(key.last_used_at, first)

    def test_raw_key_not_stored(self):
        _, raw = make_key()
        with connection.cursor() as c:
            c.execute("SELECT * FROM skills_apikey")
            row = " ".join(str(v) for v in c.fetchone())
        self.assertNotIn(raw, row)
        self.assertNotIn(raw[4:], row)


class PeopleLookupTests(TestCase):
    def setUp(self):
        _, self.raw = make_key()
        self.ada = make("Ada Lovelace", discord_id="d1", directory_id="p1", positions=["Software Lead"])
        self.bob = make("Bob Builder", discord_id="d2", directory_id="p2", status="alumni")
        self.gone = make("Gone Ada", archived_at=timezone.now())
        cad = Skill.objects.create(name="CAD Modelling")
        ros = Skill.objects.create(name="ROS")
        MemberSkill.objects.create(member=self.ada, skill=cad, level=3)
        MemberSkill.objects.create(member=self.bob, skill=cad, level=0, wants_to_learn=True)
        MemberSkill.objects.create(member=self.bob, skill=ros, level=2)

    def get(self, qs="", prefix="/api/v1"):
        r = self.client.get(f"{prefix}/people/{qs}", HTTP_X_API_KEY=self.raw)
        return r, [p.get("name") for p in r.json().get("people", [])]

    def test_unfiltered_and_v1_alias(self):
        for prefix in ("/api", "/api/v1"):
            r, names = self.get(prefix=prefix)
            self.assertEqual(sorted(names), ["Ada Lovelace", "Bob Builder"])

    def test_filters(self):
        cases = {
            "?name=ADA": ["Ada Lovelace"],
            "?email=ADALOVELACE@example.test": ["Ada Lovelace"],
            "?email=ada": [],
            "?discordId=d2": ["Bob Builder"],
            "?directoryId=p1": ["Ada Lovelace"],
            "?position=lead": ["Ada Lovelace"],
            "?skill=cad": ["Ada Lovelace"],
            "?skill=ros": ["Bob Builder"],
            "?wantsToLearn=cad": ["Bob Builder"],
            "?status=ALUMNI": ["Bob Builder"],
            "?name=b&skill=ros": ["Bob Builder"],
        }
        for qs, expected in cases.items():
            r, names = self.get(qs)
            self.assertEqual((r.status_code, names), (200, expected), qs)

    def test_fields(self):
        r, _ = self.get("?fields=id,name&name=ada")
        self.assertEqual(r.json()["people"], [{"id": "p1", "name": "Ada Lovelace"}])
        r, _ = self.get("?fields=id,password")
        self.assertEqual(r.status_code, 400)
        self.assertIn("skill_items", r.json()["error"])
        self.assertEqual(self.get("?fields=")[0].status_code, 400)
        self.assertEqual(self.get("?name=" + "x" * 500)[0].status_code, 400)


@override_settings(STORAGES=PLAIN_STATIC)
class KeyPageTests(TestCase):
    def setUp(self):
        self.admin = make("Ada Admin", is_admin=True)
        self.user = make("Mem Ber")

    def login(self, m):
        s = self.client.session
        s["member_id"] = m.pk
        s.save()

    def test_logged_out_redirects_and_non_admin_forbidden(self):
        r = self.client.get("/api-keys/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/login/"))
        self.login(self.user)
        self.assertEqual(self.client.get("/api-keys/").status_code, 403)
        self.assertEqual(self.client.post("/api-keys/create/", {"name": "x", "scope": "read"}).status_code, 403)
        self.assertEqual(ApiKey.objects.count(), 0)
        key, _ = make_key()
        self.assertEqual(self.client.post(f"/api-keys/{key.pk}/revoke/").status_code, 403)
        key.refresh_from_db()
        self.assertIsNone(key.revoked_at)
        self.assertNotContains(self.client.get("/"), 'href="/api-keys/"')

    def test_create_shows_key_once_then_revoke(self):
        self.login(self.admin)
        self.assertContains(self.client.get("/"), 'href="/api-keys/"')
        r = self.client.post("/api-keys/create/", {"name": "Ticketing", "scope": "write"})
        self.assertRedirects(r, "/api-keys/", fetch_redirect_response=False)
        key = ApiKey.objects.get()
        self.assertEqual((key.scope, key.created_by), ("write", self.admin))
        page = self.client.get("/api-keys/").content.decode()
        raw = re.search(r"stk_[0-9a-f]{48}", page).group(0)
        self.assertEqual(key.key_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(page.count(raw), 1)
        self.assertIn("Copy this key now", page)
        again = self.client.get("/api-keys/").content.decode()
        self.assertNotIn(raw, again)
        self.assertIn(key.key_prefix, again)
        self.assertEqual(self.client.get("/api/people/", HTTP_X_API_KEY=raw).status_code, 200)
        self.client.post(f"/api-keys/{key.pk}/revoke/")
        key.refresh_from_db()
        self.assertIsNotNone(key.revoked_at)
        self.assertEqual(self.client.get("/api/people/", HTTP_X_API_KEY=raw).status_code, 401)

    def test_create_validation(self):
        self.login(self.admin)
        self.client.post("/api-keys/create/", {"name": "", "scope": "read"})
        self.client.post("/api-keys/create/", {"name": "x", "scope": "root"})
        self.assertEqual(ApiKey.objects.count(), 0)
