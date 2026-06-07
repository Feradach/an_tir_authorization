from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.models import Authorization, AuthorizationStatus
from authorizations.permissions import (
    KINGDOM_APPROVAL_STATUS,
    KINGDOM_EQUESTRIAN_WAIVER_STATUS,
    authorization_officer_sign_off_enabled,
    kingdom_review_status_name_for_style,
)


class Command(BaseCommand):
    help = (
        "Advance non-marshal authorizations out of Awaiting Fighter Concurrence "
        "after the fighter concurrence requirement has been disabled."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--write",
            action="store_true",
            help="Persist changes. Without this flag, only report planned updates.",
        )

    def handle(self, *args, **options):
        write = options["write"]
        needs_concurrence_status = self._status_by_name("Awaiting Fighter Concurrence")
        active_status = self._status_by_name("Active")
        pending_waiver_status = self._status_by_name("Awaiting Waiver")
        needs_kingdom_status = self._status_by_name(KINGDOM_APPROVAL_STATUS)
        needs_kingdom_equestrian_waiver_status = self._get_or_create_status(
            KINGDOM_EQUESTRIAN_WAIVER_STATUS,
            write=write,
        )
        sign_off_required = authorization_officer_sign_off_enabled()

        pending_authorizations = list(
            Authorization.objects.select_related(
                "person__user",
                "style__discipline",
                "status",
                "concurring_fighter",
            )
            .filter(status=needs_concurrence_status)
            .order_by("person__sca_name", "style__discipline__name", "style__name", "id")
        )

        planned = []
        skipped = []
        for authorization in pending_authorizations:
            target_status, skip_reason = self._target_status(
                authorization,
                sign_off_required=sign_off_required,
                active_status=active_status,
                pending_waiver_status=pending_waiver_status,
                needs_kingdom_status=needs_kingdom_status,
                needs_kingdom_equestrian_waiver_status=needs_kingdom_equestrian_waiver_status,
            )
            if skip_reason:
                skipped.append((authorization, skip_reason))
            else:
                planned.append((authorization, target_status))

        self.stdout.write(
            f"Awaiting Fighter Concurrence authorizations found: {len(pending_authorizations)}"
        )
        self.stdout.write(f"Authorizations to advance: {len(planned)}")
        self.stdout.write(f"Authorizations skipped: {len(skipped)}")
        self.stdout.write(
            f"Authorization officer sign-off required: {'yes' if sign_off_required else 'no'}"
        )

        for authorization, target_status in planned:
            self.stdout.write(
                f"- advance authorization_id={authorization.id} "
                f"person_id={authorization.person_id} {authorization.person.sca_name} / "
                f"{authorization.style.discipline.name} - {authorization.style.name}: "
                f"{authorization.status.name} -> {target_status.name}"
            )

        for authorization, reason in skipped:
            style_label = (
                f"{authorization.style.discipline.name} - {authorization.style.name}"
                if authorization.style and authorization.style.discipline
                else "missing style"
            )
            self.stdout.write(
                f"- skip authorization_id={authorization.id} "
                f"person_id={authorization.person_id} {authorization.person.sca_name} / "
                f"{style_label}: {reason}"
            )

        if not write:
            self.stdout.write("Dry run only. Re-run with --write to advance these authorizations.")
            return

        with transaction.atomic():
            for authorization, target_status in planned:
                authorization.status = target_status
                authorization.concurring_fighter = None
                authorization.save(update_fields=["status", "concurring_fighter", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(f"Advanced {len(planned)} authorization(s).")
        )

    def _status_by_name(self, name):
        status = AuthorizationStatus.objects.filter(name=name).order_by("id").first()
        if not status:
            raise CommandError(f'Required authorization status "{name}" was not found.')
        return status

    def _get_or_create_status(self, name, *, write):
        status = AuthorizationStatus.objects.filter(name=name).order_by("id").first()
        if status:
            return status
        if not write:
            return AuthorizationStatus(name=name)
        return AuthorizationStatus.objects.create(name=name)

    def _target_status(
        self,
        authorization,
        *,
        sign_off_required,
        active_status,
        pending_waiver_status,
        needs_kingdom_status,
        needs_kingdom_equestrian_waiver_status,
    ):
        if not authorization.style or not authorization.style.discipline:
            return None, "missing style or discipline"
        if authorization.style.name in ["Junior Marshal", "Senior Marshal"]:
            return None, "marshal authorizations should not use fighter concurrence"

        review_status_name = kingdom_review_status_name_for_style(authorization.style)
        if sign_off_required or review_status_name == KINGDOM_EQUESTRIAN_WAIVER_STATUS:
            if review_status_name == KINGDOM_EQUESTRIAN_WAIVER_STATUS:
                return needs_kingdom_equestrian_waiver_status, ""
            return needs_kingdom_status, ""

        user = authorization.person.user
        if user.waiver_expiration and user.waiver_expiration > date.today():
            return active_status, ""
        return pending_waiver_status, ""
