import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from authorizations.models import SYSTEM_USER_IDS, User


FIELDNAMES = ["user_id", "state_province", "country"]


class Command(BaseCommand):
    help = "Backfill missing user state/province and country values from a reviewed source database or CSV."

    def add_arguments(self, parser):
        parser.add_argument("--source-db", default="trial", help="Source database alias containing reviewed address data.")
        parser.add_argument("--target-db", default="default", help="Target database alias to backfill.")
        parser.add_argument("--source-csv", help="Read reviewed jurisdiction values from this CSV instead of --source-db.")
        parser.add_argument("--export-source-csv", help="Export source values to this CSV and exit.")
        parser.add_argument("--apply", action="store_true", help="Apply changes. Without this, only report the planned changes.")
        parser.add_argument("--include-system-users", action="store_true", help="Include system/admin seed accounts.")

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

        actions, skipped = self._build_actions(source_rows, target_db, include_system_users)
        self._write_report(actions, skipped, apply=options["apply"])

        if options["apply"]:
            self._apply_actions(actions, target_db)
            self.stdout.write(self.style.SUCCESS(f"Applied jurisdiction backfill for {len(actions)} users."))
        else:
            self.stdout.write("No changes applied. Re-run with --apply to update the target database.")

    def _source_rows(self, source_csv, source_db, include_system_users):
        if source_csv:
            return self._read_source_csv(Path(source_csv), include_system_users)

        queryset = User.objects.using(source_db).order_by("id").values("id", "state_province", "country")
        if not include_system_users:
            queryset = queryset.exclude(id__in=SYSTEM_USER_IDS)
        rows = {}
        for row in queryset:
            rows[row["id"]] = {
                "state_province": self._clean(row["state_province"]),
                "country": self._clean(row["country"]),
            }
        return rows

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
                rows[user_id] = {
                    "state_province": self._clean(row["state_province"]),
                    "country": self._clean(row["country"]),
                }
        return rows

    def _write_source_csv(self, path, source_rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            writer.writeheader()
            for user_id in sorted(source_rows):
                row = source_rows[user_id]
                writer.writerow(
                    {
                        "user_id": user_id,
                        "state_province": row["state_province"],
                        "country": row["country"],
                    }
                )

    def _build_actions(self, source_rows, target_db, include_system_users):
        target_users = User.objects.using(target_db).order_by("id").only("id", "state_province", "country")
        if not include_system_users:
            target_users = target_users.exclude(id__in=SYSTEM_USER_IDS)

        actions = []
        skipped = {
            "target_users_seen": 0,
            "missing_source_row": 0,
            "source_has_no_values": 0,
            "target_already_complete": 0,
            "target_has_different_existing_value": 0,
        }
        for user in target_users:
            skipped["target_users_seen"] += 1
            source = source_rows.get(user.id)
            if source is None:
                skipped["missing_source_row"] += 1
                continue

            changes = {}
            conflicts = {}
            for field in ["state_province", "country"]:
                source_value = source[field]
                target_value = self._clean(getattr(user, field))
                if not source_value:
                    continue
                if not target_value:
                    changes[field] = source_value
                elif target_value != source_value:
                    conflicts[field] = {"target": target_value, "source": source_value}

            if conflicts:
                skipped["target_has_different_existing_value"] += 1
            if changes:
                actions.append({"user_id": user.id, "changes": changes, "conflicts": conflicts})
            elif not source["state_province"] and not source["country"]:
                skipped["source_has_no_values"] += 1
            elif not conflicts:
                skipped["target_already_complete"] += 1
        return actions, skipped

    def _apply_actions(self, actions, target_db):
        with transaction.atomic(using=target_db):
            for action in actions:
                user = User.objects.using(target_db).get(id=action["user_id"])
                update_fields = []
                for field, value in action["changes"].items():
                    if not self._clean(getattr(user, field)):
                        setattr(user, field, value)
                        update_fields.append(field)
                if update_fields:
                    update_fields.append("updated_at")
                    user.save(using=target_db, update_fields=update_fields)

    def _write_report(self, actions, skipped, *, apply):
        mode = "APPLY" if apply else "DRY RUN"
        self.stdout.write(f"Jurisdiction backfill {mode}")
        self.stdout.write(f"Target users inspected: {skipped['target_users_seen']}")
        self.stdout.write(f"Users to update: {len(actions)}")
        self.stdout.write(f"Missing source rows: {skipped['missing_source_row']}")
        self.stdout.write(f"Source rows without values: {skipped['source_has_no_values']}")
        self.stdout.write(f"Target users already complete: {skipped['target_already_complete']}")
        self.stdout.write(f"Existing target/source differences left untouched: {skipped['target_has_different_existing_value']}")

        if actions:
            self.stdout.write("")
            self.stdout.write("Planned updates:")
            for action in actions[:100]:
                fields = ", ".join(f"{field}={value}" for field, value in action["changes"].items())
                conflict_note = ""
                if action["conflicts"]:
                    conflicts = ", ".join(
                        f"{field}: target={values['target']} source={values['source']}"
                        for field, values in action["conflicts"].items()
                    )
                    conflict_note = f" (left existing differences untouched: {conflicts})"
                self.stdout.write(f"- user_id={action['user_id']}: {fields}{conflict_note}")
            if len(actions) > 100:
                self.stdout.write(f"... {len(actions) - 100} additional updates omitted from console output.")

    def _clean(self, value):
        return str(value or "").strip()
