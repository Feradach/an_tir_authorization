from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0024_authorizationportalsetting_maintenance_lock'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='parent_first_name',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='person',
            name='parent_last_name',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='person',
            name='parent_sca_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
