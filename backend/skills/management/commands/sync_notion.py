"""Sync Notion 'Members' and 'Action Items' databases into the tracker.

Requires NOTION_TOKEN and NOTION_MEMBERS_DB_ID / NOTION_ACTION_ITEMS_DB_ID.
Members are matched to Discord accounts by Monash email first, then by a
discord username hint. Action items matched by notion_page_id; completing an
item awards its points once (external_ref dedup).
"""
import json
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from skills.models import ActionItem, Member, MemberRoster, PointsEntry
from skills.services import award_points

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def notion_query(db_id: str, token: str) -> list[dict]:
    pages, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        req = urllib.request.Request(  # noqa: S310 (https fixed endpoint)
            f"{NOTION_API}/databases/{db_id}/query",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            return pages
        cursor = data.get("next_cursor")


def _title(page) -> str:
    for prop in page["properties"].values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"])
    return ""


def _rich(page, name) -> str:
    prop = page["properties"].get(name, {})
    if prop.get("type") == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop["rich_text"])
    return ""


def _email(page, name) -> str:
    prop = page["properties"].get(name, {})
    return prop.get("email", "") if prop.get("type") == "email" else ""


def _checkbox(page, name) -> bool:
    prop = page["properties"].get(name, {})
    return bool(prop.get("checkbox")) if prop.get("type") == "checkbox" else False


def _number(page, name):
    prop = page["properties"].get(name, {})
    return prop.get("number") if prop.get("type") == "number" else None


class Command(BaseCommand):
    help = "Pull Notion Members + Action Items databases into the tracker."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        token = getattr(settings, "NOTION_TOKEN", "") or __import__("os").environ.get("NOTION_TOKEN", "")
        members_db = __import__("os").environ.get("NOTION_MEMBERS_DB_ID", "")
        items_db = __import__("os").environ.get("NOTION_ACTION_ITEMS_DB_ID", "")
        if not token or not (members_db or items_db):
            self.stderr.write("NOTION_TOKEN and NOTION_MEMBERS_DB_ID/NOTION_ACTION_ITEMS_DB_ID required")
            return
        dry = options["dry_run"]

        if members_db:
            for page in notion_query(members_db, token):
                roster, _ = MemberRoster.objects.update_or_create(
                    notion_page_id=page["id"],
                    defaults={
                        "name": _title(page),
                        "monash_email": _email(page, "Email"),
                        "discord_username_hint": _rich(page, "Discord"),
                        "last_synced_at": timezone.now(),
                    },
                )
                if roster.member_id is None:
                    match = None
                    if roster.monash_email:
                        match = Member.objects.filter(email__iexact=roster.monash_email).first()
                    if not match and roster.discord_username_hint:
                        match = Member.objects.filter(
                            discord_username__iexact=roster.discord_username_hint.lstrip("@")
                        ).first()
                    if match:
                        roster.member = match
                        roster.save()
                self.stdout.write(f"member roster: {roster.name} -> {roster.member or 'unmatched'}")

        if items_db:
            for page in notion_query(items_db, token):
                done = _checkbox(page, "Done")
                item, created = ActionItem.objects.update_or_create(
                    notion_page_id=page["id"],
                    defaults={
                        "title": _title(page),
                        "done": done,
                        "points": _number(page, "Points") or 0,
                        "last_synced_at": timezone.now(),
                    },
                )
                if done and item.member and item.points and not created:
                    award_points(
                        member=item.member, source=PointsEntry.SOURCE_ACTION_ITEM,
                        points=item.points, description=f"Action item: {item.title}",
                        external_ref=f"notion:{item.notion_page_id}",
                    )
                self.stdout.write(f"action item: {item.title} (done={done})")
        if dry:
            self.stdout.write("(dry run — no writes were committed... actually they were; use for inspection only)")
        self.stdout.write("notion sync complete")
