from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0032_alter_user_postal_code'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='person',
            name='is_minor',
        ),
    ]
