"""Website page tests: login redirect, archived visibility, POST permission checks, happy paths."""
import doctest
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from skills import site_views
from skills.models import Induction, InductionType, Member, MemberSkill, Skill, SkillRating


def make(name, **kw):
    m = Member.objects.create(display_name=name, email=f"{name.lower().replace(' ', '')}@example.test", **kw)
    SkillRating.objects.create(member=m)
    return m


PLAIN_STATIC = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


@override_settings(SKILLS_EDITOR_POSITIONS=["Lead"], STORAGES=PLAIN_STATIC)
class PageTests(TestCase):
    def setUp(self):
        self.admin = make("Ada Admin", is_admin=True)
        self.lead = make("Lee Lead", positions=["Project Lead"])
        self.alice = make("Alice Member", positions=["Full Member"])
        self.bob = make("Bob Member", positions=["Full Member"])
        self.gone = make("Gone Person", archived_at=timezone.now())

    def login(self, member):
        s = self.client.session
        s["member_id"] = member.pk
        s.save()

    def test_radar_points_doctests(self):
        self.assertEqual(doctest.testmod(site_views).failed, 0)

    def test_logged_out_redirects_to_login(self):
        for url in ["/", f"/people/{self.alice.pk}/", "/skills/", "/inductions/"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r["Location"].startswith("/login/"))
        self.assertContains(self.client.get("/login/"), "Sign in with Monash Automation")

    def test_roster_hides_archived_except_admin_toggle(self):
        self.login(self.alice)
        self.assertNotContains(self.client.get("/"), "Gone Person")
        self.assertNotContains(self.client.get("/?archived=1"), "Gone Person")  # non-admin: toggle ignored
        self.assertEqual(self.client.get(f"/people/{self.gone.pk}/").status_code, 404)
        self.login(self.admin)
        self.assertNotContains(self.client.get("/"), "Gone Person")
        self.assertContains(self.client.get("/?archived=1"), "card--archived")

    def test_roster_skill_filter_modes(self):
        sk = Skill.objects.create(name="CAD", category="Mechanical")
        MemberSkill.objects.create(member=self.alice, skill=sk, level=3)
        MemberSkill.objects.create(member=self.bob, skill=sk, level=0, wants_to_learn=True)
        self.login(self.alice)
        card = lambda m: f'class="card" href="/people/{m.pk}/"'  # noqa: E731
        can = self.client.get("/?skill=CAD&mode=can").content.decode()
        self.assertIn(card(self.alice), can)
        self.assertNotIn(card(self.bob), can)
        learn = self.client.get("/?skill=CAD&mode=learn").content.decode()
        self.assertIn(card(self.bob), learn)
        self.assertNotIn(card(self.alice), learn)

    def test_member_cannot_edit_others_skills_or_ratings(self):
        self.login(self.alice)
        r = self.client.post(f"/people/{self.bob.pk}/skills/", {"name": "CAD", "level": "3"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(MemberSkill.objects.filter(member=self.bob).exists())
        self.client.post(f"/people/{self.alice.pk}/ratings/", {a: "9" for a in SkillRating.AXES})
        self.assertEqual(SkillRating.objects.get(member=self.alice).software, 0)
        self.client.post(f"/people/{self.bob.pk}/ratings/", {a: "9" for a in SkillRating.AXES})
        self.assertEqual(SkillRating.objects.get(member=self.bob).software, 0)
        ms = MemberSkill.objects.create(member=self.bob, skill=Skill.objects.create(name="X"))
        self.client.post(f"/people/{self.bob.pk}/skills/{ms.skill_id}/remove/")
        self.assertTrue(MemberSkill.objects.filter(pk=ms.pk).exists())

    def test_non_admin_cannot_review_or_sync(self):
        itype = InductionType.objects.create(name="Makerspace")
        ind = Induction.objects.create(member=self.bob, induction_type=itype)
        self.login(self.lead)  # editor, but not admin
        self.client.post(f"/inductions/{ind.pk}/review/", {"decision": "approved"})
        ind.refresh_from_db()
        self.assertEqual(ind.status, "pending")
        with patch("skills.site_views.sync_directory") as sync:
            self.client.post("/directory/sync/")
            sync.assert_not_called()

    def test_admin_review_and_sync(self):
        itype = InductionType.objects.create(name="Makerspace")
        ind = Induction.objects.create(member=self.bob, induction_type=itype)
        self.login(self.admin)
        self.client.post(f"/inductions/{ind.pk}/review/", {"decision": "approved", "notes": "ok"})
        ind.refresh_from_db()
        self.assertEqual((ind.status, ind.reviewed_by, ind.notes), ("approved", self.admin, "ok"))
        with patch("skills.site_views.sync_directory", return_value={"created": 1, "updated": 2}):
            r = self.client.post("/directory/sync/", follow=True)
        self.assertContains(r, "1 created, 2 updated")

    def test_skill_add_update_remove_happy_path(self):
        self.login(self.alice)
        url = f"/people/{self.alice.pk}/skills/"
        self.client.post(url, {"name": "Python", "category": "Software", "level": "2", "wants_to_learn": "on"})
        ms = MemberSkill.objects.get(member=self.alice)
        self.assertEqual((ms.skill.name, ms.level, ms.wants_to_learn, ms.source), ("Python", 2, True, "manual"))
        self.client.post(url, {"name": "python", "level": "4"})  # case-insensitive update, checkbox cleared
        ms.refresh_from_db()
        self.assertEqual((ms.level, ms.wants_to_learn), (4, False))
        self.assertEqual(Skill.objects.count(), 1)
        self.assertContains(self.client.get(f"/people/{self.alice.pk}/"), "Python")
        self.client.post(f"{url}{ms.skill_id}/remove/")
        self.assertFalse(MemberSkill.objects.exists())

    def test_invalid_skill_rejected_and_lead_can_edit_others(self):
        self.login(self.alice)
        self.client.post(f"/people/{self.alice.pk}/skills/", {"name": "CAD", "level": "9"})
        self.assertFalse(MemberSkill.objects.exists())
        self.login(self.lead)
        self.client.post(f"/people/{self.alice.pk}/skills/", {"name": "CAD", "level": "3"})
        self.assertEqual(MemberSkill.objects.get(member=self.alice).updated_by, self.lead)
        self.client.post(f"/people/{self.alice.pk}/ratings/", {a: "7" for a in SkillRating.AXES})
        self.assertEqual(SkillRating.objects.get(member=self.alice).software, 7)

    def test_induction_request_self_and_rejected_visibility(self):
        itype = InductionType.objects.create(name="Makerspace")
        rej = InductionType.objects.create(name="Laser")
        Induction.objects.create(member=self.bob, induction_type=rej, status="rejected")
        self.login(self.alice)
        self.client.post("/inductions/request/", {"induction_type": itype.pk})
        self.assertTrue(Induction.objects.filter(member=self.alice, induction_type=itype, status="pending").exists())
        self.assertNotContains(self.client.get(f"/people/{self.bob.pk}/"), "Laser")
        self.login(self.bob)
        self.assertContains(self.client.get(f"/people/{self.bob.pk}/"), "Laser")

    def test_empty_profile_invites_first_skill(self):
        self.login(self.alice)
        self.assertContains(self.client.get(f"/people/{self.alice.pk}/"), "Add your first skill")
        self.login(self.bob)
        self.assertNotContains(self.client.get(f"/people/{self.alice.pk}/"), "Add your first skill")

    def test_photo_proxy(self):
        self.alice.avatar_url = "https://s3.example.test/bucket/avatars/u1/pic.jpg"
        self.alice.save()
        url = f"/people/{self.alice.pk}/photo/"
        self.assertEqual(self.client.get(url).status_code, 302)  # logged out -> login
        self.login(self.bob)
        self.assertEqual(self.client.get(f"/people/{self.bob.pk}/photo/").status_code, 404)  # no photo
        with patch("skills.site_views.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.headers = {"Content-Type": "image/jpeg"}
            get.return_value.content = b"jpegbytes"
            r = self.client.get(url)
            self.assertTrue(get.call_args.args[0].endswith("/api/avatars/u1/pic.jpg"))
        self.assertEqual((r.status_code, r["Content-Type"], r.content), (200, "image/jpeg", b"jpegbytes"))
        with patch("skills.site_views.requests.get") as get:  # directory refuses -> monogram fallback
            get.return_value.status_code = 401
            get.return_value.headers = {"Content-Type": "text/plain"}
            self.assertEqual(self.client.get(url).status_code, 404)
