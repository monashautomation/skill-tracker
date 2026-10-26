"""Django settings for the Monash Automation skill tracker.

Standalone service split out of discordbot. Auth is a shared bearer token
(SKILL_TRACKER_TOKEN) for bot/server-to-server calls; admin actions require
the caller Member to carry is_admin=True.
"""
import os
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
    "django.contrib.sessions.middleware.SessionMiddleware",
    "skills.middleware.CorsMiddleware",
    "skills.middleware.ApiTokenMiddleware",
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

STATIC_URL = "/static/"
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
