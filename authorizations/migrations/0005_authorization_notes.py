from django.db import migrations, models
import django.db.models.deletion


def add_rejected_status(apps, schema_editor):
    AuthorizationStatus = apps.get_model('authorizations', 'AuthorizationStatus')
    AuthorizationStatus.objects.get_or_create(name='Rejected')


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0004_alter_user_membership'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuthorizationNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('marshal_proposed', 'Marshal proposed'), ('marshal_concurred', 'Marshal concurred'), ('marshal_approved', 'Marshal approved'), ('marshal_rejected', 'Marshal rejected'), ('sanction_issued', 'Sanction issued'), ('sanction_lifted', 'Sanction lifted')], max_length=50)),
                ('note', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('authorization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notes', to='authorizations.authorization')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='authorization_notes_created', to='authorizations.user')),
            ],
            options={
                'verbose_name': 'authorization note',
                'verbose_name_plural': 'authorization notes',
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(add_rejected_status, migrations.RunPython.noop),
    ]
