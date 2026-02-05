from django.contrib.auth.signals import user_login_failed
from django.dispatch import receiver

from .events import log_security_event


@receiver(user_login_failed)
def capture_failed_login(sender, credentials, request, **kwargs):

    username = credentials.get("username", "unknown")

    ip = None
    user_agent = None

    if request:
        ip = (
            request.META.get("HTTP_CF_CONNECTING_IP")
            or request.META.get("REMOTE_ADDR")
        )
        user_agent = request.META.get("HTTP_USER_AGENT")

    log_security_event(
        "auth_failed",
        username=username,
        ip=ip,
        user_agent=user_agent,
        component="django_auth",
    )
