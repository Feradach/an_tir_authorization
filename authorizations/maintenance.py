from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from .models import AuthorizationPortalSetting

DEFAULT_MAINTENANCE_LOCK_MESSAGE = (
    'The authorization portal is temporarily locked for maintenance. Please try again shortly.'
)


def get_portal_setting(create=False):
    try:
        if create:
            setting, _ = AuthorizationPortalSetting.objects.get_or_create(pk=1)
        else:
            setting = AuthorizationPortalSetting.objects.filter(pk=1).first()
    except (OperationalError, ProgrammingError):
        return None
    return setting


def maintenance_lock_enabled():
    setting = get_portal_setting()
    return bool(setting and setting.maintenance_lock_enabled)


def maintenance_lock_message():
    setting = get_portal_setting()
    if not setting:
        return DEFAULT_MAINTENANCE_LOCK_MESSAGE
    return setting.maintenance_lock_message or DEFAULT_MAINTENANCE_LOCK_MESSAGE


def can_manage_maintenance_lock(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return bool(getattr(user, 'is_superuser', False))


def active_logged_in_users():
    """Return users with unexpired database sessions.

    This is an estimate: Django sessions expire by age, not when someone closes
    a browser tab.
    """
    user_ids = set()
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        data = session.get_decoded()
        user_id = data.get('_auth_user_id')
        if user_id:
            user_ids.add(user_id)

    if not user_ids:
        return []

    User = get_user_model()
    return list(
        User.objects.filter(id__in=user_ids)
        .select_related('person')
        .order_by('person__sca_name', 'username')
    )
