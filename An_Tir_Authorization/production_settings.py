# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

import os
import pymysql
from pathlib import Path
from dotenv import load_dotenv

"""
Required environment variables for production:

    DJANGO_SECRET_KEY
    DJANGO_DEBUG
    DJANGO_ALLOWED_HOSTS
    DJANGO_SETTINGS_MODULE=An_Tir_Authorization.settings
"""



# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") # Stored in the environment
DEBUG = os.environ.get("DJANGO_DEBUG", "False").strip().lower() in {"1", "true", "yes", "on"}  # Stored in the environment
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") # Stored in the environment

# Gate test-only features (default off in production; enable via env as needed)
AUTHZ_TEST_FEATURES = os.environ.get('AUTHZ_TEST_FEATURES', '0').strip().lower() in ('1', 'true', 'yes', 'on')
AUTHZ_ENABLE_LEGACY_AUTHORIZATION_IMPORT = os.environ.get(
    'AUTHZ_ENABLE_LEGACY_AUTHORIZATION_IMPORT',
    '0',
).strip().lower() in ('1', 'true', 'yes', 'on')

SITE_URL = os.environ.get("SITE_URL")

# Linux-compatible security events log file
SECURITY_EVENTS_LOG_PATH = '/var/log/an_tir_authorizations/security_events.log'

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

# Shared cache for production throttling. Create the table during deployment with:
#   python manage.py createcachetable django_cache
CACHES = {
    'default': {
        'BACKEND': os.environ.get(
            'DJANGO_CACHE_BACKEND',
            'django.core.cache.backends.db.DatabaseCache',
        ),
        'LOCATION': os.environ.get('DJANGO_CACHE_LOCATION', 'django_cache'),
    }
}


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'authorizations.middleware.MaintenanceLockMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Email configuration (HTTPS)

# Email to file for testing
'''
EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
EMAIL_FILE_PATH = '/home/antir/mail_outbox'
DEFAULT_FROM_EMAIL = 'no-reply@authorizations.antir.org'
'''

# Email configuration
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'authorizations.email_backends.GmailAPIBackend',
)
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL')
if EMAIL_BACKEND != 'django.core.mail.backends.filebased.EmailBackend' and not DEFAULT_FROM_EMAIL:
    raise RuntimeError('DEFAULT_FROM_EMAIL must be set when production email sending is enabled.')

if EMAIL_BACKEND == 'authorizations.email_backends.GmailAPIBackend':
    GMAIL_TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE")
    if not GMAIL_TOKEN_FILE:
        raise RuntimeError('GMAIL_TOKEN_FILE must be set when using the Gmail API email backend.')
elif EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
    EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').strip().lower() in (
        '1',
        'true',
        'yes',
        'on',
    )
    EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '30'))


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
# Keep uploaded files outside the deploy/repo tree so deploy cleanup cannot remove them.
MEDIA_ROOT = Path('/srv/an_tir/media')

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
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        'CSRF_TRUSTED_ORIGINS',
        'https://authorizations.thebusinessduck.com',
    ).split(',')
    if origin.strip()
]

ADMINS = [
    ('Don Reynolds', 'don.k.a.reynolds@outlook.com'),
]
MANAGERS = ADMINS
