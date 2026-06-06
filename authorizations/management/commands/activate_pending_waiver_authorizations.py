from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.models import Authorization, AuthorizationStatus, sync_authorization_validity_interval
from authorizations.permissions import _JUNIOR_GROUND_CREW_STYLES, _SENIOR_GROUND_CREW_STYLES


class Command(BaseCommand):
    help = "Activate Awaiting Waiver authorizations for fighters who already have a current waiver expiration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this flag, only report matching records.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        active_status = self._status_by_name("Active")
        pending_authorizations = list(self._pending_authorizations_with_current_waiver())

        if not pending_authorizations:
            self.stdout.write("No Awaiting Waiver authorizations were found for fighters with a current waiver.")
            return

        self.stdout.write(
            f"Found {len(pending_authorizations)} Awaiting Waiver authorization(s) "
            "for fighters with a current waiver."
        )
        for authorization in pending_authorizations:
            user = authorization.person.user
            self.stdout.write(
                f"- authorization_id={authorization.id} person_id={authorization.person_id} "
                f"user_id={user.id}: {authorization.person.sca_name} / "
                f"{authorization.style.discipline.name} / {authorization.style.name} / "
                f"authorization expires {authorization.expiration} / waiver expires {user.waiver_expiration}"
            )

        if not apply_changes:
            self.stdout.write("Dry run only. Re-run with --apply to mark these authorizations Active.")
            return

        with transaction.atomic():
            authorization_ids = [authorization.id for authorization in pending_authorizations]
            Authorization.objects.filter(id__in=authorization_ids).update(status=active_status)
            for authorization in pending_authorizations:
                authorization.status = active_status
                sync_authorization_validity_interval(
                    authorization,
                    note="Generated when Awaiting Waiver authorization became active by repair command.",
                )
            senior_ground_crew_user_ids = {
                authorization.person.user_id
                for authorization in pending_authorizations
                if (
                    authorization.style.discipline.name == "Equestrian"
                    and authorization.style.name in _SENIOR_GROUND_CREW_STYLES
                )
            }
            if senior_ground_crew_user_ids:
                inactive_status = self._get_or_create_status("Inactive")
                Authorization.objects.filter(
                    person__user_id__in=senior_ground_crew_user_ids,
                    style__discipline__name="Equestrian",
                    style__name__in=_JUNIOR_GROUND_CREW_STYLES,
                ).update(status=inactive_status)

        self.stdout.write(
            self.style.SUCCESS(f"Marked {len(pending_authorizations)} authorization(s) Active.")
        )

    def _status_by_name(self, name):
        status = AuthorizationStatus.objects.filter(name=name).order_by("id").first()
        if not status:
            raise CommandError(f'Required authorization status "{name}" was not found.')
        return status

    def _get_or_create_status(self, name):
        status = AuthorizationStatus.objects.filter(name=name).order_by("id").first()
        if status:
            return status
        return AuthorizationStatus.objects.create(name=name)

    def _pending_authorizations_with_current_waiver(self):
        return (
            Authorization.objects.select_related("person__user", "style__discipline", "status")
            .filter(
                status__name="Awaiting Waiver",
                person__user__waiver_expiration__gt=date.today(),
            )
            .order_by("person__sca_name", "style__discipline__name", "style__name", "id")
        )
