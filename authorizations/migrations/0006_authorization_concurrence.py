from django.db import migrations, models
import django.db.models.deletion


def add_needs_concurrence_status(apps, schema_editor):
    AuthorizationStatus = apps.get_model('authorizations', 'AuthorizationStatus')
    AuthorizationStatus.objects.get_or_create(name='Needs Concurrence')


def backfill_concurring_fighter(apps, schema_editor):
    Authorization = apps.get_model('authorizations', 'Authorization')
    Person = apps.get_model('authorizations', 'Person')
    try:
        admin_person = Person.objects.get(user_id=11968)
    except Person.DoesNotExist:
        return
    Authorization.objects.filter(concurring_fighter__isnull=True).update(concurring_fighter=admin_person)


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0005_authorization_notes'),
    ]

    operations = [
        migrations.AddField(
            model_name='authorization',
            name='concurring_fighter',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='concurrences',
                to='authorizations.person',
            ),
        ),
        migrations.RunPython(add_needs_concurrence_status, migrations.RunPython.noop),
        migrations.RunPython(backfill_concurring_fighter, migrations.RunPython.noop),
    ]
