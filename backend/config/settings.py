"""Django settings for the Monash Automation skill tracker.

Standalone service split out of discordbot. Auth is a shared bearer token
(SKILL_TRACKER_TOKEN) for bot/server-to-server calls; admin actions require
the caller Member to carry is_admin=True.
"""
import os
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "skills",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "skills.middleware.CorsMiddleware",
    "skills.middleware.ApiTokenMiddleware",
    "skills.middleware.SiteCsrfMiddleware",
    "skills.middleware.SiteMemberMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SKILL_TRACKER_DB", str(BASE_DIR / "db.sqlite3")),
    }
}
# DATABASE_URL=postgres://user:pass@host:5432/dbname switches to Postgres (psycopg2).
if os.environ.get("DATABASE_URL"):
    _db = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _db.path.lstrip("/"),
        "USER": urllib.parse.unquote(_db.username or ""),
        "PASSWORD": urllib.parse.unquote(_db.password or ""),
        "HOST": _db.hostname or "",
        "PORT": str(_db.port or ""),
        "CONN_MAX_AGE": 60,
    }

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# Shared secret for API calls (discordbot / Tamarin / training site backend).
# Env var wins; otherwise read from SKILL_TRACKER_TOKEN_FILE (default .token
# next to the db) so local dev doesn't put secrets in shell history.
def _load_token() -> str:
    env = os.environ.get("SKILL_TRACKER_TOKEN", "")
    if env:
        return env
    token_file = Path(os.environ.get("SKILL_TRACKER_TOKEN_FILE", str(BASE_DIR / ".token")))
    try:
        return token_file.read_text().strip()
    except OSError:
        return ""


SKILL_TRACKER_TOKEN = _load_token()

# --- Discord OAuth (training site login) -----------------------------------
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")  # noqa: S105
DISCORD_REDIRECT_URI = os.environ.get(
    "DISCORD_REDIRECT_URI", "http://127.0.0.1:8731/api/auth/discord/callback/"
)
# Comma-separated Discord snowflakes auto-promoted to is_admin on login.
DISCORD_ADMIN_IDS = os.environ.get("DISCORD_ADMIN_IDS", "")

# Where the SPA lives; OAuth callback redirects there with #token=...
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")

# Origins allowed to call the API from a browser.
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

# --- Website (skills.monashautomation.com) -----------------------------------
# Public origin; redirect URIs are built from this, never from the request host
# (the pod sits behind a proxy that does not forward Host).
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
CSRF_TRUSTED_ORIGINS = [SITE_URL]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Authentik OIDC (hand-rolled code flow; see skills/auth_authentik.py).
AUTHENTIK_ISSUER = os.environ.get("AUTHENTIK_ISSUER", "")
AUTHENTIK_CLIENT_ID = os.environ.get("AUTHENTIK_CLIENT_ID", "")
AUTHENTIK_CLIENT_SECRET = os.environ.get("AUTHENTIK_CLIENT_SECRET", "")  # noqa: S105
# Honoured only when DEBUG is true: /auth/login/ logs straight in as this email.
DEV_LOGIN_EMAIL = os.environ.get("DEV_LOGIN_EMAIL", "")

# People Directory (source of truth for who exists + org positions).
DIRECTORY_URL = os.environ.get("DIRECTORY_URL", "https://directory.monashautomation.com").rstrip("/")
DIRECTORY_API_KEY = os.environ.get("DIRECTORY_API_KEY", "")
# Members whose directory position contains one of these (case-insensitive)
# can edit other members' skills and ratings, as can admins.
SKILLS_EDITOR_POSITIONS = [
    p.strip()
    for p in os.environ.get("SKILLS_EDITOR_POSITIONS", "Lead,Chief,Executive,Manager").split(",")
    if p.strip()
]
