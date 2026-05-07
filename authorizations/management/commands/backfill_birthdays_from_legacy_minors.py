import csv
import re
from datetime import date, datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from authorizations.models import Person, User


ZERO_DATE = "0000-00-00"
MINOR_CUTOFF = date(2026, 5, 1)
CANADA_PROVINCES = {
    "AB",
    "BC",
    "MB",
    "NB",
    "NL",
    "NS",
    "NT",
    "NU",
    "ON",
    "PE",
    "QC",
    "SK",
    "YT",
    "ALBERTA",
    "BRITISH COLUMBIA",
    "MANITOBA",
    "NEW BRUNSWICK",
    "NEWFOUNDLAND AND LABRADOR",
    "NOVA SCOTIA",
    "NORTHWEST TERRITORIES",
    "NUNAVUT",
    "ONTARIO",
    "PRINCE EDWARD ISLAND",
    "QUEBEC",
    "SASKATCHEWAN",
    "YUKON",
}


class Command(BaseCommand):
    help = "Generate or apply birthday/minor cleanup rows from legacy MinorExpDate values."

    def add_arguments(self, parser):
        parser.add_argument("--legacy-db", default="legacy", help="Legacy source database alias.")
        parser.add_argument("--target-db", default="trial", help="Target database alias to inspect/update.")
        parser.add_argument(
            "--duplicate-actions-file",
            default=str(
                Path(settings.BASE_DIR)
                / "tmp"
                / "legacy_migration_decision_validation"
                / "duplicate_person_import_actions.csv"
            ),
            help="Validated duplicate person import actions CSV.",
        )
        parser.add_argument(
            "--output-file",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_minor_birthday_cleanup.csv"),
            help="CSV to write when generating cleanup rows.",
        )
        parser.add_argument(
            "--input-file",
            help="CSV to apply. If omitted, the command generates a CSV from legacy data.",
        )
        parser.add_argument("--apply", action="store_true", help="Apply the input CSV to the target database.")

    def handle(self, *args, **options):
        self.legacy_alias = options["legacy_db"]
        self.target_alias = options["target_db"]
        self.duplicate_actions_file = Path(options["duplicate_actions_file"])

        if options["apply"]:
            if not options["input_file"]:
                raise CommandError("--apply requires --input-file.")
            rows = self._read_csv(Path(options["input_file"]))
            self._apply_rows(rows)
            return

        rows = self._build_rows()
        output_file = Path(options["output_file"])
        output_file.parent.mkdir(parents=True, exist_ok=True)
        self._write_csv(output_file, rows)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} cleanup row(s) to {output_file}"))

    def _build_rows(self):
        if self.legacy_alias not in connections.databases:
            raise CommandError(f'Legacy database alias "{self.legacy_alias}" is not configured.')
        if self.target_alias not in connections.databases:
            raise CommandError(f'Target database alias "{self.target_alias}" is not configured.')

        legacy_people = self._legacy_people()
        person_actions = self._person_actions()
        imported_canonical_ids = self._canonical_legacy_ids(legacy_people, person_actions)
        username_by_legacy_id = self._expected_usernames(legacy_people, imported_canonical_ids)
        rows = []

        for legacy_person_id in sorted(imported_canonical_ids):
            legacy_row = legacy_people[legacy_person_id]
            minor_expiration = self._date_or_none(legacy_row["MinorExpDate"])
            if not minor_expiration:
                continue
            username = username_by_legacy_id[legacy_person_id]
            user = User.objects.using(self.target_alias).filter(username=username).first()
            if not user:
                rows.append(
                    self._row(
                        legacy_row,
                        legacy_person_id,
                        username,
                        action="unresolved",
                        reason="Expected imported user was not found by username.",
                    )
                )
                continue
            person = Person.objects.using(self.target_alias).filter(user_id=user.id).first()
            if not person:
                rows.append(
                    self._row(
                        legacy_row,
                        legacy_person_id,
                        username,
                        user_id=user.id,
                        action="unresolved",
                        reason="Expected imported person row was not found.",
                    )
                )
                continue

            inferred_birthday = self._birthday_from_minor_expiration(legacy_row, minor_expiration)
            new_is_minor = minor_expiration >= MINOR_CUTOFF
            needs_update = user.birthday != inferred_birthday or person.is_minor != new_is_minor
            rows.append(
                self._row(
                    legacy_row,
                    legacy_person_id,
                    username,
                    user_id=user.id,
                    sca_name=person.sca_name,
                    minor_expiration=minor_expiration,
                    inferred_birthday=inferred_birthday,
                    old_birthday=user.birthday,
                    old_is_minor=person.is_minor,
                    new_is_minor=new_is_minor,
                    action="update" if needs_update else "no_change",
                    reason="",
                )
            )
        return rows

    def _apply_rows(self, rows):
        update_rows = [row for row in rows if row.get("action") == "update"]
        unresolved_rows = [row for row in rows if row.get("action") == "unresolved"]
        if unresolved_rows:
            raise CommandError(f"Input contains {len(unresolved_rows)} unresolved row(s). Resolve them before applying.")

        with transaction.atomic(using=self.target_alias):
            for row in update_rows:
                user_id = int(row["user_id"])
                birthday = self._date_or_none(row["new_birthday"])
                new_is_minor = self._bool(row["new_is_minor"])
                User.objects.using(self.target_alias).filter(pk=user_id).update(birthday=birthday)
                Person.objects.using(self.target_alias).filter(user_id=user_id).update(is_minor=new_is_minor)

        self.stdout.write(self.style.SUCCESS(f"Applied {len(update_rows)} birthday/minor cleanup row(s) to {self.target_alias}."))

    def _row(
        self,
        legacy_row,
        legacy_person_id,
        username,
        *,
        user_id="",
        sca_name="",
        minor_expiration=None,
        inferred_birthday=None,
        old_birthday=None,
        old_is_minor="",
        new_is_minor="",
        action,
        reason,
    ):
        return {
            "action": action,
            "legacy_person_id": legacy_person_id,
            "user_id": user_id,
            "username": username,
            "sca_name": sca_name,
            "legacy_sca_name": legacy_row["SCAName"],
            "legacy_legal_name": legacy_row["LegalName"],
            "legacy_country": legacy_row["Country"],
            "legacy_state": legacy_row["State"],
            "minor_expiration": self._date_string(minor_expiration or self._date_or_none(legacy_row["MinorExpDate"])),
            "new_birthday": self._date_string(inferred_birthday),
            "old_birthday": self._date_string(old_birthday),
            "old_is_minor": old_is_minor,
            "new_is_minor": new_is_minor,
            "canada_rule": self._lives_in_canada(legacy_row),
            "reason": reason,
        }

    def _legacy_people(self):
        rows = self._fetch_legacy(
            "SELECT ID, LegalName, SCAName, State, Country, Email, CAST(MinorExpDate AS CHAR) AS MinorExpDate "
            "FROM people ORDER BY ID"
        )
        return {row["ID"]: row for row in rows}

    def _person_actions(self):
        if not self.duplicate_actions_file.exists():
            raise CommandError(f"Duplicate actions file not found: {self.duplicate_actions_file}")
        return {row["legacy_person_id"]: row for row in self._read_csv(self.duplicate_actions_file)}

    def _canonical_legacy_ids(self, legacy_people, person_actions):
        excluded_avacal = self._legacy_id_set(
            "SELECT p.ID FROM people p LEFT JOIN branches b ON b.ID=p.BranchID "
            "LEFT JOIN regions r ON r.ID=b.RegionID WHERE b.Name='Avacal' OR r.Name='Avacal'"
        )
        canonical_ids = set()
        for person_id in legacy_people:
            if person_id in excluded_avacal:
                continue
            action = person_actions.get(str(person_id), {})
            if action.get("import_action") == "exclude_avacal":
                continue
            canonical_id = int(action.get("map_to_id") or person_id)
            if canonical_id == person_id:
                canonical_ids.add(person_id)
        return canonical_ids

    def _expected_usernames(self, legacy_people, canonical_ids):
        used_usernames = {"antir.authorization.database@gmail.com"}
        used_identity_keys = set()
        usernames = {}
        for person_id in sorted(canonical_ids):
            row = legacy_people[person_id]
            username = self._username_for(row, used_usernames)
            used_usernames.add(username)
            first_name, last_name = self._split_legal_name(row["LegalName"])
            email = (row["Email"] or "").strip() or f"legacy-import+{person_id}@invalid.local"
            identity_key = (first_name.casefold(), last_name.casefold(), email.casefold())
            if identity_key in used_identity_keys:
                email = f"legacy-duplicate-email+{person_id}@invalid.local"
            used_identity_keys.add((first_name.casefold(), last_name.casefold(), email.casefold()))
            usernames[person_id] = username
        return usernames

    def _username_for(self, row, used_usernames):
        email = (row["Email"] or "").strip().lower()
        if email and email not in used_usernames:
            return email
        base = self._normalize_username(row["SCAName"])
        if not base:
            base = f"legacy.{row['ID']}"
        if base not in used_usernames:
            return base
        return f"{base[:130]}.{row['ID']}"

    def _normalize_username(self, value):
        return re.sub(r"[^a-z0-9]+", ".", str(value or "").lower()).strip(".")[:140]

    def _split_legal_name(self, value):
        parts = str(value or "").strip().split()
        if not parts:
            return "Legacy", "Fighter"
        if len(parts) == 1:
            return parts[0], "Fighter"
        return parts[0], " ".join(parts[1:])

    def _birthday_from_minor_expiration(self, row, minor_expiration):
        return minor_expiration - relativedelta(years=19 if self._lives_in_canada(row) else 18)

    def _lives_in_canada(self, row):
        country = str(row.get("Country") or "").strip().casefold()
        state = str(row.get("State") or "").strip().upper().replace(".", "")
        return "canada" in country or state in CANADA_PROVINCES

    def _fetch_legacy(self, sql):
        with connections[self.legacy_alias].cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _legacy_id_set(self, sql):
        with connections[self.legacy_alias].cursor() as cursor:
            cursor.execute(sql)
            return {row[0] for row in cursor.fetchall()}

    def _read_csv(self, path):
        with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            return list(csv.DictReader(csv_file))

    def _write_csv(self, path, rows):
        fieldnames = [
            "action",
            "legacy_person_id",
            "user_id",
            "username",
            "sca_name",
            "legacy_sca_name",
            "legacy_legal_name",
            "legacy_country",
            "legacy_state",
            "minor_expiration",
            "new_birthday",
            "old_birthday",
            "old_is_minor",
            "new_is_minor",
            "canada_rule",
            "reason",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _date_or_none(self, value):
        value = str(value or "").strip()
        if not value or value == ZERO_DATE or value.lower() == "none":
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    def _date_string(self, value):
        return value.isoformat() if value else ""

    def _bool(self, value):
        return str(value).strip().casefold() in {"1", "true", "yes", "y"}
