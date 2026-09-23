"""
Django settings for SmartAnalytica: Content Detection and Analysis.

Thesis reference: Chapter 3, Project Design.
"""

import os
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'academic',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'plagiarism_detector.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'academic.context_processors.role_flags',
            ],
        },
    },
]

WSGI_APPLICATION = 'plagiarism_detector.wsgi.application'
ASGI_APPLICATION = 'plagiarism_detector.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'home'

# --- SmartAnalytica detection settings (thesis section 3.2.3) ---

# Similarity percentage at or above which a submission pair is flagged.
PLAGIARISM_SIMILARITY_THRESHOLD = config(
    'PLAGIARISM_SIMILARITY_THRESHOLD', default=40.0, cast=float
)

# Upload guard rails (thesis section 2.1.2).
MAX_SUBMISSION_SIZE_MB = config('MAX_SUBMISSION_SIZE_MB', default=20, cast=int)
ALLOWED_SUBMISSION_EXTENSIONS = config(
    'ALLOWED_SUBMISSION_EXTENSIONS',
    default='.pdf,.docx,.txt,.md,.py,.java,.c,.cpp,.js,.ts,.zip,.rar',
    cast=Csv(),
)

# Teacher self-registration gate (thesis section 2.1.1, authorization).
#
# Without this, anyone could pick "Teacher" on the public registration form and
# gain access to every course, submission and plagiarism report in the system.
# Registering as a teacher requires this code, which the department issues to
# real staff.
#
# Left empty, teacher self-registration is disabled entirely rather than left
# open: an unconfigured deployment fails closed.
TEACHER_ACCESS_CODE = config('TEACHER_ACCESS_CODE', default='')

# Optional sentence-embedding model for semantic paraphrase detection.
# If sentence-transformers is not installed the pipeline falls back to
# scikit-learn LSA, then to a pure-Python n-gram model. See detection/semantic.py.
SEMANTIC_MODEL_NAME = config('SEMANTIC_MODEL_NAME', default='all-MiniLM-L6-v2')
SEMANTIC_ANALYSIS_ENABLED = config('SEMANTIC_ANALYSIS_ENABLED', default=True, cast=bool)

# Security headers, applied when DEBUG is off.
if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=31536000, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
