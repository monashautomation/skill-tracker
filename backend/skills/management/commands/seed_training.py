"""Seed the KUKA training content agreed in the 2026-10-25 kickoff.

Idempotent: safe to re-run. Content is placeholder lesson text — admins
replace it via Django admin or the lesson editor later.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from skills.models import (
    EquipmentGroup,
    InductionType,
    Lesson,
    Quiz,
    RoleRule,
)

KUKA_LESSONS = [
    {
        "title": "KUKA Safety Basics",
        "summary": "E-stops, safety zones, and what not to do near the arm.",
        "points": 10,
        "requires_induction": True,
        "body": (
            "# KUKA Safety Basics\n\n"
            "- The E-stop is the red button on the teach pendant and the cell.\n"
            "- Never enter the arm's range of motion while it is powered.\n"
            "- Confirm the mode key is in T1 for teaching.\n"
        ),
        "quiz": [
            {
                "question": "Where are the E-stop buttons on the KUKA cell?",
                "options": ["Teach pendant and cell", "Only the control cabinet",
                            "Only on the wall", "There is no E-stop"],
                "answer_index": 0,
            },
            {
                "question": "Which mode key position is used for teaching?",
                "options": ["AUT", "T1", "T2", "EXT"],
                "answer_index": 1,
            },
        ],
    },
    {
        "title": "KUKA Sunrise.OS Navigation",
        "summary": "Visual Studio plug-in, workcells, and the program tree.",
        "points": 10,
        "requires_induction": True,
        "body": (
            "# Sunrise.OS Navigation\n\n"
            "- Projects live in the Eclipse-based Sunrise Workbench.\n"
            "- A workcell bundles the robot station and its inputs/outputs.\n"
            "- Programs are Java; move commands come from the built-in command set.\n"
        ),
        "quiz": [
            {
                "question": "Sunrise.OS programs are written in…",
                "options": ["Python", "KRL", "Java", "C++"],
                "answer_index": 2,
            },
        ],
    },
    {
        "title": "Pick and Place Project",
        "summary": "Drive the arm to pick a cube and place it on the target.",
        "points": 25,
        "requires_induction": True,
        "body": (
            "# Pick and Place Project\n\n"
            "Use the built-in move commands to reach the cube, close the gripper, "
            "move to the target pad, and release. The simulation sandbox has "
            "gripperOpen/gripperClose commands.\n"
        ),
        "quiz": [],
    },
]


class Command(BaseCommand):
    help = "Seed equipment groups, inductions, KUKA lessons/quizzes, role rules."

    def handle(self, *args, **options):
        kuka, _ = EquipmentGroup.objects.get_or_create(
            key="kuka",
            defaults={"name": "KUKA Arm", "order": 1,
                      "description": "Collaborative KUKA arm in the Smart Manufacturing Lab"},
        )
        for key, name, order in [
            ("cnc", "CNC Mill", 2), ("prusa", "Prusa 3D Printer", 3), ("doosan", "Doosan Arm", 4),
        ]:
            EquipmentGroup.objects.get_or_create(key=key, defaults={"name": name, "order": order})

        induction, _ = InductionType.objects.get_or_create(
            name="KUKA Physical Induction",
            defaults={"description": "In-person induction on the KUKA arm (Keenan/Keenan-approved)"},
        )

        for i, spec in enumerate(KUKA_LESSONS):
            lesson, created = Lesson.objects.get_or_create(
                slug=spec["title"].lower().replace(" ", "-"),
                defaults={
                    "group": kuka, "title": spec["title"], "summary": spec["summary"],
                    "points": Decimal(spec["points"]),
                    "requires_induction": induction if spec["requires_induction"] else None,
                    "order": i, "published": True, "body_markdown": spec["body"],
                },
            )
            if spec["quiz"] and created:
                Quiz.objects.create(lesson=lesson, questions=spec["quiz"], pass_mark=60)
            self.stdout.write(f"{'created' if created else 'exists'}: {lesson.title}")

        for threshold, name in [(100, "Contributor"), (500, "Senior Contributor")]:
            RoleRule.objects.get_or_create(
                discord_role_name=name,
                defaults={"threshold_points": Decimal(threshold), "discord_role_id": "TBD"},
            )
        self.stdout.write("seed complete")
