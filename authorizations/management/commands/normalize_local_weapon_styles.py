from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, transaction

from authorizations.models import (
    Authorization,
    Discipline,
    LegacyAuthorizationRecoveryEntry,
    Sanction,
    WeaponStyle,
)


EXPECTED_DATABASE_NAME = 'antir_auth_local'
FORBIDDEN_DATABASE_NAME = 'antir_auth_legacy'

DISCIPLINE_RENAMES = {
    'Armored': 'Armored Combat',
    'Rapier': 'Rapier Combat',
    'Missile': 'Missile Combat',
    'Archery': 'Target Archery',
    'Thrown': 'Thrown Weapons',
}

STYLE_RENAMES = {
    'Rapier Combat': {
        'Sword & Offensive Secondary': 'Sword w/Offensive Secondary',
        'Sword & Defensive Secondary': 'Sword w/Defensive Secondary',
        'Two-Handed Sword': 'Two Handed Sword',
    },
    'Cut & Thrust': {
        'Single Sword': 'Single Sword w/Secondaries',
        'Sword & Offensive Secondary': 'Single Sword w/Secondaries',
        'Sword & Defensive Secondary': 'Single Sword w/Secondaries',
        'Two-Handed Sword': 'Two Handed Sword',
    },
    'Missile Combat': {
        'Hand Thrown Weapons': 'Hand Thrown',
    },
    'Equestrian': {
        'Junior Ground Crew': 'Ground Crew - Junior',
        'Senior Ground Crew': 'Ground Crew - Senior',
        'Mounted Crest Combat': 'Crest Combat',
        'Mounted Combat': 'Mounted Heavy Combat',
        'Foam-Tipped Jousting': 'Jousting',
        'Ground Driving': 'Driving',
    },
}


class Command(BaseCommand):
    help = 'Normalize the local database weapon style names to production names.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Write changes. Without this flag the command only reports what would change.',
        )
        parser.add_argument(
            '--database',
            default=DEFAULT_DB_ALIAS,
            help='Database alias to update. The resolved database name must be antir_auth_local.',
        )

    def handle(self, *args, **options):
        database_alias = options['database']
        apply_changes = options['apply']
        database_name = connections.databases[database_alias].get('NAME')

        if str(database_name) == FORBIDDEN_DATABASE_NAME:
            raise CommandError('Refusing to modify antir_auth_legacy.')
        if str(database_name) != EXPECTED_DATABASE_NAME:
            raise CommandError(
                f'Refusing to modify database {database_name!r}. '
                f'This command only runs against {EXPECTED_DATABASE_NAME!r}.'
            )

        planned_changes = []
        with transaction.atomic(using=database_alias):
            planned_changes.extend(self._normalize_disciplines(database_alias, apply_changes))
            planned_changes.extend(self._normalize_styles(database_alias, apply_changes))
            if not apply_changes:
                transaction.set_rollback(True, using=database_alias)

        if planned_changes:
            for message in planned_changes:
                self.stdout.write(message)
        else:
            self.stdout.write('No local discipline or weapon style names needed normalization.')

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f'Updated {database_name}.'))
        else:
            self.stdout.write(self.style.WARNING('Dry run only. Re-run with --apply to write these changes.'))

    def _normalize_disciplines(self, database_alias, apply_changes):
        messages = []
        for old_name, new_name in DISCIPLINE_RENAMES.items():
            old_discipline = Discipline.objects.using(database_alias).filter(name=old_name).first()
            if not old_discipline:
                continue

            new_discipline = Discipline.objects.using(database_alias).filter(name=new_name).first()
            if new_discipline and new_discipline.pk != old_discipline.pk:
                self._merge_discipline(database_alias, old_discipline, new_discipline, apply_changes)
                messages.append(f'Merge discipline {old_name!r} into {new_name!r}.')
            else:
                if apply_changes:
                    old_discipline.name = new_name
                    old_discipline.save(update_fields=['name'])
                messages.append(f'Rename discipline {old_name!r} to {new_name!r}.')
        return messages

    def _normalize_styles(self, database_alias, apply_changes):
        messages = []
        for discipline_name, renames in STYLE_RENAMES.items():
            discipline = Discipline.objects.using(database_alias).filter(name=discipline_name).first()
            if not discipline:
                continue
            for old_name, new_name in renames.items():
                old_style = WeaponStyle.objects.using(database_alias).filter(
                    discipline=discipline,
                    name=old_name,
                ).first()
                if not old_style:
                    continue

                new_style = WeaponStyle.objects.using(database_alias).filter(
                    discipline=discipline,
                    name=new_name,
                ).first()
                if new_style and new_style.pk != old_style.pk:
                    self._merge_style(database_alias, old_style, new_style, apply_changes)
                    messages.append(
                        f'Merge {discipline_name} style {old_name!r} into {new_name!r}.'
                    )
                else:
                    if apply_changes:
                        old_style.name = new_name
                        old_style.save(update_fields=['name'])
                    messages.append(
                        f'Rename {discipline_name} style {old_name!r} to {new_name!r}.'
                    )
        return messages

    def _merge_discipline(self, database_alias, old_discipline, new_discipline, apply_changes):
        old_styles = WeaponStyle.objects.using(database_alias).filter(discipline=old_discipline)
        for old_style in old_styles:
            matching_new_style = WeaponStyle.objects.using(database_alias).filter(
                discipline=new_discipline,
                name=old_style.name,
            ).first()
            if matching_new_style:
                self._merge_style(database_alias, old_style, matching_new_style, apply_changes)
            elif apply_changes:
                old_style.discipline = new_discipline
                old_style.save(update_fields=['discipline'])
        if apply_changes:
            old_discipline.delete()

    def _merge_style(self, database_alias, old_style, new_style, apply_changes):
        conflicting_people = set(
            Authorization.objects.using(database_alias)
            .filter(style=old_style, person_id__in=Authorization.objects.using(database_alias).filter(
                style=new_style,
            ).values('person_id'))
            .values_list('person_id', flat=True)
        )
        if conflicting_people:
            raise CommandError(
                f'Cannot merge {old_style.discipline.name} {old_style.name!r} into {new_style.name!r}: '
                f'{len(conflicting_people)} person(s) already have both styles.'
            )

        if not apply_changes:
            return

        Authorization.objects.using(database_alias).filter(style=old_style).update(style=new_style)
        LegacyAuthorizationRecoveryEntry.objects.using(database_alias).filter(style=old_style).update(style=new_style)
        Sanction.objects.using(database_alias).filter(style=old_style).update(style=new_style)
        old_style.delete()
