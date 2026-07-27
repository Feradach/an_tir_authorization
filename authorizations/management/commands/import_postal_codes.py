import csv
import re
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.addressing import normalize_state_province, validate_postal_code_for_state
from authorizations.models import User


REQUIRED_REVIEW_COLUMNS = {
    "user_id",
    "address",
    "address2",
    "city",
    "state_province",
    "postal_code",
}
POSTAL_CODE_AT_END_RE = re.compile(r"\b(\d{5}(?:-\d{4})?)\s*$")


class Command(BaseCommand):
    help = "Fill blank postal codes from a reviewed export or US Census batch result; dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", help="Reviewed CSV or Census batch result to import.")
        parser.add_argument(
            "--format",
            choices=("review", "census"),
            default="review",
            help="Input format. The default is an edited *_review.csv export.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply all validated blank-postal-code updates.",
        )

    def handle(self, *args, **options):
        input_path = Path(options["csv_file"])
        if not input_path.is_file():
            raise CommandError(f"CSV file does not exist: {input_path}")

        if options["format"] == "census":
            candidates, input_errors = self._read_census(input_path)
        else:
            candidates, input_errors = self._read_review(input_path)

        updates = []
        errors = list(input_errors)
        skipped = []
        seen_user_ids = set()

        for row_number, candidate in candidates:
            raw_user_id = str(candidate.get("user_id") or "").strip()
            try:
                user_id = int(raw_user_id)
            except ValueError:
                errors.append(f"Row {row_number}: invalid user_id {raw_user_id!r}.")
                continue
            if user_id in seen_user_ids:
                errors.append(f"Row {row_number}: duplicate user_id {user_id}.")
                continue
            seen_user_ids.add(user_id)

            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                errors.append(f"Row {row_number}: user_id {user_id} was not found.")
                continue

            current_postal_code = str(user.postal_code or "").strip()
            if current_postal_code:
                skipped.append(
                    f'user_id={user_id}: already has postal code "{current_postal_code}"'
                )
                continue

            if options["format"] == "review":
                stale_fields = self._stale_review_fields(user, candidate)
                if stale_fields:
                    errors.append(
                        f"Row {row_number}: user_id {user_id} changed since export "
                        f"({', '.join(stale_fields)})."
                    )
                    continue

            raw_postal_code = str(candidate.get("postal_code") or "").strip()
            if not raw_postal_code:
                skipped.append(f"user_id={user_id}: no postal code supplied")
                continue
            try:
                postal_code = validate_postal_code_for_state(
                    raw_postal_code,
                    user.state_province,
                )
            except ValidationError as exc:
                errors.append(
                    f"Row {row_number}: user_id {user_id}: {' '.join(exc.messages)}"
                )
                continue
            updates.append((user_id, postal_code))

        self.stdout.write(
            f"Postal-code import ({'APPLY' if options['apply'] else 'DRY RUN'})"
        )
        for user_id, postal_code in updates:
            self.stdout.write(f'user_id={user_id}: blank -> "{postal_code}"')
        for message in skipped:
            self.stdout.write(f"SKIP: {message}")
        for message in errors:
            self.stderr.write(f"ERROR: {message}")

        self.stdout.write("")
        self.stdout.write(f"Validated updates: {len(updates)}")
        self.stdout.write(f"Skipped rows: {len(skipped)}")
        self.stdout.write(f"Errors: {len(errors)}")

        if errors:
            raise CommandError("No changes applied because the input contains errors.")

        if options["apply"]:
            with transaction.atomic():
                for user_id, postal_code in updates:
                    user = User.objects.select_for_update().get(id=user_id)
                    if str(user.postal_code or "").strip():
                        raise CommandError(
                            f"user_id {user_id} changed after validation; transaction rolled back."
                        )
                    user.postal_code = postal_code
                    user.save(update_fields=["postal_code", "updated_at"])
            self.stdout.write(f"Database rows changed: {len(updates)}")
        else:
            self.stdout.write("Database rows changed: 0")
            self.stdout.write("No database changes were applied. Re-run with --apply after review.")

    def _read_review(self, path):
        with path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            missing = REQUIRED_REVIEW_COLUMNS - set(reader.fieldnames or [])
            if missing:
                return [], [
                    "Missing required review CSV columns: " + ", ".join(sorted(missing))
                ]
            return list(enumerate(reader, start=2)), []

    def _read_census(self, path):
        candidates = []
        errors = []
        with path.open(newline="", encoding="utf-8-sig") as source:
            for row_number, row in enumerate(csv.reader(source), start=1):
                if not row or not any(value.strip() for value in row):
                    continue
                if len(row) < 5:
                    errors.append(
                        f"Row {row_number}: expected a Census batch result with at least five columns."
                    )
                    continue
                match_status = row[2].strip().casefold()
                matched_address = row[4].strip()
                if match_status != "match":
                    candidates.append(
                        (
                            row_number,
                            {
                                "user_id": row[0],
                                "postal_code": "",
                            },
                        )
                    )
                    continue
                postal_match = POSTAL_CODE_AT_END_RE.search(matched_address)
                if not postal_match:
                    errors.append(
                        f"Row {row_number}: matched Census address has no ending ZIP code."
                    )
                    continue
                candidates.append(
                    (
                        row_number,
                        {
                            "user_id": row[0],
                            "postal_code": postal_match.group(1),
                        },
                    )
                )
        return candidates, errors

    def _stale_review_fields(self, user, candidate):
        stale_fields = []
        for field in ("address", "address2", "city", "state_province"):
            current = str(getattr(user, field) or "").strip()
            exported = str(candidate.get(field) or "").strip()
            if field == "state_province":
                current = normalize_state_province(current)
                exported = normalize_state_province(exported)
            if current != exported:
                stale_fields.append(field)
        return stale_fields
