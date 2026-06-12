from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0037_authorizationvalidityinterval'),
    ]

    operations = [
        migrations.AlterField(
            model_name='authorizationnote',
            name='action',
            field=models.CharField(
                choices=[
                    ('marshal_proposed', 'Marshal proposed'),
                    ('marshal_concurred', 'Marshal concurred'),
                    ('marshal_approved', 'Marshal approved'),
                    ('marshal_rejected', 'Marshal rejected'),
                    ('officer_deleted', 'Officer deleted'),
                    ('sanction_issued', 'Sanction issued'),
                    ('sanction_lifted', 'Sanction lifted'),
                ],
                max_length=50,
            ),
        ),
    ]
