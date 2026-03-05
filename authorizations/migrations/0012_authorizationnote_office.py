from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0011_reportingperiod_reportvalue'),
    ]

    operations = [
        migrations.AddField(
            model_name='authorizationnote',
            name='office',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
