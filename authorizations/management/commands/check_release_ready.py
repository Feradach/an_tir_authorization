import os
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from authorizations.changelog import unreleased_has_displayable_entries


FILE_EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
LOCMEM_CACHE_BACKEND = 'django.core.cache.backends.locmem.LocMemCache'
MYSQL_BACKEND = 'django.db.backends.mysql'
SQLITE_BACKEND = 'django.db.backends.sqlite3'
WHITENOISE_MIDDLEWARE = 'whitenoise.middleware.WhiteNoiseMiddleware'


class Command(BaseCommand):
    help = 'Check whether the current checkout is ready for production release.'

    def _production_configuration_findings(self):
        errors = []
        warnings = []

        if getattr(settings, 'DEBUG', False):
            errors.append('DJANGO_DEBUG must be False for production.')

        secret_key = getattr(settings, 'SECRET_KEY', '')
        if not secret_key or secret_key in {'default-development-key', 'replace-with-a-long-random-secret'}:
            errors.append('DJANGO_SECRET_KEY must be set to a real production secret.')

        if getattr(settings, 'AUTHZ_TEST_FEATURES', False):
            errors.append('AUTHZ_TEST_FEATURES must be disabled for production.')

        allowed_hosts = getattr(settings, 'ALLOWED_HOSTS', [])
        if not allowed_hosts:
            errors.append('DJANGO_ALLOWED_HOSTS must include the production host name.')
        elif '*' in allowed_hosts:
            errors.append('DJANGO_ALLOWED_HOSTS must not include wildcard "*" in production.')

        site_url = getattr(settings, 'SITE_URL', '')
        parsed_site_url = urlparse(site_url)
        if not site_url:
            errors.append('SITE_URL must be set for production.')
        elif parsed_site_url.scheme != 'https' or not parsed_site_url.netloc:
            errors.append('SITE_URL must be a production HTTPS URL.')
        elif parsed_site_url.hostname not in allowed_hosts:
            errors.append('SITE_URL host must be included in DJANGO_ALLOWED_HOSTS.')

        csrf_trusted_origins = getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])
        if not csrf_trusted_origins:
            errors.append('CSRF_TRUSTED_ORIGINS must include the production HTTPS origin.')
        elif any(not origin.startswith('https://') for origin in csrf_trusted_origins):
            errors.append('CSRF_TRUSTED_ORIGINS must use HTTPS origins in production.')
        elif site_url and f'{parsed_site_url.scheme}://{parsed_site_url.netloc}' not in csrf_trusted_origins:
            errors.append('CSRF_TRUSTED_ORIGINS must include SITE_URL origin.')

        default_database = settings.DATABASES.get('default', {})
        if default_database.get('ENGINE') == SQLITE_BACKEND:
            errors.append('DB_ENGINE must not be SQLite for production.')
        elif default_database.get('ENGINE') != MYSQL_BACKEND:
            errors.append('DB_ENGINE must be django.db.backends.mysql for production.')

        for key, env_name in (
            ('NAME', 'DB_NAME'),
            ('USER', 'DB_USER'),
            ('PASSWORD', 'DB_PASSWORD'),
            ('HOST', 'DB_HOST'),
            ('PORT', 'DB_PORT'),
        ):
            if not default_database.get(key):
                errors.append(f'{env_name} must be set for production.')

        default_cache = settings.CACHES.get('default', {})
        if default_cache.get('BACKEND') == LOCMEM_CACHE_BACKEND:
            errors.append('DJANGO_CACHE_BACKEND must not use local-memory cache in production.')

        email_delivery_mode = getattr(settings, 'EMAIL_DELIVERY_MODE', '')
        email_backend = getattr(settings, 'EMAIL_BACKEND', '')
        if email_delivery_mode == 'file' or email_backend == FILE_EMAIL_BACKEND:
            errors.append('EMAIL_DELIVERY_MODE must not be file for production.')
        elif email_delivery_mode not in {'gmail', 'smtp'}:
            errors.append('EMAIL_DELIVERY_MODE must be gmail or smtp for production.')

        if not getattr(settings, 'DEFAULT_FROM_EMAIL', ''):
            errors.append('DEFAULT_FROM_EMAIL must be set for production email.')
        elif 'example.com' in getattr(settings, 'DEFAULT_FROM_EMAIL', ''):
            errors.append('DEFAULT_FROM_EMAIL must not use an example.com address in production.')

        if not getattr(settings, 'SERVER_EMAIL', ''):
            errors.append('SERVER_EMAIL must be set for production error email.')
        elif 'example.com' in getattr(settings, 'SERVER_EMAIL', ''):
            errors.append('SERVER_EMAIL must not use an example.com address in production.')

        if not getattr(settings, 'ADMINS', []):
            errors.append('DJANGO_ADMIN_EMAILS must include at least one production alert recipient.')

        if email_delivery_mode == 'gmail' and not getattr(settings, 'GMAIL_TOKEN_FILE', ''):
            errors.append('GMAIL_TOKEN_FILE must be set when EMAIL_DELIVERY_MODE=gmail.')
        elif email_delivery_mode != 'gmail' and not os.environ.get('GMAIL_TOKEN_FILE'):
            warnings.append('GMAIL_TOKEN_FILE is not set. This is okay unless EMAIL_DELIVERY_MODE is changed to gmail.')

        if email_delivery_mode == 'smtp':
            for setting_name, env_name in (
                ('EMAIL_HOST', 'EMAIL_HOST'),
                ('EMAIL_HOST_USER', 'EMAIL_HOST_USER'),
                ('EMAIL_HOST_PASSWORD', 'EMAIL_HOST_PASSWORD'),
            ):
                if not getattr(settings, setting_name, ''):
                    errors.append(f'{env_name} must be set when EMAIL_DELIVERY_MODE=smtp.')
        elif email_delivery_mode != 'smtp':
            missing_smtp_settings = [
                env_name
                for env_name in (
                    'EMAIL_HOST',
                    'EMAIL_HOST_USER',
                    'EMAIL_HOST_PASSWORD',
                )
                if not os.environ.get(env_name)
            ]
            if missing_smtp_settings:
                warnings.append(
                    f'{", ".join(missing_smtp_settings)} not set. '
                    'This is okay unless EMAIL_DELIVERY_MODE is changed to smtp.'
                )

        if not getattr(settings, 'USE_X_FORWARDED_HOST', False):
            errors.append('USE_X_FORWARDED_HOST must be True for production proxy hosting.')

        if not getattr(settings, 'SECURE_SSL_REDIRECT', False):
            errors.append('SECURE_SSL_REDIRECT must be True for production.')

        if getattr(settings, 'SECURE_HSTS_SECONDS', 0) <= 0:
            errors.append('SECURE_HSTS_SECONDS must be greater than 0 for production.')

        if not getattr(settings, 'CSRF_COOKIE_SECURE', False):
            errors.append('CSRF_COOKIE_SECURE must be True for production.')

        if not getattr(settings, 'SESSION_COOKIE_SECURE', False):
            errors.append('SESSION_COOKIE_SECURE must be True for production.')

        if not getattr(settings, 'SECURE_CONTENT_TYPE_NOSNIFF', False):
            errors.append('SECURE_CONTENT_TYPE_NOSNIFF must be True for production.')

        if not any(middleware == WHITENOISE_MIDDLEWARE for middleware in settings.MIDDLEWARE):
            errors.append('USE_WHITENOISE must be True for production static files.')

        media_root = getattr(settings, 'MEDIA_ROOT', None)
        if not media_root:
            errors.append('MEDIA_ROOT must be set for production uploads.')
        else:
            try:
                if media_root.resolve().is_relative_to(settings.BASE_DIR.resolve()):
                    errors.append('MEDIA_ROOT should be outside the deploy/repo tree in production.')
            except OSError:
                errors.append('MEDIA_ROOT could not be resolved.')

        return errors, warnings

    def handle(self, *args, **options):
        release_env = getattr(settings, 'RELEASE_ENV', '')
        self.stdout.write(f'Release environment: {release_env or "not set"}')

        if release_env != 'production':
            self.stdout.write(self.style.WARNING('Production release gates skipped.'))
            return

        configuration_errors, configuration_warnings = self._production_configuration_findings()
        for warning in configuration_warnings:
            self.stdout.write(self.style.WARNING(f'Warning: {warning}'))

        if configuration_errors:
            raise CommandError(
                'Production configuration is not release-ready. Fix these misconfigured settings before deploying:\n'
                + '\n'.join(f'- {error}' for error in configuration_errors)
            )

        changelog_path = settings.BASE_DIR / 'CHANGELOG.md'
        if not changelog_path.exists():
            raise CommandError('CHANGELOG.md was not found.')

        try:
            changelog_text = changelog_path.read_text(encoding='utf-8')
        except OSError as exc:
            raise CommandError(f'Could not read CHANGELOG.md: {exc}') from exc

        if unreleased_has_displayable_entries(changelog_text):
            raise CommandError(
                'CHANGELOG.md has unreleased entries. Move them into a numbered release before deploying production.'
            )

        self.stdout.write(self.style.SUCCESS('Release readiness check passed.'))
