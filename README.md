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
  file locally, full access) + `X-Caller-Member: <discord_id>` for on-behalf-of
  calls; admin-issued API keys (`stk_...`, created at `/api-keys/`, stored
  hashed, shown once) sent as `X-API-Key: <key>` or `Authorization: Bearer <key>`
  with `read` (GET only, else 403) or `write` scope, and they honour
  `X-Caller-Member` too; browser sessions via Discord OAuth
  (`/api/auth/discord/`) issue signed session tokens for the training site.
  Checked in that order, else 401. The admin page `/api-keys/` also holds the
  API reference.
- Every `/api/...` path is also served at `/api/v1/...`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health/` | liveness (open) |
| GET/POST | `/api/members/` | upsert a member |
| GET | `/api/members/<discord_id>/` | one member's profile (skills, lessons, inductions, points) |
| POST | `/api/members/<discord_id>/points/` | award points (bounty / engagement / lesson / action_item / manual), `external_ref` dedup |
| PUT | `/api/members/<discord_id>/skills/` | set 6-axis skill ratings (admin) |
| GET | `/api/members/<discord_id>/quiz-attempts/` | quiz attempt history |
| GET | `/api/people/` | non-archived people; filters `name`, `email`, `discordId`, `directoryId`, `position`, `skill`, `wantsToLearn`, `status`; `fields=a,b` trims the response |
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

## Website / directory / Authentik

The website logs members in with Authentik (OIDC) and reads who exists, and their
org positions, from the People Directory. Members removed from the directory, or whose
directory status is `former` or `inactive`, are archived (hidden, never deleted) and
come back automatically if they return.

| Env var | Purpose |
|---|---|
| `SITE_URL` | public origin, e.g. `https://skills.monashautomation.com` (redirect URI + CSRF origin) |
| `AUTHENTIK_ISSUER` / `AUTHENTIK_CLIENT_ID` / `AUTHENTIK_CLIENT_SECRET` | OIDC app (issuer like `https://auth.monashautomation.com/application/o/skills/`) |
| `DIRECTORY_API_KEY` / `DIRECTORY_URL` | People Directory access (URL defaults to directory.monashautomation.com) |
| `SKILLS_EDITOR_POSITIONS` | comma-separated position substrings that may edit others' skills (default `Lead,Chief,Executive,Manager`); directory admins always can |
| `LEADERSHIP_GROUPS` | comma-separated Authentik group names that can award points to anyone and approve members' point submissions on the website (Points page). Empty = admins only |
| `DATABASE_URL` | `postgres://user:pass@host:5432/db`; sqlite when unset |
| `DEV_LOGIN_EMAIL` | with `DJANGO_DEBUG=true` only: `/auth/login/` logs in as this member |

```bash
.venv/bin/python manage.py sync_directory   # cron this; also POST /api/directory/sync/
```

Website routes: `/login/`, `/auth/login/`, `/auth/callback/`, `/auth/logout/`.
New API: `GET /api/skills/`, `PUT /api/members/<ident>/skill-items/`,
`DELETE /api/members/<ident>/skill-items/<skill_id>/`, `GET /api/people/` (filters and `fields=`, see table),
`POST /api/directory/sync/`. `<ident>` is a Discord id, directory id or email.

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
