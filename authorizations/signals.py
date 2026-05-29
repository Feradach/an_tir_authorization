from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Authorization, AuthorizationAuditEntry


TRACKED_AUTHORIZATION_FIELDS = [
    'person_id',
    'style_id',
    'status_id',
    'expiration',
    'marshal_id',
    'concurring_fighter_id',
    'created_by_id',
    'updated_by_id',
]


def _snapshot_authorization(authorization):
    return {field: getattr(authorization, field) for field in TRACKED_AUTHORIZATION_FIELDS}


def _authorization_audit_event_type(before, after, created, authorization):
    if created:
        return 'created'
    changed_fields = [
        field for field in TRACKED_AUTHORIZATION_FIELDS
        if before.get(field) != after.get(field)
    ]
    if 'status_id' in changed_fields:
        status_name = authorization.status.name if authorization.status else ''
        if status_name == 'Rejected':
            return 'rejected'
        if status_name == 'Active':
            return 'approved'
        return 'status_changed'
    if 'expiration' in changed_fields:
        return 'renewed'
    if 'marshal_id' in changed_fields or 'concurring_fighter_id' in changed_fields:
        return 'marshal_changed'
    return 'updated'


def _authorization_audit_summary(event_type, before, after, authorization):
    parts = []
    if before and before.get('status_id') != after.get('status_id'):
        before_status = before.get('status_id') or '-'
        after_status = after.get('status_id') or '-'
        parts.append(f'status {before_status} -> {after_status}')
    if before and before.get('expiration') != after.get('expiration'):
        parts.append(f'expiration {before.get("expiration") or "-"} -> {after.get("expiration") or "-"}')
    if before and before.get('marshal_id') != after.get('marshal_id'):
        parts.append(f'marshal {before.get("marshal_id") or "-"} -> {after.get("marshal_id") or "-"}')
    if not parts:
        parts.append(event_type.replace('_', ' '))
    return '; '.join(parts)[:255]


@receiver(pre_save, sender=Authorization)
def cache_authorization_audit_before(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk:
        instance._authorization_audit_before = None
        return
    instance._authorization_audit_before = (
        sender.objects
        .filter(pk=instance.pk)
        .values(*TRACKED_AUTHORIZATION_FIELDS)
        .first()
    )


@receiver(post_save, sender=Authorization)
def create_authorization_audit_entry(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    before = getattr(instance, '_authorization_audit_before', None)
    after = _snapshot_authorization(instance)
    changed_fields = [
        field for field in TRACKED_AUTHORIZATION_FIELDS
        if created or (before and before.get(field) != after.get(field))
    ]
    if not changed_fields:
        return

    event_type = _authorization_audit_event_type(before or {}, after, created, instance)
    changed_by_id = after.get('updated_by_id') or after.get('created_by_id')
    person_id = after.get('person_id') or (before or {}).get('person_id')
    style_id = after.get('style_id') or (before or {}).get('style_id')

    AuthorizationAuditEntry.objects.create(
        authorization=instance,
        person_id=person_id,
        style_id=style_id,
        event_type=event_type,
        changed_by_id=changed_by_id,
        summary=_authorization_audit_summary(event_type, before, after, instance),
        changed_fields=changed_fields,
        before_person_id=(before or {}).get('person_id'),
        after_person_id=after.get('person_id'),
        before_style_id=(before or {}).get('style_id'),
        after_style_id=after.get('style_id'),
        before_status_id=(before or {}).get('status_id'),
        after_status_id=after.get('status_id'),
        before_expiration=(before or {}).get('expiration'),
        after_expiration=after.get('expiration'),
        before_marshal_id=(before or {}).get('marshal_id'),
        after_marshal_id=after.get('marshal_id'),
        before_concurring_fighter_id=(before or {}).get('concurring_fighter_id'),
        after_concurring_fighter_id=after.get('concurring_fighter_id'),
        before_created_by_id=(before or {}).get('created_by_id'),
        after_created_by_id=after.get('created_by_id'),
        before_updated_by_id=(before or {}).get('updated_by_id'),
        after_updated_by_id=after.get('updated_by_id'),
    )
