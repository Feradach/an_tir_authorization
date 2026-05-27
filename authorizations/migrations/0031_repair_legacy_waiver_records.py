from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db import migrations
from django.db.models import Max


LEGACY_IMPORT_RECORDED_AT = datetime(2026, 5, 6, 21, 36, tzinfo=ZoneInfo('America/Los_Angeles'))
LEGACY_IMPORT_SOURCE = 'legacy_database_import'


def repair_legacy_waiver_records(apps, schema_editor):
    User = apps.get_model('authorizations', 'User')
    Person = apps.get_model('authorizations', 'Person')
    Authorization = apps.get_model('authorizations', 'Authorization')
    WaiverRecord = apps.get_model('authorizations', 'WaiverRecord')
    UserNote = apps.get_model('authorizations', 'UserNote')
    MembershipRosterEntry = apps.get_model('authorizations', 'MembershipRosterEntry')

    today = date.today()
    active_expirations = (
        Authorization.objects.filter(
            status__name='Active',
            expiration__gte=today,
        )
        .values('person__user_id')
        .annotate(latest_expiration=Max('expiration'))
    )
    latest_by_user_id = {
        row['person__user_id']: row['latest_expiration']
        for row in active_expirations
        if row['latest_expiration']
    }
    if not latest_by_user_id:
        return

    existing_record_user_ids = set(
        WaiverRecord.objects.filter(
            covered_user_id__in=latest_by_user_id.keys(),
            source=LEGACY_IMPORT_SOURCE,
        ).values_list('covered_user_id', flat=True)
    )
    people_by_user_id = {
        person.user_id: person
        for person in Person.objects.filter(user_id__in=latest_by_user_id.keys()).select_related('parent__user')
    }
    users = list(User.objects.filter(id__in=latest_by_user_id.keys()))

    records = []
    notes = []
    users_to_update = []
    for user in users:
        latest_expiration = latest_by_user_id.get(user.id)
        if not latest_expiration or user.id in existing_record_user_ids:
            continue

        if user.membership and user.membership_expiration and MembershipRosterEntry.objects.filter(
            membership_number=user.membership,
            membership_expiration=user.membership_expiration,
            has_society_waiver=True,
        ).exists():
            continue

        person = people_by_user_id.get(user.id)
        parent_first = ''
        parent_last = ''
        parent_sca = ''
        covered_sca = ''
        waiver_type = 'adult'
        if person:
            covered_sca = person.sca_name or ''
            waiver_type = 'minor' if person.is_minor else 'adult'
            if person.parent_id:
                parent_first = person.parent.user.first_name or ''
                parent_last = person.parent.user.last_name or ''
                parent_sca = person.parent.sca_name or ''
            else:
                parent_first = person.parent_first_name or ''
                parent_last = person.parent_last_name or ''
                parent_sca = person.parent_sca_name or ''

        if not user.waiver_expiration or user.waiver_expiration < latest_expiration:
            user.waiver_expiration = latest_expiration
            users_to_update.append(user)

        records.append(WaiverRecord(
            covered_user_id=user.id,
            source=LEGACY_IMPORT_SOURCE,
            waiver_type=waiver_type,
            signer_relationship='legacy_database_import',
            covered_first_name_snapshot=user.first_name or '',
            covered_last_name_snapshot=user.last_name or '',
            covered_sca_name_snapshot=covered_sca,
            parent_first_name_snapshot=parent_first,
            parent_last_name_snapshot=parent_last,
            parent_sca_name_snapshot=parent_sca,
            waiver_version='legacy-database-import',
            note='Waiver coverage backfilled from the legacy authorization database during the 2026-05-06 migration.',
            resulting_waiver_expiration=latest_expiration,
            recorded_at=LEGACY_IMPORT_RECORDED_AT,
        ))
        if person:
            notes.append(UserNote(
                person_id=person.user_id,
                note=(
                    'Waiver coverage backfilled from the legacy authorization database during the '
                    f'2026-05-06 migration. Waiver expiration set to {latest_expiration.isoformat()}.'
                ),
            ))

    if users_to_update:
        User.objects.bulk_update(users_to_update, ['waiver_expiration'], batch_size=1000)
    if records:
        WaiverRecord.objects.bulk_create(records, batch_size=1000)
        WaiverRecord.objects.filter(
            source=LEGACY_IMPORT_SOURCE,
            note__contains='2026-05-06 migration',
        ).update(recorded_at=LEGACY_IMPORT_RECORDED_AT)

    legacy_records = WaiverRecord.objects.filter(
        source=LEGACY_IMPORT_SOURCE,
        note__contains='2026-05-06 migration',
    ).select_related('covered_user')
    existing_note_user_ids = set(
        UserNote.objects.filter(
            person_id__in=[record.covered_user_id for record in legacy_records],
            note__contains='Waiver coverage backfilled from the legacy authorization database',
        ).values_list('person_id', flat=True)
    )
    pending_note_user_ids = {
        note.person_id
        for note in notes
    }
    for record in legacy_records:
        if record.covered_user_id in existing_note_user_ids or record.covered_user_id in pending_note_user_ids:
            continue
        if record.covered_user_id not in people_by_user_id:
            continue
        if not record.resulting_waiver_expiration:
            continue
        notes.append(UserNote(
            person_id=record.covered_user_id,
            note=(
                'Waiver coverage backfilled from the legacy authorization database during the '
                f'2026-05-06 migration. Waiver expiration set to {record.resulting_waiver_expiration.isoformat()}.'
            ),
        ))
        pending_note_user_ids.add(record.covered_user_id)
    if notes:
        UserNote.objects.bulk_create(notes, batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0030_backfill_legacy_waiver_records'),
    ]

    operations = [
        migrations.RunPython(repair_legacy_waiver_records, migrations.RunPython.noop),
    ]
