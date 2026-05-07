import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from authorizations.models import Branch, Discipline, Title, WeaponStyle


REMOVE_MARKER = "---"
REFERENCE_ACTIONS_WITHOUT_TARGET = {
    "remove",
    "ignore_for_marshal_transform",
    "no_extra_marshal_authorization",
}
REFERENCE_ACTIONS_WITH_TARGET = {
    "map_to_current",
    "create_extra_marshal_authorization",
}


class Command(BaseCommand):
    help = "Validate hand-edited legacy migration decision CSVs without writing to either database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--duplicate-person-file",
            default=str(
                Path(settings.BASE_DIR)
                / "tmp"
                / "legacy_migration_plan_review3"
                / "duplicate_person_candidate_review.csv"
            ),
            help="Hand-edited duplicate person decision CSV.",
        )
        parser.add_argument(
            "--reference-mapping-file",
            default=str(
                Path(settings.BASE_DIR)
                / "tmp"
                / "legacy_migration_plan_review3"
                / "reference_mapping_review.csv"
            ),
            help="Hand-edited reference mapping CSV.",
        )
        parser.add_argument(
            "--output-dir",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_migration_decision_validation"),
            help="Directory where validation CSV reports will be written.",
        )
        parser.add_argument("--legacy-db", default="legacy", help="Django database alias for the legacy database.")

    def handle(self, *args, **options):
        duplicate_person_file = Path(options["duplicate_person_file"])
        reference_mapping_file = Path(options["reference_mapping_file"])
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        legacy_alias = options["legacy_db"]

        if legacy_alias not in connections:
            raise CommandError(f'Database alias "{legacy_alias}" is not configured.')
        if not duplicate_person_file.exists():
            raise CommandError(f"Duplicate person decision file not found: {duplicate_person_file}")
        if not reference_mapping_file.exists():
            raise CommandError(f"Reference mapping decision file not found: {reference_mapping_file}")

        self.cursor = connections[legacy_alias].cursor()
        self.current_branches = self._current_name_map(Branch.objects.all())
        self.current_disciplines = self._current_name_map(Discipline.objects.all())
        self.current_titles = self._current_name_map(Title.objects.all())
        self.current_weaponstyles = {
            (self._norm(style.discipline.name), self._norm(style.name)): style
            for style in WeaponStyle.objects.select_related("discipline")
        }

        decisions = self._read_csv(duplicate_person_file)
        self._validate_required_columns(decisions, duplicate_person_file)
        validation_rows, action_rows = self._validate_duplicate_person_decisions(decisions)

        reference_decisions = self._read_csv(reference_mapping_file)
        self._validate_reference_columns(reference_decisions, reference_mapping_file)
        reference_validation_rows, normalized_mapping_rows = self._validate_reference_mapping_decisions(
            reference_decisions
        )
        validation_rows.extend(reference_validation_rows)

        validation_path = output_dir / "duplicate_person_decision_validation.csv"
        action_path = output_dir / "duplicate_person_import_actions.csv"
        reference_validation_path = output_dir / "reference_mapping_decision_validation.csv"
        normalized_reference_path = output_dir / "normalized_reference_mapping.csv"
        self._write_csv(
            validation_path,
            ["severity", "legacy_person_id", "field", "value", "message"],
            validation_rows,
        )
        self._write_csv(
            action_path,
            [
                "legacy_person_id",
                "map_to_id",
                "is_canonical_import_person",
                "authorization_count_to_move",
                "membership_action",
                "membership_value",
                "membership_expiration_action",
                "membership_expiration_value",
                "notes",
            ],
            action_rows,
        )
        self._write_csv(
            reference_validation_path,
            ["severity", "reference_type", "legacy_id", "field", "value", "message"],
            reference_validation_rows,
        )
        self._write_csv(
            normalized_reference_path,
            [
                "reference_type",
                "legacy_id",
                "legacy_name",
                "legacy_context",
                "suggested_action",
                "current_id",
                "current_value",
                "notes",
            ],
            normalized_mapping_rows,
        )

        error_count = sum(1 for row in validation_rows if row["severity"] == "error")
        warning_count = sum(1 for row in validation_rows if row["severity"] == "warning")

        self.stdout.write(self.style.SUCCESS("Legacy migration decision validation complete."))
        self.stdout.write(str(validation_path))
        self.stdout.write(str(action_path))
        self.stdout.write(str(reference_validation_path))
        self.stdout.write(str(normalized_reference_path))
        self.stdout.write(f"Errors: {error_count}")
        self.stdout.write(f"Warnings: {warning_count}")

    def _validate_duplicate_person_decisions(self, decisions):
        people_ids = {row["ID"].strip() for row in decisions if row.get("ID", "").strip()}
        map_targets = {
            row.get("map_to_id", "").strip()
            for row in decisions
            if row.get("map_to_id", "").strip()
        }
        all_ids_to_check = people_ids | map_targets
        existing_people = self._legacy_people_by_id(all_ids_to_check)
        auth_counts = self._authorization_counts_by_person(all_ids_to_check)

        validation_rows = []
        action_rows = []
        reverse_map = defaultdict(list)

        for row in decisions:
            person_id = row.get("ID", "").strip()
            map_to_id = row.get("map_to_id", "").strip()
            mem_fix = row.get("MemFix", "").strip()
            mem_exp_fix = row.get("MemExpFix", "").strip()

            if not person_id:
                validation_rows.append(self._validation_row("error", "", "ID", "", "Missing legacy person ID."))
                continue
            if person_id not in existing_people:
                validation_rows.append(
                    self._validation_row("error", person_id, "ID", person_id, "Legacy person ID does not exist.")
                )

            if map_to_id:
                if not map_to_id.isdigit():
                    validation_rows.append(
                        self._validation_row("error", person_id, "map_to_id", map_to_id, "map_to_id must be numeric.")
                    )
                elif map_to_id not in existing_people:
                    validation_rows.append(
                        self._validation_row(
                            "error",
                            person_id,
                            "map_to_id",
                            map_to_id,
                            "map_to_id does not exist in legacy people.",
                        )
                    )
                reverse_map[map_to_id].append(person_id)
            else:
                map_to_id = person_id
                reverse_map[map_to_id].append(person_id)

            if mem_fix and mem_fix != REMOVE_MARKER and not mem_fix.isdigit():
                validation_rows.append(
                    self._validation_row(
                        "warning",
                        person_id,
                        "MemFix",
                        mem_fix,
                        "MemFix is neither blank, removal marker, nor numeric membership value.",
                    )
                )
            if mem_exp_fix and mem_exp_fix != REMOVE_MARKER and not self._parse_optional_date(mem_exp_fix):
                validation_rows.append(
                    self._validation_row(
                        "warning",
                        person_id,
                        "MemExpFix",
                        mem_exp_fix,
                        "MemExpFix is not blank, removal marker, or a recognized date.",
                    )
                )

            membership_action = "keep_legacy_membership"
            membership_value = row.get("MembershipNum", "").strip()
            if mem_fix == REMOVE_MARKER:
                membership_action = "clear_membership"
                membership_value = ""
            elif mem_fix:
                membership_action = "override_membership"
                membership_value = mem_fix

            membership_expiration_action = "keep_legacy_membership_expiration"
            membership_expiration_value = row.get("MembershipExpiration", "").strip()
            if mem_exp_fix == REMOVE_MARKER or mem_fix == REMOVE_MARKER:
                membership_expiration_action = "clear_membership_expiration"
                membership_expiration_value = ""
            elif mem_exp_fix:
                membership_expiration_action = "override_membership_expiration"
                membership_expiration_value = self._normalize_date(mem_exp_fix) or mem_exp_fix

            action_rows.append(
                {
                    "legacy_person_id": person_id,
                    "map_to_id": map_to_id,
                    "is_canonical_import_person": "yes" if person_id == map_to_id else "no",
                    "authorization_count_to_move": auth_counts.get(person_id, 0) if person_id != map_to_id else 0,
                    "membership_action": membership_action,
                    "membership_value": membership_value,
                    "membership_expiration_action": membership_expiration_action,
                    "membership_expiration_value": membership_expiration_value,
                    "notes": "Use map_to_id as the canonical imported person for duplicate legacy people.",
                }
            )

        for target_id, source_ids in sorted(reverse_map.items()):
            if target_id and target_id not in source_ids:
                validation_rows.append(
                    self._validation_row(
                        "warning",
                        target_id,
                        "map_to_id",
                        target_id,
                        "map_to_id target is not present as its own row in the decision file.",
                    )
                )

        return validation_rows, action_rows

    def _validate_required_columns(self, rows, path):
        if not rows:
            raise CommandError(f"Decision file has no rows: {path}")
        required = {"ID", "map_to_id", "MemFix"}
        missing = sorted(required - set(rows[0].keys()))
        if missing:
            raise CommandError(f"{path} is missing required columns: {', '.join(missing)}")

    def _validate_reference_columns(self, rows, path):
        if not rows:
            raise CommandError(f"Reference mapping file has no rows: {path}")
        required = {"reference_type", "legacy_id", "legacy_name", "suggested_action", "suggested_current_value"}
        missing = sorted(required - set(rows[0].keys()))
        if missing:
            raise CommandError(f"{path} is missing required columns: {', '.join(missing)}")

    def _validate_reference_mapping_decisions(self, rows):
        validation_rows = []
        normalized_rows = []
        known_actions = REFERENCE_ACTIONS_WITHOUT_TARGET | REFERENCE_ACTIONS_WITH_TARGET

        for row in rows:
            reference_type = row["reference_type"].strip()
            legacy_id = row["legacy_id"].strip()
            action = row["suggested_action"].strip()
            current_value = row["suggested_current_value"].strip()
            current_id = ""

            if action not in known_actions:
                validation_rows.append(
                    self._reference_validation_row(
                        "error",
                        reference_type,
                        legacy_id,
                        "suggested_action",
                        action,
                        "Unknown suggested_action.",
                    )
                )
            if action in REFERENCE_ACTIONS_WITH_TARGET and not current_value:
                validation_rows.append(
                    self._reference_validation_row(
                        "error",
                        reference_type,
                        legacy_id,
                        "suggested_current_value",
                        current_value,
                        "Action requires a current target value.",
                    )
                )
            if action in REFERENCE_ACTIONS_WITHOUT_TARGET and current_value:
                validation_rows.append(
                    self._reference_validation_row(
                        "warning",
                        reference_type,
                        legacy_id,
                        "suggested_current_value",
                        current_value,
                        "Action ignores current target value.",
                    )
                )

            target = None
            if current_value and action in REFERENCE_ACTIONS_WITH_TARGET:
                target = self._resolve_current_reference(reference_type, current_value)
                if not target:
                    validation_rows.append(
                        self._reference_validation_row(
                            "error",
                            reference_type,
                            legacy_id,
                            "suggested_current_value",
                            current_value,
                            "Current target value was not found in canonical current data.",
                        )
                    )
                else:
                    current_id = str(target.id)

            normalized_rows.append(
                {
                    "reference_type": reference_type,
                    "legacy_id": legacy_id,
                    "legacy_name": row["legacy_name"].strip(),
                    "legacy_context": row.get("legacy_context", "").strip(),
                    "suggested_action": action,
                    "current_id": current_id,
                    "current_value": current_value,
                    "notes": row.get("notes", "").strip(),
                }
            )

        return validation_rows, normalized_rows

    def _resolve_current_reference(self, reference_type, current_value):
        if reference_type == "branch":
            return self.current_branches.get(self._norm(current_value))
        if reference_type == "discipline":
            return self.current_disciplines.get(self._norm(current_value))
        if reference_type == "title":
            return self.current_titles.get(self._norm(current_value))
        if reference_type in {"weaponform", "marshallevel"}:
            discipline_name, style_name = self._split_current_style_value(current_value)
            if not discipline_name or not style_name:
                return None
            return self.current_weaponstyles.get((self._norm(discipline_name), self._norm(style_name)))
        return None

    def _split_current_style_value(self, current_value):
        if "/" in current_value:
            discipline_name, style_name = current_value.split("/", 1)
        elif " - " in current_value:
            discipline_name, style_name = current_value.split(" - ", 1)
        else:
            return "", ""
        return discipline_name.strip(), style_name.strip()

    def _legacy_people_by_id(self, person_ids):
        person_ids = sorted({person_id for person_id in person_ids if person_id and person_id.isdigit()})
        if not person_ids:
            return {}
        self.cursor.execute(
            f"SELECT ID, SCAName, LegalName FROM people WHERE ID IN ({','.join(['%s'] * len(person_ids))})",
            person_ids,
        )
        return {str(row[0]): {"sca_name": row[1], "legal_name": row[2]} for row in self.cursor.fetchall()}

    def _authorization_counts_by_person(self, person_ids):
        person_ids = sorted({person_id for person_id in person_ids if person_id and person_id.isdigit()})
        if not person_ids:
            return {}
        self.cursor.execute(
            f"SELECT PersonID, COUNT(*) FROM authorizations WHERE PersonID IN ({','.join(['%s'] * len(person_ids))}) "
            "GROUP BY PersonID",
            person_ids,
        )
        return {str(row[0]): row[1] for row in self.cursor.fetchall()}

    def _read_csv(self, path):
        with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            return list(csv.DictReader(csv_file))

    def _write_csv(self, path, fieldnames, rows):
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _validation_row(self, severity, person_id, field, value, message):
        return {
            "severity": severity,
            "legacy_person_id": person_id,
            "field": field,
            "value": value,
            "message": message,
        }

    def _reference_validation_row(self, severity, reference_type, legacy_id, field, value, message):
        return {
            "severity": severity,
            "reference_type": reference_type,
            "legacy_id": legacy_id,
            "field": field,
            "value": value,
            "message": message,
        }

    def _current_name_map(self, queryset):
        return {self._norm(obj.name): obj for obj in queryset}

    def _norm(self, value):
        return " ".join(str(value or "").casefold().split())

    def _parse_optional_date(self, value):
        return self._normalize_date(value) is not None

    def _normalize_date(self, value):
        value = (value or "").strip()
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
        return None
