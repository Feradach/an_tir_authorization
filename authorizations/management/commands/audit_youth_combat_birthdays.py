from datetime import date

from django.core.management.base import BaseCommand

from authorizations.models import Authorization


class Command(BaseCommand):
    help = 'Report active youth combatant authorizations that are missing birthdays.'

    def handle(self, *args, **options):
        today = date.today()
        rows = (
            Authorization.objects.select_related('person__user', 'style__discipline', 'status')
            .filter(
                status__name='Active',
                expiration__gte=today,
                person__user__birthday__isnull=True,
                style__discipline__name__in=['Youth Armored', 'Youth Rapier'],
            )
            .exclude(style__name__in=['Junior Marshal', 'Senior Marshal'])
            .order_by('person__sca_name', 'style__discipline__name', 'style__name', 'id')
        )

        count = rows.count()
        if not count:
            self.stdout.write(self.style.SUCCESS('No active youth combatant authorizations are missing birthdays.'))
            return

        self.stdout.write(
            'Active youth combatant authorizations missing birthdays:'
        )
        self.stdout.write('authorization_id,person_id,sca_name,discipline,style,expiration')
        for auth in rows:
            self.stdout.write(
                f'{auth.id},{auth.person.user_id},"{auth.person.sca_name}",'
                f'{auth.style.discipline.name},"{auth.style.name}",{auth.expiration.isoformat()}'
            )
        self.stdout.write(self.style.WARNING(f'{count} active youth combatant authorization(s) need birthday fixes.'))
