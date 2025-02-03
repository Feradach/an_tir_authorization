# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
DEBUG = os.environ.get("DEBUG") == "True"  # Convert string to boolean
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Caches (default for development)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

# Email configuration using Gmail
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')  # Must be set in the environment
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')  # Must be set in the environment
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

SECURE_SSL_REDIRECT = True

# Use secure cookies for sessions and CSRF protection
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Enable HTTP Strict Transport Security (HSTS) This is commented out because it is a good idea but I don't know what
# the kingdom is doing now.
# SECURE_HSTS_SECONDS = 31536000  # Enforce HTTPS for one year
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True

# Optional security headers
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

ADMINS = [
    ('Don Reynolds', 'don.k.a.reynolds@outlook.com'),
]
MANAGERS = ADMINS