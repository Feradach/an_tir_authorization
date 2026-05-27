from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db import migrations, models
from django.db.models import Max, Q


LEGACY_IMPORT_RECORDED_AT = datetime(2026, 5, 6, 21, 36, tzinfo=ZoneInfo('America/Los_Angeles'))
LEGACY_IMPORT_SOURCE = 'legacy_database_import'


def backfill_legacy_waiver_records(apps, schema_editor):
    User = apps.get_model('authorizations', 'User')
    Person = apps.get_model('authorizations', 'Person')
    Authorization = apps.get_model('authorizations', 'Authorization')
    WaiverRecord = apps.get_model('authorizations', 'WaiverRecord')

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

    people_by_user_id = {
        person.user_id: person
        for person in Person.objects.filter(user_id__in=latest_by_user_id.keys()).select_related('parent__user')
    }
    users = User.objects.filter(
        Q(waiver_expiration__isnull=True) | Q(waiver_expiration__lt=today),
        id__in=latest_by_user_id.keys(),
    )
    existing_record_user_ids = set(
        WaiverRecord.objects.filter(
            covered_user_id__in=latest_by_user_id.keys(),
            source=LEGACY_IMPORT_SOURCE,
        ).values_list('covered_user_id', flat=True)
    )

    records = []
    for user in users:
        latest_expiration = latest_by_user_id.get(user.id)
        if not latest_expiration:
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

        user.waiver_expiration = latest_expiration
        user.save(update_fields=['waiver_expiration'])

        if user.id in existing_record_user_ids:
            continue

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

    if records:
        WaiverRecord.objects.bulk_create(records, batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0029_waiverrecord'),
    ]

    operations = [
        migrations.AlterField(
            model_name='waiverrecord',
            name='source',
            field=models.CharField(
                choices=[
                    ('portal_adult_signature', 'Portal Adult Signature'),
                    ('portal_minor_signature', 'Portal Minor Signature'),
                    ('membership_roster', 'Membership Roster'),
                    ('paper_waiver', 'Paper Waiver'),
                    ('legacy_database_import', 'Legacy Database Import'),
                ],
                db_index=True,
                max_length=50,
            ),
        ),
        migrations.RunPython(backfill_legacy_waiver_records, migrations.RunPython.noop),
    ]
