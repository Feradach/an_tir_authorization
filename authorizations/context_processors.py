from django.conf import settings


def feature_flags(request):
    return {
        'TEST_FEATURES_ENABLED': getattr(settings, 'AUTHZ_TEST_FEATURES', False),
        'AUTHZ_REQUIRE_FIGHTER_CONCURRENCE': getattr(settings, 'AUTHZ_REQUIRE_FIGHTER_CONCURRENCE', False),
    }

