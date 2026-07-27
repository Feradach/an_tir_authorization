import csv
from collections import Counter
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from authorizations.addressing import (
    jurisdiction_for_state,
    normalize_country,
    normalize_postal_code,
    normalize_state_province,
    postal_code_jurisdiction,
    postal_code_matches_state,
    postal_code_within_an_tir,
)
from authorizations.models import User, is_minor_from_birthday


ISSUE_ORDER = [
    "missing_state_province",
    "unsupported_state_province",
    "missing_postal_code",
    "postal_code_needs_normalization",
    "invalid_postal_code_format",
    "postal_code_outside_an_tir",
    "state_postal_jurisdiction_mismatch",
    "unrecognized_country",
    "country_state_jurisdiction_mismatch",
    "minor_status_would_change",
]

def postal_code_details(value):
    raw_postal_code = str(value or "").strip()
    if not raw_postal_code:
        return "", "", False
    try:
        postal_code = normalize_postal_code(raw_postal_code)
    except ValidationError:
        return raw_postal_code.upper(), "", False
    return (
        postal_code,
        postal_code_jurisdiction(postal_code),
        postal_code_within_an_tir(postal_code),
    )


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
            if postal_code != str(user.postal_code or ""):
                issues.append("postal_code_needs_normalization")
            if not postal_within_an_tir:
                issues.append("postal_code_outside_an_tir")
            if state_jurisdiction and not postal_code_matches_state(
                postal_code,
                state_province,
            ):
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
