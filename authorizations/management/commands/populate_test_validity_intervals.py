from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.models import Authorization, AuthorizationValidityInterval
from authorizations.permissions import YOUTH_DISCIPLINE_NAMES


class Command(BaseCommand):
    help = (
        "Populate authorization validity intervals for local/staging test data "
        "where no legacy history is available."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--write",
            action="store_true",
            help="Persist intervals. Without this flag, only report what would be created.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing validity intervals before writing the local/staging test-data intervals.",
        )

    def handle(self, *args, **options):
        write = options["write"]
        replace = options["replace"]

        existing_count = AuthorizationValidityInterval.objects.count()
        if existing_count and write and not replace:
            raise CommandError(
                f"Found {existing_count} existing authorization validity interval(s). "
                "Re-run with --replace if this local/staging test-data population should replace them."
            )

        authorizations = (
            Authorization.objects.select_related("person__user", "style__discipline")
            .order_by("id")
        )
        (
            intervals,
            skipped_missing_expiration,
            skipped_missing_style,
            skipped_effective_before_start,
        ) = self._build_intervals(authorizations)

        self.stdout.write(f"Authorizations considered: {authorizations.count()}")
        self.stdout.write(f"Existing validity intervals: {existing_count}")
        generated_label = "prepared" if write else "generated from current authorizations"
        self.stdout.write(f"Intervals {generated_label}: {len(intervals)}")
        self.stdout.write(f"2-year intervals: {sum(1 for interval in intervals if interval['years'] == 2)}")
        self.stdout.write(f"4-year intervals: {sum(1 for interval in intervals if interval['years'] == 4)}")
        self.stdout.write(f"Skipped missing expiration: {skipped_missing_expiration}")
        self.stdout.write(f"Skipped missing style/discipline: {skipped_missing_style}")
        self.stdout.write(f"Skipped effective expiration before calculated start: {skipped_effective_before_start}")

        if not write:
            self.stdout.write("Dry run only. Re-run with --write to create these intervals.")
            return

        with transaction.atomic():
            if replace:
                AuthorizationValidityInterval.objects.all().delete()

            AuthorizationValidityInterval.objects.bulk_create(
                [
                    AuthorizationValidityInterval(
                        authorization_id=interval["authorization_id"],
                        start_date=interval["start_date"],
                        end_date=interval["end_date"],
                        source="manual_repair",
                        note=(
                            "Generated for local/staging test data population; "
                            "no legacy history was available."
                        ),
                    )
                    for interval in intervals
                ]
            )

        replaced_text = " after replacing existing intervals" if replace and existing_count else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(intervals)} authorization validity interval(s){replaced_text}."
            )
        )

    def _build_intervals(self, authorizations):
        intervals = []
        skipped_missing_expiration = 0
        skipped_missing_style = 0
        skipped_effective_before_start = 0

        for authorization in authorizations:
            if not authorization.expiration:
                skipped_missing_expiration += 1
                continue
            if not authorization.style or not authorization.style.discipline:
                skipped_missing_style += 1
                continue

            years = self._duration_years(authorization)
            start_date = authorization.expiration - relativedelta(years=years)
            end_date = authorization.effective_expiration
            if end_date < start_date:
                skipped_effective_before_start += 1
                continue

            intervals.append(
                {
                    "authorization_id": authorization.id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "years": years,
                }
            )

        return (
            intervals,
            skipped_missing_expiration,
            skipped_missing_style,
            skipped_effective_before_start,
        )

    def _duration_years(self, authorization):
        discipline_name = authorization.style.discipline.name
        if discipline_name in YOUTH_DISCIPLINE_NAMES:
            return 2
        if authorization.person and authorization.person.is_current_minor:
            return 2
        return 4
