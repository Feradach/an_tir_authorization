from django.db import migrations


def add_seneschal(apps, schema_editor):
    Discipline = apps.get_model('authorizations', 'Discipline')
    Discipline.objects.get_or_create(name='Seneschal')


def remove_seneschal(apps, schema_editor):
    Discipline = apps.get_model('authorizations', 'Discipline')
    Discipline.objects.filter(name='Seneschal').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0035_authorizationauditentry'),
    ]

    operations = [
        migrations.RunPython(add_seneschal, remove_seneschal),
    ]
