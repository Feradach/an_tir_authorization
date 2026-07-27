import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from authorizations.addressing import jurisdiction_for_state, normalize_state_province
from authorizations.models import User


REVIEW_FIELDS = [
    "user_id",
    "address",
    "address2",
    "city",
    "state_province",
    "postal_code",
    "lookup_status",
    "notes",
]
US_STATE_ABBREVIATIONS = {
    "Oregon": "OR",
    "Washington": "WA",
    "Idaho": "ID",
}


class Command(BaseCommand):
    help = "Export accounts with blank postal codes for US, Canadian, or manual lookup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            required=True,
            help="Directory in which to create the lookup CSV files.",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        grouped_rows = {"us": [], "canada": [], "unknown": []}
        for user in User.objects.order_by("id"):
            if str(user.postal_code or "").strip():
                continue
            state_province = normalize_state_province(user.state_province)
            jurisdiction = jurisdiction_for_state(state_province)
            group = {
                "United States": "us",
                "Canada": "canada",
            }.get(jurisdiction, "unknown")
            grouped_rows[group].append(
                {
                    "user_id": user.id,
                    "address": user.address or "",
                    "address2": user.address2 or "",
                    "city": user.city or "",
                    "state_province": state_province,
                    "postal_code": "",
                    "lookup_status": "",
                    "notes": "",
                }
            )

        for group, rows in grouped_rows.items():
            self._write_review_csv(output_dir / f"missing_postal_{group}_review.csv", rows)

        census_path = output_dir / "missing_postal_us_census.csv"
        self._write_census_csv(census_path, grouped_rows["us"])

        self.stdout.write("Missing postal-code export (read-only database operation)")
        self.stdout.write(f"US review rows: {len(grouped_rows['us'])}")
        self.stdout.write(f"Canadian review rows: {len(grouped_rows['canada'])}")
        self.stdout.write(f"Unknown-jurisdiction review rows: {len(grouped_rows['unknown'])}")
        self.stdout.write(f"Output directory: {output_dir}")
        self.stdout.write("No database changes were applied.")

    def _write_review_csv(self, path, rows):
        with path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_census_csv(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            for row in rows:
                street = " ".join(
                    part.strip()
                    for part in [row["address"], row["address2"]]
                    if part and part.strip()
                )
                writer.writerow(
                    [
                        row["user_id"],
                        street,
                        row["city"],
                        US_STATE_ABBREVIATIONS.get(row["state_province"], ""),
                        "",
                    ]
                )
