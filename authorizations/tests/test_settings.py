from An_Tir_Authorization.settings import *

# Use isolated SQLite for tests.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
AUTHZ_TEST_FEATURES = True
SITE_URL = 'http://testserver'
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']

# Keep logs quiet in test output.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
}
