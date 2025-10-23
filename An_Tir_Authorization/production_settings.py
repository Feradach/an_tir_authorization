# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

import os
import pymysql
from pathlib import Path

"""
Add the following environment variables to the host:
    DJANGO_SECRET_KEY
    DEBUG
    ALLOWED_HOSTS
    DJANGO_SETTINGS_MODULE = An_Tir_Authorization.settings
    EMAIL_HOST_USER
    EMAIL_HOST_PASSWORD
"""

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") # Stored in the environment
DEBUG = os.environ.get("DEBUG") == "True"  # Stored in the environment
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",") # Stored in the environment

SITE_URL = os.environ.get("SITE_URL", "https://auth.thebusinessduck.com")

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

# Email configuration using Gmail
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')  # Stored in the environment
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')  # Stored in the environment
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

SECURE_SSL_REDIRECT = True

# Use secure cookies for sessions and CSRF protection
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Ensure Django honors Cloudflare/Proxy HTTPS headers
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Enable HTTP Strict Transport Security (HSTS) This is commented out because it is a good idea but I don't know what
# the kingdom is doing now.
SECURE_HSTS_SECONDS = 31536000  # Enforce HTTPS for one year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True

# Optional security headers
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# CSRF trusted origins: build from allowed hosts
CSRF_TRUSTED_ORIGINS = [
    f"https://{h.strip()}" for h in ALLOWED_HOSTS if h and not h.startswith("http")
]

ADMINS = [
    ('Don Reynolds', 'don.k.a.reynolds@outlook.com'),
]
MANAGERS = ADMINS
