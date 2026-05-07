import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from authorizations.models import SYSTEM_USER_IDS, User


PROFILE_FIELDS = [
    "username",
    "email",
    "first_name",
    "last_name",
    "membership",
    "membership_expiration",
    "address",
    "address2",
    "city",
    "state_province",
    "postal_code",
    "country",
    "phone_number",
    "background_check_expiration",
    "birthday",
]
FIELDNAMES = ["user_id", *PROFILE_FIELDS]


class Command(BaseCommand):
    help = "Audit imported user profile columns against a reviewed source database or CSV without applying changes."

    def add_arguments(self, parser):
        parser.add_argument("--source-db", default="trial", help="Source database alias containing reviewed import data.")
        parser.add_argument("--target-db", default="default", help="Target database alias to audit.")
        parser.add_argument("--source-csv", help="Read reviewed source values from this CSV instead of --source-db.")
        parser.add_argument("--export-source-csv", help="Export reviewed source values to this CSV and exit.")
        parser.add_argument("--include-system-users", action="store_true", help="Include system/admin seed accounts.")
        parser.add_argument("--sample-limit", type=int, default=25, help="Number of sample mismatches to print.")

    def handle(self, *args, **options):
        source_db = options["source_db"]
        target_db = options["target_db"]
        source_csv = options.get("source_csv")
        export_source_csv = options.get("export_source_csv")
        include_system_users = options["include_system_users"]

        if source_csv and export_source_csv:
            raise CommandError("--source-csv and --export-source-csv cannot be used together.")
        if not source_csv and source_db not in connections:
            raise CommandError(f'Source database alias "{source_db}" is not configured.')
        if target_db not in connections:
            raise CommandError(f'Target database alias "{target_db}" is not configured.')
        if not source_csv and source_db == target_db:
            raise CommandError("Source and target database aliases must be different.")

        source_rows = self._source_rows(source_csv, source_db, include_system_users)
        if export_source_csv:
            self._write_source_csv(Path(export_source_csv), source_rows)
            self.stdout.write(self.style.SUCCESS(f"Exported {len(source_rows)} source rows to {export_source_csv}."))
            return

        self._write_audit(source_rows, target_db, include_system_users, options["sample_limit"])

    def _source_rows(self, source_csv, source_db, include_system_users):
        if source_csv:
            return self._read_source_csv(Path(source_csv), include_system_users)

        queryset = User.objects.using(source_db).order_by("id").values("id", *PROFILE_FIELDS)
        if not include_system_users:
            queryset = queryset.exclude(id__in=SYSTEM_USER_IDS)
        return {row["id"]: {field: self._clean(row[field]) for field in PROFILE_FIELDS} for row in queryset}

    def _read_source_csv(self, path, include_system_users):
        if not path.exists():
            raise CommandError(f"Source CSV not found: {path}")
        rows = {}
        with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            missing = [field for field in FIELDNAMES if field not in (reader.fieldnames or [])]
            if missing:
                raise CommandError(f"Source CSV is missing required columns: {', '.join(missing)}")
            for row in reader:
                user_id = int(row["user_id"])
                if not include_system_users and user_id in SYSTEM_USER_IDS:
                    continue
                rows[user_id] = {field: self._clean(row[field]) for field in PROFILE_FIELDS}
        return rows

    def _write_source_csv(self, path, source_rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            writer.writeheader()
            for user_id in sorted(source_rows):
                writer.writerow({"user_id": user_id, **source_rows[user_id]})

    def _write_audit(self, source_rows, target_db, include_system_users, sample_limit):
        target_users = User.objects.using(target_db).order_by("id").only("id", *PROFILE_FIELDS)
        if not include_system_users:
            target_users = target_users.exclude(id__in=SYSTEM_USER_IDS)

        target_ids = set()
        stats = {
            field: {
                "source_nonblank": 0,
                "target_blank_source_nonblank": 0,
                "different": 0,
            }
            for field in PROFILE_FIELDS
        }
        samples = []

        for user in target_users:
            target_ids.add(user.id)
            source = source_rows.get(user.id)
            if source is None:
                continue
            for field in PROFILE_FIELDS:
                source_value = source[field]
                target_value = self._clean(getattr(user, field))
                if source_value:
                    stats[field]["source_nonblank"] += 1
                if source_value and not target_value:
                    stats[field]["target_blank_source_nonblank"] += 1
                if source_value != target_value:
                    stats[field]["different"] += 1
                    if len(samples) < sample_limit:
                        samples.append((user.id, field, target_value, source_value))

        common_count = len(set(source_rows) & target_ids)
        self.stdout.write("User profile import audit")
        self.stdout.write(f"Source users: {len(source_rows)}")
        self.stdout.write(f"Target users: {len(target_ids)}")
        self.stdout.write(f"Matched user IDs: {common_count}")
        self.stdout.write(f"Source users missing in target: {len(set(source_rows) - target_ids)}")
        self.stdout.write(f"Target users missing in source: {len(target_ids - set(source_rows))}")
        self.stdout.write("")
        self.stdout.write("Field summary:")
        for field in PROFILE_FIELDS:
            row = stats[field]
            self.stdout.write(
                f"- {field}: source_nonblank={row['source_nonblank']}, "
                f"target_blank_source_nonblank={row['target_blank_source_nonblank']}, "
                f"different={row['different']}"
            )

        if samples:
            self.stdout.write("")
            self.stdout.write("Sample differences:")
            for user_id, field, target_value, source_value in samples:
                self.stdout.write(f"- user_id={user_id}, field={field}, target={target_value}, source={source_value}")

    def _clean(self, value):
        return str(value or "").strip()
