from django.db import migrations


def add_equestrian_authorization_officer(apps, schema_editor):
    Discipline = apps.get_model('authorizations', 'Discipline')
    Discipline.objects.get_or_create(name='Equestrian Authorization Officer')


def remove_equestrian_authorization_officer(apps, schema_editor):
    Discipline = apps.get_model('authorizations', 'Discipline')
    Discipline.objects.filter(name='Equestrian Authorization Officer').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0027_backfill_remaining_youth_category_styles'),
    ]

    operations = [
        migrations.RunPython(
            add_equestrian_authorization_officer,
            remove_equestrian_authorization_officer,
        ),
    ]
