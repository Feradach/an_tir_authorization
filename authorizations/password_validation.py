from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    UserAttributeSimilarityValidator,
)
from django.utils.translation import gettext as _


class AccountSimilarityValidator(UserAttributeSimilarityValidator):
    def get_help_text(self):
        return _(
            "Choose a password that differs substantially from your username, first name, "
            "last name, and email address. Changing capitalization or adding a year may "
            "still be too similar."
        )


class CommonPasswordListValidator(CommonPasswordValidator):
    def get_help_text(self):
        return _(
            "Choose a password that isn’t on the list of common passwords. "
            "For example, “password123” is blocked."
        )
