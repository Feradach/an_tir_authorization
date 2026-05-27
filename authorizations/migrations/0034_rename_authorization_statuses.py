from django.db import migrations


STATUS_RENAMES = {
    'Pending Waiver': 'Awaiting Waiver',
    'Needs Concurrence': 'Awaiting Fighter Concurrence',
    'Pending': 'Awaiting Second Marshal Concurrence',
    'Needs Regional Approval': 'Awaiting Regional Marshal Approval',
    'Pending Background Check': 'Awaiting Background Check',
    'Needs Kingdom Equestrian Waiver': 'Awaiting Equestrian Authorization Officer Review',
    'Needs Kingdom Approval': 'Awaiting Kingdom Authorization Officer Review',
}


def rename_statuses_forward(apps, schema_editor):
    AuthorizationStatus = apps.get_model('authorizations', 'AuthorizationStatus')
    for old_name, new_name in STATUS_RENAMES.items():
        AuthorizationStatus.objects.filter(name=old_name).update(name=new_name)


def rename_statuses_backward(apps, schema_editor):
    AuthorizationStatus = apps.get_model('authorizations', 'AuthorizationStatus')
    for old_name, new_name in STATUS_RENAMES.items():
        AuthorizationStatus.objects.filter(name=new_name).update(name=old_name)


class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0033_remove_person_is_minor'),
    ]

    operations = [
        migrations.RunPython(rename_statuses_forward, rename_statuses_backward),
    ]
