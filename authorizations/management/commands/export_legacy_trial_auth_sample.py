import csv
import random
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from authorizations.models import Authorization, AuthorizationNote


LEGACY_AUTH_ID_RE = re.compile(r"Legacy authorization ID:\s*(\d+)")


class Command(BaseCommand):
    help = "Export paired random authorization samples from legacy source and trial target for manual comparison."

    def add_arguments(self, parser):
        parser.add_argument("--source-db", default="legacy", help="Read-only legacy source database alias.")
        parser.add_argument("--target-db", default="trial", help="Trial target database alias.")
        parser.add_argument("--count", type=int, default=100, help="Number of imported authorizations to sample.")
        parser.add_argument("--seed", type=int, default=None, help="Optional random seed for repeatable samples.")
        parser.add_argument(
            "--output-dir",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_trial_sample"),
            help="Directory where sample CSV files will be written.",
        )

    def handle(self, *args, **options):
        source_alias = options["source_db"]
        target_alias = options["target_db"]
        count = options["count"]
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        if source_alias not in connections:
            raise CommandError(f'Source database alias "{source_alias}" is not configured.')
        if target_alias not in connections:
            raise CommandError(f'Target database alias "{target_alias}" is not configured.')
        if source_alias == target_alias:
            raise CommandError("Source and target aliases must be different.")
        if count < 1:
            raise CommandError("--count must be positive.")

        source_name = connections.databases[source_alias].get("NAME")
        target_name = connections.databases[target_alias].get("NAME")
        if source_name == target_name:
            raise CommandError("Source and target point to the same database.")

        imported_pairs = self._imported_auth_pairs(target_alias)
        if not imported_pairs:
            raise CommandError("No imported trial authorizations with legacy authorization notes were found.")

        rng = random.Random(options["seed"])
        sample_size = min(count, len(imported_pairs))
        sample = rng.sample(imported_pairs, sample_size)
        sample.sort(key=lambda row: row["sample_number"])

        legacy_rows = self._legacy_rows(source_alias, sample)
        trial_rows = self._trial_rows(target_alias, sample)

        legacy_path = output_dir / "legacy_authorization_sample.csv"
        trial_path = output_dir / "trial_authorization_sample.csv"
        self._write_csv(legacy_path, self._fieldnames(), legacy_rows)
        self._write_csv(trial_path, self._fieldnames(), trial_rows)

        self.stdout.write(self.style.SUCCESS("Authorization comparison samples exported."))
        self.stdout.write(str(legacy_path))
        self.stdout.write(str(trial_path))
        self.stdout.write(f"Rows: {sample_size}")

    def _imported_auth_pairs(self, target_alias):
        notes = (
            AuthorizationNote.objects.using(target_alias)
            .filter(note__contains="Legacy authorization ID:")
            .values_list("authorization_id", "note")
            .order_by("authorization_id")
        )
        pairs = []
        for index, (authorization_id, note) in enumerate(notes, start=1):
            match = LEGACY_AUTH_ID_RE.search(note or "")
            if not match:
                continue
            pairs.append(
                {
                    "sample_number": index,
                    "current_auth_id": authorization_id,
                    "legacy_auth_id": int(match.group(1)),
                }
            )
        return pairs

    def _legacy_rows(self, source_alias, sample):
        sample_by_legacy_id = {row["legacy_auth_id"]: row for row in sample}
        legacy_ids = sorted(sample_by_legacy_id.keys())
        placeholders = ",".join(["%s"] * len(legacy_ids))
        sql = (
            "SELECT a.ID AS legacy_auth_id, a.PersonID, p.SCAName, p.LegalName, "
            "p.Address1, p.Address2, p.City, p.State, p.PostCode, p.Country, p.MembershipNum, "
            "ad.Name AS DisciplineName, w.Name AS WeaponFormName, mp.SCAName AS MarshalName, a.ExpiresOn "
            "FROM authorizations a "
            "LEFT JOIN people p ON p.ID = a.PersonID "
            "LEFT JOIN disciplines ad ON ad.ID = a.DisciplineID "
            "LEFT JOIN weaponform w ON w.ID = a.WeaponFormID "
            "LEFT JOIN people mp ON mp.ID = a.AuthorizingMarshalID "
            f"WHERE a.ID IN ({placeholders})"
        )
        with connections[source_alias].cursor() as cursor:
            cursor.execute(sql, legacy_ids)
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        exported = []
        for row in rows:
            sample_row = sample_by_legacy_id[row["legacy_auth_id"]]
            exported.append(
                {
                    "sample_number": sample_row["sample_number"],
                    "legacy_auth_id": row["legacy_auth_id"],
                    "current_auth_id": sample_row["current_auth_id"],
                    "user_person_id": row["PersonID"],
                    "sca_name": row["SCAName"] or "",
                    "legal_name": row["LegalName"] or "",
                    "address": self._join_address(
                        row["Address1"],
                        row["Address2"],
                        row["City"],
                        row["State"],
                        row["PostCode"],
                        row["Country"],
                    ),
                    "membership_number": "" if row["MembershipNum"] in (None, 0) else row["MembershipNum"],
                    "authorization_discipline": row["DisciplineName"] or "",
                    "authorization_weaponstyle": row["WeaponFormName"] or "",
                    "authorizing_marshal": row["MarshalName"] or "",
                    "authorization_expiration_date": self._date_string(row["ExpiresOn"]),
                }
            )
        return sorted(exported, key=lambda row: row["sample_number"])

    def _trial_rows(self, target_alias, sample):
        sample_by_current_id = {row["current_auth_id"]: row for row in sample}
        auths = (
            Authorization.objects.using(target_alias)
            .select_related("person__user", "style__discipline", "marshal")
            .filter(id__in=sample_by_current_id.keys())
            .order_by("id")
        )
        exported = []
        for auth in auths:
            sample_row = sample_by_current_id[auth.id]
            user = auth.person.user
            exported.append(
                {
                    "sample_number": sample_row["sample_number"],
                    "legacy_auth_id": sample_row["legacy_auth_id"],
                    "current_auth_id": auth.id,
                    "user_person_id": auth.person_id,
                    "sca_name": auth.person.sca_name or "",
                    "legal_name": " ".join(part for part in [user.first_name, user.last_name] if part),
                    "address": self._join_address(
                        user.address,
                        user.address2,
                        user.city,
                        user.state_province,
                        user.postal_code,
                        user.country,
                    ),
                    "membership_number": user.membership or "",
                    "authorization_discipline": auth.style.discipline.name if auth.style else "",
                    "authorization_weaponstyle": auth.style.name if auth.style else "",
                    "authorizing_marshal": auth.marshal.sca_name if auth.marshal else "",
                    "authorization_expiration_date": self._date_string(auth.expiration),
                }
            )
        return sorted(exported, key=lambda row: row["sample_number"])

    def _fieldnames(self):
        return [
            "sample_number",
            "legacy_auth_id",
            "current_auth_id",
            "user_person_id",
            "sca_name",
            "legal_name",
            "address",
            "membership_number",
            "authorization_discipline",
            "authorization_weaponstyle",
            "authorizing_marshal",
            "authorization_expiration_date",
        ]

    def _join_address(self, *parts):
        return ", ".join(str(part).strip() for part in parts if str(part or "").strip())

    def _date_string(self, value):
        return value.isoformat() if hasattr(value, "isoformat") else str(value or "")

    def _write_csv(self, path, fieldnames, rows):
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
