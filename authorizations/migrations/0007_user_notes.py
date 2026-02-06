from django.db import migrations, models
import django.db.models.deletion


def move_user_comments(apps, schema_editor):
    User = apps.get_model('authorizations', 'User')
    Person = apps.get_model('authorizations', 'Person')
    UserNote = apps.get_model('authorizations', 'UserNote')

    for user in User.objects.exclude(comment__isnull=True).exclude(comment__exact=''):
        try:
            person = Person.objects.get(user_id=user.id)
        except Person.DoesNotExist:
            continue
        UserNote.objects.create(
            person=person,
            created_by=None,
            note_type='officer_note',
            note=user.comment,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0006_authorization_concurrence'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note_type', models.CharField(choices=[('officer_note', 'Officer note')], default='officer_note', max_length=50)),
                ('note', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='user_notes_created', to='authorizations.user')),
                ('person', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_notes', to='authorizations.person')),
            ],
            options={
                'verbose_name': 'user note',
                'verbose_name_plural': 'user notes',
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(move_user_comments, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='user',
            name='comment',
        ),
    ]
