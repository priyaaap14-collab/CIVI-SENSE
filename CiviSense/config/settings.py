
"""CiviSense settings. Database credentials can be overridden with environment variables."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Optional .env file next to manage.py
# Real environment variables always take precedence.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


# --------------------------------------------------
# DJANGO CONFIGURATION
# --------------------------------------------------

SECRET_KEY = os.environ.get(
    "CIVISENSE_SECRET_KEY",
    "dev-only-change-me-civisense-2026"
)

DEBUG = os.environ.get("CIVISENSE_DEBUG", "1") == "1"

if not DEBUG and SECRET_KEY.startswith("dev-only"):
    raise ImproperlyConfigured(
        "Set CIVISENSE_SECRET_KEY to a long random value "
        "when CIVISENSE_DEBUG=0."
    )

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]


# --------------------------------------------------
# INSTALLED APPLICATIONS
# --------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]


# --------------------------------------------------
# MIDDLEWARE
# --------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# --------------------------------------------------
# URL AND TEMPLATE CONFIGURATION
# --------------------------------------------------

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.views.notifications_context",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"


# --------------------------------------------------
# DATABASE CONFIGURATION - POSTGRESQL
# --------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("CIVISENSE_DB_NAME", "civisense"),
        "USER": os.environ.get("CIVISENSE_DB_USER", "civisense"),
        "PASSWORD": os.environ.get(
            "CIVISENSE_DB_PASSWORD",
            "civisense123"
        ),
        "HOST": os.environ.get("CIVISENSE_DB_HOST", "localhost"),
        "PORT": os.environ.get("CIVISENSE_DB_PORT", "5432"),
    }
}


# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"
    },
]

LOGIN_URL = "/"


# --------------------------------------------------
# LANGUAGE AND TIMEZONE
# --------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"

USE_I18N = True
USE_TZ = True


# --------------------------------------------------
# STATIC AND MEDIA FILES
# --------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024


# --------------------------------------------------
# SESSION AND SECURITY SETTINGS
# --------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"


# --------------------------------------------------
# CIVISENSE DATA AND ML CONFIGURATION
# --------------------------------------------------

RAW_DATASET = (
    BASE_DIR / "data" / "raw" /
    "bengaluru_civic_complaints_raw.csv"
)

CLEAN_DATASET = (
    BASE_DIR / "data" / "cleaned" /
    "complaints_clean.csv"
)

WRANGLING_REPORT = (
    BASE_DIR / "data" / "cleaned" /
    "wrangling_report.json"
)

ML_ARTIFACTS = BASE_DIR / "ml" / "artifacts"


# --------------------------------------------------
# AI ASSISTANT CONFIGURATION - GEMINI
# --------------------------------------------------

# Gemini API key: server-side only.
# Never expose this key in HTML, JavaScript, or frontend code.

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = os.environ.get(
    "CIVISENSE_GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)

ASSISTANT_MAX_TOKENS = int(
    os.environ.get(
        "CIVISENSE_ASSISTANT_MAX_TOKENS",
        "1500"
    )
)

# Maximum number of tool-calling rounds per assistant response.
ASSISTANT_MAX_TOOL_ROUNDS = 6

# Maximum conversation history retained per browser session.
ASSISTANT_MAX_HISTORY = 24

# Assistant request rate limit per minute.
ASSISTANT_RATE_PER_MINUTE = int(
    os.environ.get("CIVISENSE_ASSISTANT_RATE", "12")
)


# --------------------------------------------------
# MCP SERVER SECURITY
# --------------------------------------------------

# Used to sign MCP identity and approval tokens.
# Falls back to Django SECRET_KEY when not separately configured.

MCP_SECRET = (
    os.environ.get("CIVISENSE_MCP_SECRET")
    or SECRET_KEY
)

