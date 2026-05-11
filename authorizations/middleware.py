from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .maintenance import (
    can_manage_maintenance_lock,
    maintenance_lock_enabled,
    maintenance_lock_message,
)


class MaintenanceLockMiddleware:
    """Block write requests while the portal is locked for maintenance."""

    SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_block(request):
            messages.warning(request, maintenance_lock_message())
            return redirect('index')
        return self.get_response(request)

    def _should_block(self, request):
        if request.method in self.SAFE_METHODS:
            return False
        if not maintenance_lock_enabled():
            return False
        try:
            url_name = resolve(request.path_info).url_name
        except Resolver404:
            url_name = None
        if url_name in {'login', 'logout'}:
            return False
        if (
            url_name == 'index'
            and request.POST.get('action') == 'set_maintenance_lock'
            and can_manage_maintenance_lock(request.user)
        ):
            return False
        return True
