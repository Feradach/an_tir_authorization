# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

import os
import pymysql
from pathlib import Path
from dotenv import load_dotenv

"""
Required environment variables for production:

    DJANGO_SECRET_KEY
    DEBUG
    DJANGO_ALLOWED_HOSTS
    DJANGO_SETTINGS_MODULE=An_Tir_Authorization.settings
"""



# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") # Stored in the environment
DEBUG = os.environ.get("DEBUG") == "True"  # Stored in the environment
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") # Stored in the environment

# Gate test-only features (default off in production; enable via env as needed)
AUTHZ_TEST_FEATURES = os.environ.get('AUTHZ_TEST_FEATURES', '0').strip().lower() in ('1', 'true', 'yes', 'on')

SITE_URL = os.environ.get("SITE_URL", "https://authorizations.thebusinessduck.com")

# Use PyMySQL as MySQLdb backend
pymysql.install_as_MySQLdb()

# MySQL database (configure via environment)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', ''),
        'USER': os.environ.get('DB_USER', ''),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            'charset': 'utf8mb4',
        },
    }
}

# Caches (default for development)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Email configuration (production-safe)
# ------------------------------------
# DigitalOcean blocks outbound SMTP. To prevent email failures
# from crashing Gunicorn workers, production uses a file-based
# email backend. Messages are written to disk and can be inspected
# or replayed manually if needed.

EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
EMAIL_FILE_PATH = '/home/antir/mail_outbox'

DEFAULT_FROM_EMAIL = 'no-reply@authorizations.antir.org'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

USE_X_FORWARDED_HOST = True

# Unsecure settings, use while HTTPS is not working
'''
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'http')
SECURE_SSL_REDIRECT = False
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = ['IP ADDRESS']
CSRF_TRUSTED_ORIGINS = ['http://authorizations.thebusinessduck.com']
'''

# Secure Settings, Comment out while HTTPS is not working
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000  # Enforce HTTPS for one year
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
CSRF_TRUSTED_ORIGINS = ['https://authorizations.thebusinessduck.com']

ADMINS = [
    ('Don Reynolds', 'don.k.a.reynolds@outlook.com'),
]
MANAGERS = ADMINS