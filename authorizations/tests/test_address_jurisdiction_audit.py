from datetime import date
from io import StringIO

from dateutil.relativedelta import relativedelta
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from authorizations.models import User


class AddressJurisdictionAuditCommandTests(TestCase):
    def make_user(self, username, **overrides):
        values = {
            "email": f"{username}@example.com",
            "state_province": "Oregon",
            "postal_code": "97201",
            "country": "United States",
        }
        values.update(overrides)
        return User.objects.create_user(username=username, password="StrongPass!123", **values)

    def test_clean_address_reports_no_issues(self):
        self.make_user("clean_address")
        output = StringIO()

        call_command("audit_address_jurisdictions", stdout=output)

        report = output.getvalue()
        self.assertIn("Accounts scanned: 1", report)
        self.assertIn("Accounts with issues: 0", report)
        self.assertIn("No database changes were applied.", report)

    def test_british_columbia_address_is_valid_without_stored_country(self):
        self.make_user(
            "clean_bc_address",
            state_province="British Columbia",
            postal_code="V8V 1A1",
            country="",
            birthday=date.today() - relativedelta(years=18, months=6),
        )
        output = StringIO()

        call_command("audit_address_jurisdictions", stdout=output)

        self.assertIn("Accounts with issues: 0", output.getvalue())

    def test_audit_reports_country_state_conflict_that_changes_minor_status(self):
        birthday = date.today() - relativedelta(years=18, months=6)
        user = self.make_user(
            "minor_conflict",
            state_province="Oregon",
            postal_code="97201",
            country="Canada",
            birthday=birthday,
        )
        output = StringIO()

        call_command("audit_address_jurisdictions", stdout=output)

        report = output.getvalue()
        self.assertIn("country_state_jurisdiction_mismatch: 1", report)
        self.assertIn("minor_status_would_change: 1", report)
        self.assertIn(f"user_id={user.id}", report)
        user.refresh_from_db()
        self.assertEqual(user.country, "Canada")
        self.assertEqual(user.state_province, "Oregon")
        self.assertEqual(user.postal_code, "97201")

    def test_audit_reports_state_postal_jurisdiction_mismatch(self):
        self.make_user(
            "bc_us_postal",
            state_province="British Columbia",
            postal_code="97201",
            country="Canada",
        )
        output = StringIO()

        call_command("audit_address_jurisdictions", stdout=output)

        self.assertIn("state_postal_jurisdiction_mismatch: 1", output.getvalue())

    def test_fail_on_issues_raises_after_printing_report(self):
        self.make_user("bad_state", state_province="", postal_code="")
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_address_jurisdictions", "--fail-on-issues", stdout=output)

        self.assertIn("missing_state_province: 1", output.getvalue())
        self.assertIn("missing_postal_code: 1", output.getvalue())
