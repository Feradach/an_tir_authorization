import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from authorizations.addressing import (
    normalize_postal_code,
    validate_postal_code_for_state,
)
from authorizations.models import User


class PostalCodeRulesTests(SimpleTestCase):
    def test_normalizes_canadian_spacing_and_capitalization(self):
        self.assertEqual(normalize_postal_code(" v8v  1a1 "), "V8V 1A1")
        self.assertEqual(normalize_postal_code("v8v-1a1"), "V8V 1A1")

    def test_normalizes_optional_zip_plus_four(self):
        self.assertEqual(normalize_postal_code("972011234"), "97201-1234")

    def test_five_digit_zip_is_valid_and_zip_plus_four_is_not_required(self):
        self.assertEqual(
            validate_postal_code_for_state("97201", "Oregon"),
            "97201",
        )

    def test_rejects_blank_and_state_jurisdiction_mismatch(self):
        with self.assertRaises(ValidationError):
            normalize_postal_code("")
        with self.assertRaises(ValidationError):
            validate_postal_code_for_state("97201", "British Columbia")
        with self.assertRaises(ValidationError):
            validate_postal_code_for_state("97201", "Washington")


class PostalCodeManagementCommandTests(TestCase):
    def make_user(self, username, **overrides):
        values = {
            "email": f"{username}@example.com",
            "address": "123 Main St",
            "address2": "",
            "city": "Portland",
            "state_province": "Oregon",
            "postal_code": "",
            "country": "United States",
        }
        values.update(overrides)
        return User.objects.create_user(
            username=username,
            password="StrongPass!123",
            **values,
        )

    def test_export_splits_us_canadian_and_unknown_and_writes_census_file(self):
        us_user = self.make_user("missing_us")
        bc_user = self.make_user(
            "missing_bc",
            city="Victoria",
            state_province="British Columbia",
            country="Canada",
        )
        unknown_user = self.make_user("missing_unknown", state_province="")
        self.make_user("has_postal", postal_code="97201")

        with TemporaryDirectory() as temp_dir:
            call_command("export_missing_postal_codes", "--output-dir", temp_dir)

            us_rows = self.read_dict_rows(Path(temp_dir) / "missing_postal_us_review.csv")
            canada_rows = self.read_dict_rows(
                Path(temp_dir) / "missing_postal_canada_review.csv"
            )
            unknown_rows = self.read_dict_rows(
                Path(temp_dir) / "missing_postal_unknown_review.csv"
            )
            with (Path(temp_dir) / "missing_postal_us_census.csv").open(
                newline="", encoding="utf-8"
            ) as source:
                census_rows = list(csv.reader(source))

        self.assertEqual([int(row["user_id"]) for row in us_rows], [us_user.id])
        self.assertEqual([int(row["user_id"]) for row in canada_rows], [bc_user.id])
        self.assertEqual([int(row["user_id"]) for row in unknown_rows], [unknown_user.id])
        self.assertEqual(
            census_rows,
            [[str(us_user.id), "123 Main St", "Portland", "OR", ""]],
        )

    def test_normalizer_is_dry_run_by_default_then_applies(self):
        user = self.make_user(
            "normalize_bc",
            city="Victoria",
            state_province="British Columbia",
            postal_code="v8v  1a1",
            country="Canada",
        )

        call_command("normalize_postal_codes")
        user.refresh_from_db()
        self.assertEqual(user.postal_code, "v8v  1a1")

        call_command("normalize_postal_codes", "--apply")
        user.refresh_from_db()
        self.assertEqual(user.postal_code, "V8V 1A1")

    def test_review_import_is_dry_run_then_fills_blank(self):
        user = self.make_user("review_import")

        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "review.csv"
            self.write_review_csv(csv_path, user, "97201")

            call_command("import_postal_codes", str(csv_path))
            user.refresh_from_db()
            self.assertEqual(user.postal_code, "")

            call_command("import_postal_codes", str(csv_path), "--apply")

        user.refresh_from_db()
        self.assertEqual(user.postal_code, "97201")

    def test_review_import_accepts_exported_normalized_state_name(self):
        user = self.make_user("review_abbreviated_state", state_province="OR")

        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "review.csv"
            row = self.review_row(user, "97201")
            row["state_province"] = "Oregon"
            with csv_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)

            call_command("import_postal_codes", str(csv_path), "--apply")

        user.refresh_from_db()
        self.assertEqual(user.postal_code, "97201")

    def test_review_import_rejects_stale_address_without_partial_write(self):
        first = self.make_user("stale_first")
        second = self.make_user("stale_second", address="456 Oak St")

        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "review.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "user_id",
                        "address",
                        "address2",
                        "city",
                        "state_province",
                        "postal_code",
                    ],
                )
                writer.writeheader()
                writer.writerow(self.review_row(first, "97201"))
                stale_row = self.review_row(second, "97202")
                stale_row["address"] = "Old Address"
                writer.writerow(stale_row)

            with self.assertRaises(CommandError):
                call_command("import_postal_codes", str(csv_path), "--apply")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.postal_code, "")
        self.assertEqual(second.postal_code, "")

    def test_census_result_import_uses_only_matched_rows(self):
        matched = self.make_user("census_match")
        unmatched = self.make_user("census_no_match")

        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "census.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.writer(output)
                writer.writerow(
                    [
                        matched.id,
                        "123 Main St, Portland, OR,",
                        "Match",
                        "Exact",
                        "123 MAIN ST, PORTLAND, OR, 97201",
                        "",
                        "",
                        "",
                    ]
                )
                writer.writerow(
                    [
                        unmatched.id,
                        "Bad Address, Portland, OR,",
                        "No_Match",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ]
                )

            call_command(
                "import_postal_codes",
                str(csv_path),
                "--format",
                "census",
                "--apply",
            )

        matched.refresh_from_db()
        unmatched.refresh_from_db()
        self.assertEqual(matched.postal_code, "97201")
        self.assertEqual(unmatched.postal_code, "")

    def review_row(self, user, postal_code):
        return {
            "user_id": user.id,
            "address": user.address or "",
            "address2": user.address2 or "",
            "city": user.city or "",
            "state_province": user.state_province or "",
            "postal_code": postal_code,
        }

    def write_review_csv(self, path, user, postal_code):
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "user_id",
                    "address",
                    "address2",
                    "city",
                    "state_province",
                    "postal_code",
                ],
            )
            writer.writeheader()
            writer.writerow(self.review_row(user, postal_code))

    def read_dict_rows(self, path):
        with path.open(newline="", encoding="utf-8-sig") as source:
            return list(csv.DictReader(source))
