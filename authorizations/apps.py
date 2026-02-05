from django.apps import AppConfig


class AuthConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'authorizations'

    def ready(self):
        import authorizations.security.signals
