from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0023_legacy_recovery_marshal_promotion'),
    ]

    operations = [
        migrations.AddField(
            model_name='authorizationportalsetting',
            name='maintenance_lock_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='authorizationportalsetting',
            name='maintenance_lock_message',
            field=models.CharField(
                blank=True,
                default='The authorization portal is temporarily locked for maintenance. Please try again shortly.',
                max_length=255,
            ),
        ),
    ]
