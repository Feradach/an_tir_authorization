import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from authorizations.management.commands.populate_restore_validity_intervals import (
    Command as RestorePopulationCommand,
    IntervalCandidate,
)
from authorizations.models import Authorization, AuthorizationValidityInterval


@dataclass
class CatchUpPlan:
    authorization_id: int
    action: str
    existing_count: int
    merged_count: int
    note: str


class Command(BaseCommand):
    help = "Catch up authorization validity intervals for authorizations changed after a reviewed snapshot cutoff."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            help=(
                "Only consider authorizations whose authorization row or person user row changed at or after this "
                "timestamp. Accepts YYYY-MM-DD or ISO datetime."
            ),
        )
        parser.add_argument("--all", action="store_true", help="Consider all current authorizations.")
        parser.add_argument("--write", action="store_true", help="Persist catch-up changes. Dry-run is the default.")
        parser.add_argument(
            "--allow-empty",
            action="store_true",
            help="Allow running when the interval table is empty. Normally this indicates the vetted history was not imported yet.",
        )
        parser.add_argument(
            "--snapshot-date",
            default=None,
            help="Date to use for missing membership/background-check fallback. Defaults to today.",
        )
        parser.add_argument(
            "--output-dir",
            default=str(Path(settings.BASE_DIR) / "tmp" / "validity_interval_population"),
        )

    def handle(self, *args, **options):
        since = self._parse_since(options["since"])
        use_all = options["all"]
        write = options["write"]
        allow_empty = options["allow_empty"]
        snapshot_date = self._parse_snapshot_date(options["snapshot_date"])
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        if since and use_all:
            raise CommandError("Use either --since or --all, not both.")
        if not since and not use_all:
            raise CommandError("Provide --since for a scoped catch-up, or --all for an intentional full current-data reconciliation.")

        self._validate_interval_table()
        existing_total = AuthorizationValidityInterval.objects.count()
        if existing_total == 0 and not allow_empty:
            raise CommandError(
                "The validity interval table is empty. Import the vetted history first, or use --allow-empty intentionally."
            )

        authorizations = self._authorizations_since(since) if since else self._all_authorizations()
        helper = RestorePopulationCommand()
        plans = []
        merged_by_authorization = {}
        report_rows = []
        counts = Counter()

        for authorization in authorizations:
            candidate = helper._candidate_from_authorization(
                authorization_id=authorization.id,
                person=authorization.person,
                style=authorization.style,
                expiration=authorization.expiration,
                snapshot_date=snapshot_date,
                source="portal_authorization",
                note=(
                    f"Catch-up generated from current database on {snapshot_date.isoformat()}; "
                    "start uses stored authorization expiration and end uses effective expiration."
                ),
            )
            existing_intervals = list(
                AuthorizationValidityInterval.objects.filter(authorization=authorization).order_by("start_date", "end_date", "id")
            )

            if isinstance(candidate, str):
                counts[f"skipped_{candidate}"] += 1
                plans.append(CatchUpPlan(authorization.id, f"skipped_{candidate}", len(existing_intervals), len(existing_intervals), ""))
                report_rows.append(self._report_row(authorization, candidate, existing_intervals, None, ""))
                continue

            merged, _merge_counts = helper._merge_candidates(
                [
                    IntervalCandidate(
                        interval.authorization_id,
                        interval.start_date,
                        interval.end_date,
                        interval.source,
                        interval.note,
                    )
                    for interval in existing_intervals
                ]
                + [candidate]
            )
            action = self._action_for(existing_intervals, merged)
            counts[action] += 1
            plans.append(CatchUpPlan(authorization.id, action, len(existing_intervals), len(merged), ""))
            report_rows.append(self._report_row(authorization, action, existing_intervals, candidate, merged))

            if action != "unchanged":
                merged_by_authorization[authorization.id] = merged

        self._write_csv(output_dir / "validity_interval_catch_up_review.csv", self._report_fields(), report_rows)
        self._write_csv(
            output_dir / "validity_interval_catch_up_summary.csv",
            ["metric", "value"],
            [{"metric": key, "value": value} for key, value in {
                "target_database": settings.DATABASES["default"]["NAME"],
                "since": since.isoformat() if since else "",
                "all": use_all,
                "snapshot_date": snapshot_date.isoformat(),
                "existing_intervals_before": existing_total,
                "authorizations_considered": len(plans),
                **counts,
            }.items()],
        )

        self.stdout.write(f"Target database: {settings.DATABASES['default']['NAME']}")
        self.stdout.write(f"Existing intervals before catch-up: {existing_total}")
        self.stdout.write(f"Authorizations considered: {len(plans)}")
        for key, value in sorted(counts.items()):
            self.stdout.write(f"{key}: {value}")
        self.stdout.write(str(output_dir / "validity_interval_catch_up_summary.csv"))
        self.stdout.write(str(output_dir / "validity_interval_catch_up_review.csv"))

        if not write:
            self.stdout.write("Dry run only. Re-run with --write to apply these catch-up changes.")
            return

        with transaction.atomic():
            for authorization_id, merged_intervals in merged_by_authorization.items():
                AuthorizationValidityInterval.objects.filter(authorization_id=authorization_id).delete()
                AuthorizationValidityInterval.objects.bulk_create(
                    [
                        AuthorizationValidityInterval(
                            authorization_id=interval.authorization_id,
                            start_date=interval.start_date,
                            end_date=interval.end_date,
                            source=interval.source,
                            note=interval.note,
                        )
                        for interval in merged_intervals
                    ]
                )

        self.stdout.write(self.style.SUCCESS(f"Applied catch-up changes for {len(merged_by_authorization)} authorization(s)."))

    def _authorizations_since(self, since):
        return list(
            self._base_authorizations()
            .filter(Q(updated_at__gte=since) | Q(person__user__updated_at__gte=since))
            .order_by("id")
        )

    def _all_authorizations(self):
        return list(self._base_authorizations().order_by("id"))

    def _base_authorizations(self):
        return Authorization.objects.select_related("person__user", "style__discipline", "status")

    def _action_for(self, existing_intervals, merged):
        existing_keys = [(interval.start_date, interval.end_date, interval.source) for interval in existing_intervals]
        merged_keys = [(interval.start_date, interval.end_date, interval.source) for interval in merged]
        if existing_keys == merged_keys:
            return "unchanged"
        if not existing_intervals:
            return "created"
        if len(merged) > len(existing_intervals):
            return "added_gap_interval"
        if len(merged) < len(existing_intervals):
            return "merged_existing_intervals"
        return "updated"

    def _validate_interval_table(self):
        db_name = settings.DATABASES["default"]["NAME"]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = 'authorizations_authorizationvalidityinterval'
                """,
                [db_name],
            )
            if cursor.fetchone()[0] != 1:
                raise CommandError(f'Missing required table "{db_name}.authorizations_authorizationvalidityinterval".')

    def _parse_since(self, value):
        if not value:
            return None
        parsed_datetime = parse_datetime(value)
        if parsed_datetime:
            if timezone.is_naive(parsed_datetime):
                parsed_datetime = timezone.make_aware(parsed_datetime, timezone.get_current_timezone())
            return parsed_datetime
        parsed_date = parse_date(value)
        if parsed_date:
            return timezone.make_aware(datetime.combine(parsed_date, datetime.min.time()), timezone.get_current_timezone())
        raise CommandError("--since must be YYYY-MM-DD or an ISO datetime.")

    def _parse_snapshot_date(self, value):
        if not value:
            return date.today()
        parsed = parse_date(value)
        if not parsed:
            raise CommandError("--snapshot-date must be YYYY-MM-DD.")
        return parsed

    def _report_row(self, authorization, action, existing_intervals, candidate, merged):
        return {
            "authorization_id": authorization.id,
            "person_id": authorization.person_id,
            "sca_name": authorization.person.sca_name if authorization.person_id else "",
            "discipline": authorization.style.discipline.name if authorization.style_id and authorization.style.discipline_id else "",
            "style": authorization.style.name if authorization.style_id else "",
            "status": authorization.status.name if authorization.status_id else "",
            "authorization_expiration": authorization.expiration.isoformat() if authorization.expiration else "",
            "authorization_updated_at": authorization.updated_at.isoformat() if authorization.updated_at else "",
            "user_updated_at": authorization.person.user.updated_at.isoformat() if authorization.person_id else "",
            "action": action,
            "candidate_start": candidate.start_date.isoformat() if candidate and not isinstance(candidate, str) else "",
            "candidate_end": candidate.end_date.isoformat() if candidate and not isinstance(candidate, str) else "",
            "existing_intervals": self._intervals_text(existing_intervals),
            "merged_intervals": self._intervals_text(merged) if isinstance(merged, list) else "",
        }

    def _intervals_text(self, intervals):
        return "; ".join(
            f"{interval.start_date.isoformat()}..{interval.end_date.isoformat()} [{interval.source}]"
            for interval in intervals
        )

    def _report_fields(self):
        return [
            "authorization_id",
            "person_id",
            "sca_name",
            "discipline",
            "style",
            "status",
            "authorization_expiration",
            "authorization_updated_at",
            "user_updated_at",
            "action",
            "candidate_start",
            "candidate_end",
            "existing_intervals",
            "merged_intervals",
        ]

    def _write_csv(self, path, fieldnames, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
