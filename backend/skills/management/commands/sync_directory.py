"""Sync members from the People Directory (run from cron / on demand)."""
from django.core.management.base import BaseCommand, CommandError

from skills.directory import DirectoryError, sync_directory


class Command(BaseCommand):
    help = "Sync members, positions and admin flags from the People Directory"

    def handle(self, *args, **opts):
        try:
            counts = sync_directory()
        except DirectoryError as exc:
            raise CommandError(str(exc))
        self.stdout.write(" ".join(f"{k}={v}" for k, v in counts.items()))
