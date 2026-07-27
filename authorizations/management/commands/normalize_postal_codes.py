from collections import Counter

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.addressing import normalize_postal_code
from authorizations.models import User


class Command(BaseCommand):
    help = "Normalize safely repairable postal-code capitalization and spacing; dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the reported normalization changes.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        changes = []
        invalid = []

        for user in User.objects.order_by("id"):
            current = str(user.postal_code or "")
            if not current.strip():
                continue
            try:
                normalized = normalize_postal_code(current)
            except ValidationError:
                invalid.append((user.id, current))
                continue
            if current != normalized:
                changes.append((user.id, current, normalized))

        self.stdout.write(
            f"Postal-code normalization ({'APPLY' if apply_changes else 'DRY RUN'})"
        )
        for user_id, current, normalized in changes:
            self.stdout.write(
                f'user_id={user_id}: "{current}" -> "{normalized}"'
            )
        for user_id, current in invalid:
            self.stdout.write(
                f'user_id={user_id}: invalid and unchanged "{current}"'
            )

        if apply_changes:
            with transaction.atomic():
                for user_id, current, normalized in changes:
                    user = User.objects.select_for_update().get(id=user_id)
                    if str(user.postal_code or "") != current:
                        raise CommandError(
                            f"user_id {user_id} changed after review; transaction rolled back."
                        )
                    user.postal_code = normalized
                    user.save(update_fields=["postal_code"])

        counts = Counter(
            changed=len(changes),
            invalid=len(invalid),
        )
        self.stdout.write("")
        self.stdout.write(f"Codes to normalize: {counts['changed']}")
        self.stdout.write(f"Invalid codes left unchanged: {counts['invalid']}")
        self.stdout.write(
            f"Database rows changed: {counts['changed'] if apply_changes else 0}"
        )
        if not apply_changes:
            self.stdout.write("No database changes were applied. Re-run with --apply after review.")
