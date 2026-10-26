# skill-tracker

Standalone Monash Automation skill tracker — split out of
[discordbot](https://github.com/monashautomation/discordbot) (discordbot.monashautomation.com).

Tracks member skill ratings (6-axis hexagon), a points ledger, physical
inductions, and scalable training content (KUKA arm first, then CNC / Prusa /
Doosan). The Discord bot (Tamarin) consumes this API to upgrade Discord roles
as members progress; the KUKA training site calls it to award lesson and
action-item points.

## Stack

- Backend: Django 6 (SQLite for dev, Postgres in prod)
- CLI: `mastcli` (Rust, in `cli/`) — same house style as `maticket`/`maims`
- Auth: shared bearer token (`SKILL_TRACKER_TOKEN` env, or `backend/.token`
  file locally) + `X-Caller-Member: <discord_id>` for on-behalf-of calls;
  browser sessions via Discord OAuth (`/api/auth/discord/`) issue signed
  session tokens for the training site

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health/` | liveness (open) |
| GET/POST | `/api/members/` | upsert a member |
| GET | `/api/members/<discord_id>/` | one member's profile (skills, lessons, inductions, points) |
| POST | `/api/members/<discord_id>/points/` | award points (bounty / engagement / lesson / action_item / manual), `external_ref` dedup |
| PUT | `/api/members/<discord_id>/skills/` | set 6-axis skill ratings (admin) |
| GET | `/api/members/<discord_id>/quiz-attempts/` | quiz attempt history |
| GET | `/api/leaderboard/` | points ranking |
| GET | `/api/catalog/` | equipment groups + lessons |
| GET | `/api/lessons/<id>/` | lesson detail (markdown body, quiz meta) |
| GET | `/api/lessons/<id>/quiz/` | quiz questions (answers never sent) |
| POST | `/api/lessons/<id>/quiz/attempt/` | grade quiz server-side, records attempt |
| POST | `/api/lessons/<id>/complete/` | complete a lesson (enforces induction gate + quiz pass, awards points) |
| POST | `/api/inductions/` | request a physical induction |
| GET | `/api/inductions/list/` | inductions (admins see all) |
| PATCH | `/api/inductions/<id>/review/` | admin approves/rejects |
| GET/POST | `/api/action-items/` | list / create meeting action items |
| PATCH | `/api/action-items/<id>/` | mark done → awards its points once |
| GET/POST | `/api/role-rules/` | points → Discord role thresholds (admin writes) |
| GET | `/api/auth/discord/login/` | Discord OAuth start (training site) |
| GET | `/api/auth/whoami/` | resolve a session token |

Django admin at `/admin/` for manual content management.

## CLI (`mastcli`)

```bash
cd cli && cargo build --release
target/release/mastcli auth login -u skills.monashautomation.com -t <token>
mastcli leaderboard
mastcli catalog
mastcli points add 111 50 --source bounty --external-ref bounty:42
mastcli lessons quiz 1 --member 111 --answers 0,1,0
mastcli inductions review 4 approved --caller <admin_discord_id>
mastcli action-items create 'Fix gripper' --caller <admin> --member 111 --points 20
```

Config: `~/.config/mastcli/config.json` (0600), or `MASTCLI_URL`/`MASTCLI_TOKEN`
env, or `-u/-t` flags. `--json` for raw output.

## Run locally

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "your-dev-token" > .token
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_training   # KUKA lessons + quizzes + role rules
.venv/bin/python manage.py runserver
```

Notion sync (members + action items) via cron:

```bash
NOTION_TOKEN=*** NOTION_MEMBERS_DB_ID=… NOTION_ACTION_ITEMS_DB_ID=… \
  .venv/bin/python manage.py sync_notion
```

Tests: `python manage.py test skills` (backend) and `cargo test` (cli).
CI runs both. Docker: `docker build -t skill-tracker . && docker run -p 8000:8000 skill-tracker`.

## Domain model

`Member` · `SkillRating` (6 axes, 0–10) · `PointsEntry` (ledger, `external_ref`
dedup) · `InductionType`/`Induction` (physical gate) ·
`EquipmentGroup`/`Lesson`/`Quiz`/`QuizAttempt`/`LessonCompletion` (training) ·
`ActionItem` (meeting follow-ups → points) · `RoleRule` (points → Discord role).
