from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Authorization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('expiration', models.DateField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='authorizations_created', to='authorizations.user')),
                ('marshal', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='authorizations_marshaled', to='authorizations.person')),
                ('person', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='authorizations.person')),
                ('status', models.ForeignKey(default=1, null=True, on_delete=django.db.models.deletion.SET_NULL, to='authorizations.authorizationstatus')),
                ('style', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='authorizations.weaponstyle')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='authorizations_updated', to='authorizations.user')),
            ],
            options={
                'db_table': 'authorizations_authorization',
            },
        ),
        migrations.AddConstraint(
            model_name='authorization',
            constraint=models.UniqueConstraint(fields=('person', 'style'), name='unique_person_style'),
        ),
    ]
