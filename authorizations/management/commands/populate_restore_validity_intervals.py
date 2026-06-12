import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from authorizations.models import Authorization, AuthorizationValidityInterval, is_minor_from_birthday
from authorizations.permissions import YOUTH_DISCIPLINE_NAMES


@dataclass
class IntervalCandidate:
    authorization_id: int
    start_date: date
    end_date: date
    source: str
    note: str


class Command(BaseCommand):
    help = (
        "Populate merged legacy/current authorization validity intervals into the restored "
        "production database. The legacy database is read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--legacy-db-name", default="test_antir_auth_local")
        parser.add_argument("--legacy-snapshot-date", default="2025-05-09")
        parser.add_argument("--current-snapshot-date", default="2026-06-05")
        parser.add_argument(
            "--output-dir",
            default=str(Path(settings.BASE_DIR) / "tmp" / "validity_interval_population"),
        )
        parser.add_argument("--write", action="store_true", help="Persist intervals. Dry-run is the default.")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing validity intervals before writing. Requires --write.",
        )
        parser.add_argument(
            "--include-reviewed-drift",
            action="store_true",
            help=(
                "Include legacy rows whose authorization ID exists in the restore database but whose "
                "person/style changed. Use only after manually reviewing those rows."
            ),
        )

    def handle(self, *args, **options):
        legacy_db_name = options["legacy_db_name"]
        legacy_snapshot_date = self._parse_date(options["legacy_snapshot_date"], "--legacy-snapshot-date")
        current_snapshot_date = self._parse_date(options["current_snapshot_date"], "--current-snapshot-date")
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        write = options["write"]
        replace = options["replace"]
        include_reviewed_drift = options["include_reviewed_drift"]

        if replace and not write:
            raise CommandError("--replace requires --write.")
        self._validate_databases(legacy_db_name)

        existing_count = AuthorizationValidityInterval.objects.count()
        if existing_count and write and not replace:
            raise CommandError(
                f"Found {existing_count} existing validity interval(s) in {settings.DATABASES['default']['NAME']}. "
                "Use --replace if this vetted restore population should replace them."
            )

        current_candidates, current_skips = self._current_candidates(current_snapshot_date)
        legacy_candidates, legacy_report_rows, legacy_skips = self._legacy_candidates(
            legacy_db_name,
            legacy_snapshot_date,
            include_reviewed_drift,
        )
        merged_intervals, merge_counts = self._merge_candidates(current_candidates + legacy_candidates)

        self._write_csv(output_dir / "legacy_interval_review.csv", self._legacy_review_fields(), legacy_report_rows)
        self._write_csv(output_dir / "interval_population_summary.csv", ["metric", "value"], self._summary_rows({
            "target_database": settings.DATABASES["default"]["NAME"],
            "legacy_database": legacy_db_name,
            "legacy_snapshot_date": legacy_snapshot_date.isoformat(),
            "current_snapshot_date": current_snapshot_date.isoformat(),
            "existing_intervals": existing_count,
            "include_reviewed_drift": include_reviewed_drift,
            "current_candidates": len(current_candidates),
            "legacy_candidates": len(legacy_candidates),
            "merged_intervals": len(merged_intervals),
            **{f"current_skip_{key}": value for key, value in current_skips.items()},
            **{f"legacy_{key}": value for key, value in legacy_skips.items()},
            **{f"merge_{key}": value for key, value in merge_counts.items()},
        }))

        self.stdout.write(f"Target database: {settings.DATABASES['default']['NAME']}")
        self.stdout.write(f"Legacy source database: {legacy_db_name}")
        self.stdout.write(f"Existing intervals: {existing_count}")
        self.stdout.write(f"Current candidates: {len(current_candidates)}")
        self.stdout.write(f"Legacy candidates: {len(legacy_candidates)}")
        self.stdout.write(f"Merged intervals prepared: {len(merged_intervals)}")
        for key, value in sorted(current_skips.items()):
            self.stdout.write(f"Current skip {key}: {value}")
        for key, value in sorted(legacy_skips.items()):
            self.stdout.write(f"Legacy {key}: {value}")
        self.stdout.write(str(output_dir / "interval_population_summary.csv"))
        self.stdout.write(str(output_dir / "legacy_interval_review.csv"))

        if not write:
            self.stdout.write("Dry run only. Re-run with --write to create intervals.")
            return

        with transaction.atomic():
            if replace:
                AuthorizationValidityInterval.objects.all().delete()
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

        replace_text = " after replacing existing intervals" if replace and existing_count else ""
        self.stdout.write(self.style.SUCCESS(f"Created {len(merged_intervals)} validity interval(s){replace_text}."))

    def _validate_databases(self, legacy_db_name):
        target_db_name = settings.DATABASES["default"]["NAME"]
        if target_db_name == legacy_db_name:
            raise CommandError("Default database is the legacy source. Refusing to write to the legacy database.")
        if target_db_name != "an_tir_authorizations_restore_test":
            raise CommandError(
                f'Default database is "{target_db_name}". '
                'Point DB_NAME at "an_tir_authorizations_restore_test" before running this command.'
            )

        with connection.cursor() as cursor:
            for db_name, table_name in [
                (legacy_db_name, "authorizations_authorization"),
                (target_db_name, "authorizations_authorizationvalidityinterval"),
            ]:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    [db_name, table_name],
                )
                if cursor.fetchone()[0] != 1:
                    raise CommandError(f'Missing required table "{db_name}.{table_name}".')

    def _current_candidates(self, snapshot_date):
        candidates = []
        skips = Counter()
        authorizations = Authorization.objects.select_related("person__user", "style__discipline").order_by("id")

        for authorization in authorizations:
            candidate = self._candidate_from_authorization(
                authorization_id=authorization.id,
                person=authorization.person,
                style=authorization.style,
                expiration=authorization.expiration,
                snapshot_date=snapshot_date,
                source="portal_authorization",
                note=(
                    f"Generated from restored production snapshot dated {snapshot_date.isoformat()}; "
                    "start uses stored authorization expiration and end uses effective expiration."
                ),
            )
            if isinstance(candidate, str):
                skips[candidate] += 1
            else:
                candidates.append(candidate)

        return candidates, skips

    def _legacy_candidates(self, legacy_db_name, snapshot_date, include_reviewed_drift):
        legacy_rows = self._legacy_rows(legacy_db_name)
        target_by_id = {
            authorization.id: authorization
            for authorization in Authorization.objects.select_related("person__user", "style__discipline").filter(
                id__in=[row["id"] for row in legacy_rows]
            )
        }
        candidates = []
        report_rows = []
        skips = Counter()

        for row in legacy_rows:
            target = target_by_id.get(row["id"])
            if not target:
                skips["missing_target_authorization"] += 1
                report_rows.append(self._legacy_review_row(row, "missing_target_authorization", "No target authorization with this ID."))
                continue
            target_drifted = target.person_id != row["person_id"] or target.style_id != row["style_id"]
            if target_drifted and not include_reviewed_drift:
                skips["target_person_or_style_changed"] += 1
                report_rows.append(
                    self._legacy_review_row(
                        row,
                        "target_person_or_style_changed",
                        (
                            f"Target id={target.id} has person_id={target.person_id}, style_id={target.style_id}; "
                            f"legacy had person_id={row['person_id']}, style_id={row['style_id']}."
                        ),
                    )
                )
                continue

            candidate = self._candidate_from_raw_row(row, snapshot_date, target.id, target_drifted)
            if isinstance(candidate, str):
                skips[candidate] += 1
                report_rows.append(self._legacy_review_row(row, candidate, "Could not build a valid legacy interval."))
            else:
                candidates.append(candidate)
                result = "reviewed_drift_candidate_created" if target_drifted else "candidate_created"
                report_rows.append(self._legacy_review_row(row, result, ""))

        return candidates, report_rows, skips

    def _legacy_rows(self, legacy_db_name):
        quoted_db = f"`{legacy_db_name.replace('`', '``')}`"
        sql = f"""
            SELECT
                a.id,
                a.person_id,
                a.style_id,
                a.expiration,
                p.sca_name,
                p.is_minor,
                u.birthday,
                u.country,
                u.state_province,
                u.membership_expiration,
                u.background_check_expiration,
                ws.name AS style_name,
                d.name AS discipline_name
            FROM {quoted_db}.authorizations_authorization a
            LEFT JOIN {quoted_db}.authorizations_person p ON p.user_id = a.person_id
            LEFT JOIN {quoted_db}.authorizations_user u ON u.id = a.person_id
            LEFT JOIN {quoted_db}.authorizations_weaponstyle ws ON ws.id = a.style_id
            LEFT JOIN {quoted_db}.authorizations_discipline d ON d.id = ws.discipline_id
            ORDER BY a.id
        """
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, values)) for values in cursor.fetchall()]

    def _candidate_from_authorization(self, *, authorization_id, person, style, expiration, snapshot_date, source, note):
        if not expiration:
            return "missing_expiration"
        if not style or not getattr(style, "discipline", None):
            return "missing_style_or_discipline"

        is_minor = bool(person and person.is_current_minor)
        years = self._duration_years(style.discipline.name, is_minor)
        start_date = expiration - relativedelta(years=years)
        end_date = self._effective_expiration(
            expiration=expiration,
            style_name=style.name,
            discipline_name=style.discipline.name,
            membership_expiration=getattr(person.user, "membership_expiration", None),
            background_check_expiration=getattr(person.user, "background_check_expiration", None),
            snapshot_date=snapshot_date,
        )
        if end_date < start_date:
            if end_date < snapshot_date:
                return "effective_expiration_before_start"
            start_date = snapshot_date

        return IntervalCandidate(authorization_id, start_date, end_date, source, note)

    def _candidate_from_raw_row(self, row, snapshot_date, target_authorization_id, target_drifted=False):
        if not row["expiration"]:
            return "missing_expiration"
        if not row["style_name"] or not row["discipline_name"]:
            return "missing_style_or_discipline"

        is_minor = bool(row.get("is_minor")) or is_minor_from_birthday(
            row.get("birthday"),
            row.get("country") or "",
            row.get("state_province") or "",
            today=snapshot_date,
        )
        years = self._duration_years(row["discipline_name"], is_minor)
        start_date = row["expiration"] - relativedelta(years=years)
        end_date = self._effective_expiration(
            expiration=row["expiration"],
            style_name=row["style_name"],
            discipline_name=row["discipline_name"],
            membership_expiration=row.get("membership_expiration"),
            background_check_expiration=row.get("background_check_expiration"),
            snapshot_date=snapshot_date,
        )
        if end_date < start_date:
            if end_date < snapshot_date:
                return "effective_expiration_before_start"
            start_date = snapshot_date

        return IntervalCandidate(
            target_authorization_id,
            start_date,
            end_date,
            "legacy_import",
            (
                f"Generated from test_antir_auth_local snapshot dated {snapshot_date.isoformat()}; "
                "start uses stored authorization expiration and end uses effective expiration."
                + (" Legacy row was manually reviewed before attaching across person/style drift." if target_drifted else "")
            ),
        )

    def _duration_years(self, discipline_name, is_minor):
        if discipline_name in YOUTH_DISCIPLINE_NAMES or is_minor:
            return 2
        return 4

    def _effective_expiration(
        self,
        *,
        expiration,
        style_name,
        discipline_name,
        membership_expiration,
        background_check_expiration,
        snapshot_date,
    ):
        if style_name not in ["Junior Marshal", "Senior Marshal"]:
            return expiration

        expired_fallback = snapshot_date - timedelta(days=1)
        end_date = min(expiration, membership_expiration or expired_fallback)
        if discipline_name in YOUTH_DISCIPLINE_NAMES:
            end_date = min(end_date, background_check_expiration or expired_fallback)
        return end_date

    def _merge_candidates(self, candidates):
        by_authorization = defaultdict(list)
        for candidate in candidates:
            by_authorization[candidate.authorization_id].append(candidate)

        merged = []
        counts = Counter()
        for authorization_id in sorted(by_authorization):
            intervals = sorted(
                by_authorization[authorization_id],
                key=lambda candidate: (candidate.start_date, candidate.end_date, candidate.source),
            )
            for interval in intervals:
                if not merged or merged[-1].authorization_id != authorization_id:
                    merged.append(interval)
                    counts["created_interval"] += 1
                    continue

                previous = merged[-1]
                if interval.start_date <= previous.end_date:
                    if interval.end_date > previous.end_date:
                        previous.end_date = interval.end_date
                        previous.note = f"{previous.note} Merged with overlapping {interval.source} interval."
                        counts["extended_interval"] += 1
                    else:
                        counts["contained_interval"] += 1
                else:
                    merged.append(interval)
                    counts["created_interval"] += 1

        return merged, counts

    def _legacy_review_row(self, row, result, note):
        return {
            "legacy_authorization_id": row["id"],
            "person_id": row["person_id"],
            "sca_name": row.get("sca_name") or "",
            "style_id": row["style_id"],
            "discipline": row.get("discipline_name") or "",
            "style": row.get("style_name") or "",
            "expiration": self._date_string(row.get("expiration")),
            "result": result,
            "note": note,
        }

    def _legacy_review_fields(self):
        return [
            "legacy_authorization_id",
            "person_id",
            "sca_name",
            "style_id",
            "discipline",
            "style",
            "expiration",
            "result",
            "note",
        ]

    def _write_csv(self, path, fieldnames, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _summary_rows(self, summary):
        return [{"metric": key, "value": value} for key, value in summary.items()]

    def _parse_date(self, value, option_name):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError(f"{option_name} must be YYYY-MM-DD.") from exc

    def _date_string(self, value):
        if not value:
            return ""
        return value.isoformat()
