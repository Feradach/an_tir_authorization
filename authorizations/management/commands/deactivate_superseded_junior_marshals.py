from django.core.management.base import BaseCommand
from django.db import transaction

from authorizations.models import Authorization, AuthorizationStatus


class Command(BaseCommand):
    help = "Mark active Junior Marshal authorizations inactive when an active Senior Marshal exists in the same discipline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this flag, only report matching records.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        inactive_status = self._inactive_status() if apply_changes else None
        junior_authorizations = list(self._superseded_junior_authorizations())

        if not junior_authorizations:
            self.stdout.write("No active Junior Marshal authorizations are superseded by active Senior Marshal authorizations.")
            return

        self.stdout.write(
            f"Found {len(junior_authorizations)} active Junior Marshal authorization(s) "
            "with an active Senior Marshal in the same discipline."
        )

        for authorization in junior_authorizations:
            self.stdout.write(
                f"- authorization_id={authorization.id} user_id={authorization.person_id}: "
                f"{authorization.person.sca_name} / "
                f"{authorization.style.discipline.name} / expires {authorization.effective_expiration}"
            )

        if not apply_changes:
            self.stdout.write("Dry run only. Re-run with --apply to mark these authorizations Inactive.")
            return

        with transaction.atomic():
            for authorization in junior_authorizations:
                authorization.status = inactive_status
                authorization.save(update_fields=["status", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"Marked {len(junior_authorizations)} Junior Marshal authorization(s) Inactive."))

    def _inactive_status(self):
        status = AuthorizationStatus.objects.filter(name="Inactive").order_by("id").first()
        if status:
            return status
        return AuthorizationStatus.objects.create(name="Inactive")

    def _superseded_junior_authorizations(self):
        active_senior_keys = set(
            Authorization.objects.effectively_active()
            .filter(style__name="Senior Marshal")
            .values_list("person_id", "style__discipline_id")
        )
        if not active_senior_keys:
            return Authorization.objects.none()

        junior_authorizations = (
            Authorization.objects.effectively_active()
            .select_related("person", "style__discipline")
            .filter(style__name="Junior Marshal")
            .order_by("person__sca_name", "style__discipline__name", "id")
        )
        superseded_ids = [
            authorization.id
            for authorization in junior_authorizations
            if (authorization.person_id, authorization.style.discipline_id) in active_senior_keys
        ]
        return (
            Authorization.objects.with_effective_expiration()
            .select_related("person", "style__discipline")
            .filter(id__in=superseded_ids)
            .order_by("person__sca_name", "style__discipline__name", "id")
        )
