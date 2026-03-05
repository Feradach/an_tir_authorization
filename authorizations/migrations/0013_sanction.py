from dateutil.relativedelta import relativedelta
from django.db import migrations, models


def migrate_revoked_authorizations_to_sanctions(apps, schema_editor):
    Authorization = apps.get_model('authorizations', 'Authorization')
    AuthorizationStatus = apps.get_model('authorizations', 'AuthorizationStatus')
    Sanction = apps.get_model('authorizations', 'Sanction')

    revoked_status = AuthorizationStatus.objects.filter(name='Revoked').first()
    active_status = AuthorizationStatus.objects.filter(name='Active').first()
    if not revoked_status:
        return

    for authorization in Authorization.objects.filter(status_id=revoked_status.id).select_related('style__discipline'):
        if not authorization.style_id:
            continue
        sanction, created = Sanction.objects.get_or_create(
            person_id=authorization.person_id,
            discipline_id=authorization.style.discipline_id,
            style_id=authorization.style_id,
            lifted_at__isnull=True,
            defaults={
                'start_date': authorization.expiration,
                'end_date': authorization.expiration + relativedelta(years=1),
                'issue_note': 'Migrated from legacy revoked authorization record.',
            },
        )
        if not created:
            sanction.start_date = min(sanction.start_date, authorization.expiration)
            sanction.end_date = max(sanction.end_date, authorization.expiration + relativedelta(years=1))
            sanction.save(update_fields=['start_date', 'end_date'])

        if active_status:
            authorization.status = active_status
            authorization.save(update_fields=['status'])


def reverse_migrate_sanctions_to_revoked_authorizations(apps, schema_editor):
    Authorization = apps.get_model('authorizations', 'Authorization')
    AuthorizationStatus = apps.get_model('authorizations', 'AuthorizationStatus')
    Sanction = apps.get_model('authorizations', 'Sanction')

    revoked_status = AuthorizationStatus.objects.filter(name='Revoked').first()
    if not revoked_status:
        return

    for sanction in Sanction.objects.filter(style__isnull=False):
        authorization = Authorization.objects.filter(
            person_id=sanction.person_id,
            style_id=sanction.style_id,
        ).first()
        if authorization:
            authorization.status = revoked_status
            authorization.expiration = sanction.start_date
            authorization.save(update_fields=['status', 'expiration'])

    Sanction.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0012_authorizationnote_office'),
    ]

    operations = [
        migrations.CreateModel(
            name='Sanction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField(db_index=True)),
                ('end_date', models.DateField(db_index=True)),
                ('issue_note', models.TextField()),
                ('lifted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('lift_note', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='sanctions_created', to='authorizations.user')),
                ('discipline', models.ForeignKey(on_delete=models.deletion.CASCADE, to='authorizations.discipline')),
                ('issued_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='sanctions_issued', to='authorizations.user')),
                ('lifted_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='sanctions_lifted', to='authorizations.user')),
                ('person', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='sanctions', to='authorizations.person')),
                ('style', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, to='authorizations.weaponstyle')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='sanctions_updated', to='authorizations.user')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['person', 'discipline', 'style'], name='authorizati_person__47768_idx'),
                    models.Index(fields=['start_date', 'end_date', 'lifted_at'], name='authorizati_start_d_51bfb8_idx'),
                ],
            },
        ),
        migrations.RunPython(
            migrate_revoked_authorizations_to_sanctions,
            reverse_code=reverse_migrate_sanctions_to_revoked_authorizations,
        ),
    ]
