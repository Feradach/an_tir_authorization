import csv
import re
from collections import Counter
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from authorizations.models import User, is_minor_from_birthday


STATE_PROVINCE_NORMALIZATION = {
    "OR": "Oregon",
    "OREGON": "Oregon",
    "WA": "Washington",
    "WASHINGTON": "Washington",
    "ID": "Idaho",
    "IDAHO": "Idaho",
    "BC": "British Columbia",
    "B.C.": "British Columbia",
    "BRITISH COLUMBIA": "British Columbia",
}
COUNTRY_NORMALIZATION = {
    "USA": "United States",
    "US": "United States",
    "U.S.": "United States",
    "UNITED STATES": "United States",
    "UNITED STATES OF AMERICA": "United States",
    "CAN": "Canada",
    "CA": "Canada",
    "CANADA": "Canada",
}
US_STATES = {"Oregon", "Washington", "Idaho"}
CANADIAN_PROVINCE = "British Columbia"
US_POSTAL_CODE_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
CANADIAN_POSTAL_CODE_RE = re.compile(r"^[A-Z]\d[A-Z][ -]?\d[A-Z]\d$")
US_AN_TIR_POSTAL_PREFIXES = ("97", "98", "990", "991", "992", "993", "994", "835", "838")

ISSUE_ORDER = [
    "missing_state_province",
    "unsupported_state_province",
    "missing_postal_code",
    "invalid_postal_code_format",
    "postal_code_outside_an_tir",
    "state_postal_jurisdiction_mismatch",
    "unrecognized_country",
    "country_state_jurisdiction_mismatch",
    "minor_status_would_change",
]


def normalize_state_province(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return STATE_PROVINCE_NORMALIZATION.get(raw.upper(), raw.title())


def normalize_country(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return COUNTRY_NORMALIZATION.get(raw.upper(), "")


def jurisdiction_for_state(state_province):
    if state_province == CANADIAN_PROVINCE:
        return "Canada"
    if state_province in US_STATES:
        return "United States"
    return ""


def postal_code_details(value):
    postal_code = str(value or "").strip().upper()
    if not postal_code:
        return "", "", False
    if US_POSTAL_CODE_RE.fullmatch(postal_code):
        within_an_tir = postal_code.startswith(US_AN_TIR_POSTAL_PREFIXES)
        return postal_code, "United States", within_an_tir
    if CANADIAN_POSTAL_CODE_RE.fullmatch(postal_code):
        return postal_code, "Canada", postal_code.startswith("V")
    return postal_code, "", False


class Command(BaseCommand):
    help = (
        "Read-only audit of state/province, postal code, stored country, and minor-status data "
        "before removing the country field."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-csv",
            help="Optional path for a CSV containing every account with one or more issues.",
        )
        parser.add_argument(
            "--fail-on-issues",
            action="store_true",
            help="Exit with an error after printing the report when any issues are found.",
        )

    def handle(self, *args, **options):
        today = date.today()
        rows = []
        counts = Counter()
        users = User.objects.select_related("merged_into").order_by("id")

        for user in users:
            issues = self._issues_for_user(user, today=today)
            if not issues:
                continue
            counts.update(issues)
            rows.append(self._row_for_user(user, issues))

        self.stdout.write("Address jurisdiction audit (read-only)")
        self.stdout.write("")
        self.stdout.write(f"Accounts scanned: {users.count()}")
        self.stdout.write(f"Accounts with issues: {len(rows)}")
        for issue in ISSUE_ORDER:
            self.stdout.write(f"{issue}: {counts[issue]}")

        if rows:
            self.stdout.write("")
            self.stdout.write("Records:")
            for row in rows:
                self.stdout.write(
                    '- user_id={user_id}, username="{username}", account_status={account_status}, '
                    'state_province="{state_province}", postal_code="{postal_code}", '
                    'country="{country}", birthday={birthday}: {issues}'.format(**row)
                )

        output_csv = options.get("output_csv")
        if output_csv:
            self._write_csv(Path(output_csv), rows)

        self.stdout.write("")
        self.stdout.write("No database changes were applied.")

        if rows and options["fail_on_issues"]:
            raise CommandError(f"Address jurisdiction audit found {len(rows)} account(s) with issues.")

    def _issues_for_user(self, user, *, today):
        issues = []
        state_province = normalize_state_province(user.state_province)
        state_jurisdiction = jurisdiction_for_state(state_province)
        postal_code, postal_jurisdiction, postal_within_an_tir = postal_code_details(user.postal_code)
        raw_country = str(user.country or "").strip()
        stored_country = normalize_country(raw_country)

        if not state_province:
            issues.append("missing_state_province")
        elif not state_jurisdiction:
            issues.append("unsupported_state_province")

        if not postal_code:
            issues.append("missing_postal_code")
        elif not postal_jurisdiction:
            issues.append("invalid_postal_code_format")
        else:
            if not postal_within_an_tir:
                issues.append("postal_code_outside_an_tir")
            if state_jurisdiction and postal_jurisdiction != state_jurisdiction:
                issues.append("state_postal_jurisdiction_mismatch")

        if raw_country and not stored_country:
            issues.append("unrecognized_country")
        elif stored_country and state_jurisdiction and stored_country != state_jurisdiction:
            issues.append("country_state_jurisdiction_mismatch")

        if user.birthday and state_jurisdiction:
            current_minor = is_minor_from_birthday(
                user.birthday,
                user.country,
                user.state_province,
                today=today,
            )
            inferred_minor = is_minor_from_birthday(
                user.birthday,
                "",
                state_province,
                today=today,
            )
            if current_minor != inferred_minor:
                issues.append("minor_status_would_change")

        return issues

    def _row_for_user(self, user, issues):
        if user.merged_into_id:
            account_status = f"merged_into_{user.merged_into_id}"
        elif user.is_active:
            account_status = "active"
        else:
            account_status = "inactive"
        return {
            "user_id": user.id,
            "username": user.username or "",
            "account_status": account_status,
            "state_province": user.state_province or "",
            "postal_code": user.postal_code or "",
            "country": user.country or "",
            "birthday": user.birthday.isoformat() if user.birthday else "",
            "issues": ",".join(issues),
        }

    def _write_csv(self, output_path, rows):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "user_id",
            "username",
            "account_status",
            "state_province",
            "postal_code",
            "country",
            "birthday",
            "issues",
        ]
        with output_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.stdout.write(f"CSV report: {output_path}")
