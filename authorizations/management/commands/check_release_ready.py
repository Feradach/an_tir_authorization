from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from authorizations.changelog import unreleased_has_displayable_entries


class Command(BaseCommand):
    help = 'Check whether the current checkout is ready for production release.'

    def handle(self, *args, **options):
        release_env = getattr(settings, 'RELEASE_ENV', '')
        self.stdout.write(f'Release environment: {release_env or "not set"}')

        if release_env != 'production':
            self.stdout.write(self.style.WARNING('Production release gates skipped.'))
            return

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
