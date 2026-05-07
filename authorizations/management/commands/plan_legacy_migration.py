import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from authorizations.models import AuthorizationStatus, Branch, Discipline, Title, WeaponStyle


ZERO_DATE = "0000-00-00"
SANCTION_TERMS = ["sanction", "suspend", "revok", "ban"]
ACTIVE_REVIEW_DATE = date(2026, 5, 1)

DISCIPLINE_ALIASES = {
    "armored combat": "Armored Combat",
    "cut & thrust": "Cut & Thrust",
    "equestrian": "Equestrian",
    "missile combat": "Missile Combat",
    "rapier combat": "Rapier Combat",
    "siege crew": "Siege",
    "target archery": "Target Archery",
    "thrown weapons": "Thrown Weapons",
    "youth combat": "Youth Armored",
    "youth rapier": "Youth Rapier",
}

WEAPON_STYLE_ALIASES = {
    ("Cut & Thrust", "Cut & Thrust"): "Single Sword w/Secondaries",
    ("Cut & Thrust", "Single Sword"): "Single Sword",
    ("Cut & Thrust", "Two Handed Sword"): "Two Handed Sword",
    ("Cut & Thrust", "Spear"): "Spear",
    ("Equestrian", "Ground Crew - Junior"): "Ground Crew - Junior",
    ("Equestrian", "Ground Crew - Senior"): "Ground Crew - Senior",
    ("Equestrian", "Mounted Heavy Combat"): "Mounted Heavy Combat",
    ("Rapier Combat", "Case (2 Rapiers)"): "Case",
    ("Youth Combat", "YC Two-Handed"): "Two-Handed",
    ("Youth Combat", "YC Weapon & Shield"): "Weapon & Shield",
}

IGNORE_WEAPON_FORMS = {"Non Fighting Jr.", "Non Fighting Sr."}
EXCLUDED_LEGACY_WEAPON_FORMS = {"Experimental Weapon"}
EXCLUDED_STANDALONE_DISCIPLINES = {"Target Archery", "Thrown Weapons"}


class Command(BaseCommand):
    help = "Generate read-only CSV reports for planning the legacy database migration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_migration_plan"),
            help="Directory where CSV reports will be written.",
        )
        parser.add_argument(
            "--legacy-db",
            default="legacy",
            help="Django database alias for the legacy database.",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        legacy_alias = options["legacy_db"]
        if legacy_alias not in connections:
            raise CommandError(f'Database alias "{legacy_alias}" is not configured.')

        self.legacy_cursor = connections[legacy_alias].cursor()
        self.output_dir = output_dir

        self.current_disciplines = self._current_name_map(Discipline.objects.all())
        self.current_statuses = self._current_name_map(AuthorizationStatus.objects.all())
        self.current_branches = self._current_name_map(Branch.objects.all())
        self.current_titles = self._current_name_map(Title.objects.all())
        self.current_styles = {
            (self._norm(style.discipline.name), self._norm(style.name)): style
            for style in WeaponStyle.objects.select_related("discipline")
        }

        self.legacy_disciplines = self._fetch_dicts("SELECT ID, Name FROM disciplines ORDER BY ID")
        self.legacy_weaponforms = self._fetch_dicts(
            "SELECT w.ID, w.DisciplineID, d.Name AS DisciplineName, w.Name "
            "FROM weaponform w JOIN disciplines d ON d.ID = w.DisciplineID "
            "ORDER BY w.DisciplineID, w.ID"
        )
        self.legacy_marshallevels = self._fetch_dicts(
            "SELECT m.ID, m.DisciplineID, d.Name AS DisciplineName, m.Name, m.CanAuthorize "
            "FROM marshallevel m JOIN disciplines d ON d.ID = m.DisciplineID "
            "ORDER BY m.DisciplineID, m.ID"
        )
        self.legacy_branches = self._fetch_dicts(
            "SELECT b.ID, b.Name, b.Type, b.RegionID, r.Name AS RegionName "
            "FROM branches b LEFT JOIN regions r ON r.ID = b.RegionID "
            "ORDER BY b.Name, b.ID"
        )

        self.legacy_discipline_by_id = {row["ID"]: row for row in self.legacy_disciplines}
        self.legacy_weaponform_by_id = {row["ID"]: row for row in self.legacy_weaponforms}
        self.legacy_marshallevel_by_id = {row["ID"]: row for row in self.legacy_marshallevels}

        files = [
            self._write_summary(),
            self._write_reference_mapping_review(),
            self._write_discipline_weaponform_mismatch_summary(),
            self._write_armored_youth_ambiguity_review(),
            self._write_excluded_authorization_review(),
            self._write_avacal_exclusion_review(),
            self._write_authorization_issue_review(),
            self._write_membership_duplicate_review(),
            self._write_duplicate_person_candidate_review(),
            self._write_possible_sanction_note_review(),
        ]

        self.stdout.write(self.style.SUCCESS("Legacy migration planning reports written."))
        for path in files:
            self.stdout.write(str(path))

    def _write_summary(self):
        path = self.output_dir / "summary.csv"
        rows = []
        for table in [
            "people",
            "authorizations",
            "authorization_update_log",
            "people_update_log",
            "branches",
            "regions",
            "disciplines",
            "weaponform",
            "marshallevel",
        ]:
            rows.append({"metric": f"{table}_count", "value": self._scalar(f"SELECT COUNT(*) FROM `{table}`")})

        checks = {
            "auth_missing_person": (
                "SELECT COUNT(*) FROM authorizations a LEFT JOIN people p ON a.PersonID=p.ID WHERE p.ID IS NULL"
            ),
            "auth_missing_marshal": (
                "SELECT COUNT(*) FROM authorizations a LEFT JOIN people p ON a.AuthorizingMarshalID=p.ID "
                "WHERE p.ID IS NULL"
            ),
            "auth_discipline_weaponform_mismatch": (
                "SELECT COUNT(*) FROM authorizations a JOIN weaponform w ON a.WeaponFormID=w.ID "
                "WHERE a.DisciplineID <> w.DisciplineID"
            ),
            "people_with_comments": 'SELECT COUNT(*) FROM people WHERE Comments IS NOT NULL AND TRIM(Comments) <> ""',
            "possible_sanction_comments": (
                'SELECT COUNT(*) FROM people WHERE LOWER(Comments) LIKE "%sanction%" '
                'OR LOWER(Comments) LIKE "%suspend%" OR LOWER(Comments) LIKE "%revok%" '
                'OR LOWER(Comments) LIKE "%ban%"'
            ),
            "membership_zero": "SELECT COUNT(*) FROM people WHERE MembershipNum = 0",
            "membership_null": "SELECT COUNT(*) FROM people WHERE MembershipNum IS NULL",
            "duplicate_membership_groups_excluding_zero": (
                "SELECT COUNT(*) FROM (SELECT MembershipNum FROM people "
                "WHERE MembershipNum IS NOT NULL AND MembershipNum <> 0 "
                "GROUP BY MembershipNum HAVING COUNT(*) > 1) x"
            ),
        }
        for metric, sql in checks.items():
            rows.append({"metric": metric, "value": self._scalar(sql)})

        self._write_csv(path, ["metric", "value"], rows)
        return path

    def _write_discipline_weaponform_mismatch_summary(self):
        path = self.output_dir / "discipline_weaponform_mismatch_summary.csv"
        rows = self._fetch_dicts(
            "SELECT a.DisciplineID AS auth_discipline_id, ad.Name AS auth_discipline, "
            "a.WeaponFormID AS weaponform_id, w.Name AS weaponform, "
            "w.DisciplineID AS weaponform_discipline_id, wd.Name AS weaponform_discipline, "
            "COUNT(*) AS row_count, "
            "SUM(CASE WHEN a.Status = 'Active' THEN 1 ELSE 0 END) AS active_count, "
            "SUM(CASE WHEN a.Status = 'Inactive' THEN 1 ELSE 0 END) AS inactive_count "
            "FROM authorizations a "
            "JOIN disciplines ad ON ad.ID = a.DisciplineID "
            "JOIN weaponform w ON w.ID = a.WeaponFormID "
            "JOIN disciplines wd ON wd.ID = w.DisciplineID "
            "WHERE a.DisciplineID <> w.DisciplineID "
            "GROUP BY a.DisciplineID, ad.Name, a.WeaponFormID, w.Name, w.DisciplineID, wd.Name "
            "ORDER BY row_count DESC, auth_discipline, weaponform_discipline, weaponform"
        )
        for row in rows:
            row["suggested_category"] = self._mismatch_category(row["auth_discipline"], row["weaponform_discipline"])
            row["suggested_action"] = self._mismatch_action(row["suggested_category"])
            row["notes"] = (
                "Grouped summary only. Use the row-level ambiguity/review files when the category requires person context."
            )
        self._write_csv(
            path,
            [
                "auth_discipline_id",
                "auth_discipline",
                "weaponform_id",
                "weaponform",
                "weaponform_discipline_id",
                "weaponform_discipline",
                "row_count",
                "active_count",
                "inactive_count",
                "suggested_category",
                "suggested_action",
                "notes",
            ],
            rows,
        )
        return path

    def _write_armored_youth_ambiguity_review(self):
        path = self.output_dir / "armored_youth_ambiguity_review.csv"
        rows = self._fetch_dicts(
            "SELECT a.ID AS legacy_auth_id, a.PersonID AS legacy_person_id, p.SCAName AS sca_name, "
            "p.LegalName AS legal_name, CAST(p.MinorExpDate AS CHAR) AS minor_exp_date, "
            "ad.Name AS auth_discipline, w.Name AS weaponform, wd.Name AS weaponform_discipline, "
            "a.Status AS legacy_status, a.ExpiresOn AS legacy_expires_on, m.Name AS marshal_level "
            "FROM authorizations a "
            "JOIN people p ON p.ID = a.PersonID "
            "JOIN disciplines ad ON ad.ID = a.DisciplineID "
            "JOIN weaponform w ON w.ID = a.WeaponFormID "
            "JOIN disciplines wd ON wd.ID = w.DisciplineID "
            "JOIN marshallevel m ON m.ID = a.MarshalLevelID "
            "WHERE a.DisciplineID <> w.DisciplineID "
            "AND ("
            "  (ad.Name = 'Youth Combat' AND wd.Name = 'Armored Combat') "
            "  OR (ad.Name = 'Armored Combat' AND wd.Name = 'Youth Combat')"
            ") "
            "ORDER BY p.SCAName, a.ID"
        )
        for row in rows:
            minor_exp = self._date_or_none(row["minor_exp_date"])
            auth_exp = self._date_or_none(row["legacy_expires_on"])
            estimated_issue_date = auth_exp - relativedelta(years=2) if auth_exp else None
            sixteenth_birthday = minor_exp - relativedelta(years=2) if minor_exp else None
            was_under_16_at_issue = (
                bool(estimated_issue_date and sixteenth_birthday and estimated_issue_date < sixteenth_birthday)
            )
            row["estimated_initial_authorization_date"] = self._date_string(estimated_issue_date)
            row["estimated_16th_birthday"] = self._date_string(sixteenth_birthday)
            row["minor_signal"] = "has_minor_expiration" if minor_exp else "no_minor_expiration"
            row["under_16_at_estimated_issue"] = "yes" if was_under_16_at_issue else "no"
            row["expiration_after_2026_05_01"] = "yes" if auth_exp and auth_exp > ACTIVE_REVIEW_DATE else "no"
            if was_under_16_at_issue:
                row["suggested_current_discipline"] = "Youth Armored"
                row["suggested_action"] = "map_to_youth_armored"
            elif estimated_issue_date and sixteenth_birthday:
                row["suggested_current_discipline"] = "Armored Combat"
                row["suggested_action"] = "map_to_adult_armored"
            else:
                row["suggested_current_discipline"] = ""
                row["suggested_action"] = "manual_review_missing_minor_or_expiration_date"
            row["notes"] = (
                "Estimated issue date assumes two-year youth/ambiguous authorization term. "
                "Under 16 at issue maps to Youth Armored; 16 or older maps to Armored Combat."
            )
        self._write_csv(
            path,
            [
                "legacy_auth_id",
                "legacy_person_id",
                "sca_name",
                "legal_name",
                "minor_exp_date",
                "estimated_16th_birthday",
                "estimated_initial_authorization_date",
                "minor_signal",
                "under_16_at_estimated_issue",
                "expiration_after_2026_05_01",
                "auth_discipline",
                "weaponform",
                "weaponform_discipline",
                "legacy_status",
                "legacy_expires_on",
                "marshal_level",
                "suggested_current_discipline",
                "suggested_action",
                "notes",
            ],
            rows,
        )
        return path

    def _write_excluded_authorization_review(self):
        path = self.output_dir / "excluded_authorization_review.csv"
        rows = self._fetch_dicts(
            "SELECT a.ID AS legacy_auth_id, a.PersonID AS legacy_person_id, p.SCAName AS sca_name, "
            "p.LegalName AS legal_name, ad.Name AS auth_discipline, w.Name AS weaponform, "
            "wd.Name AS weaponform_discipline, a.Status AS legacy_status, a.ExpiresOn AS legacy_expires_on, "
            "m.Name AS marshal_level "
            "FROM authorizations a "
            "LEFT JOIN people p ON p.ID = a.PersonID "
            "LEFT JOIN disciplines ad ON ad.ID = a.DisciplineID "
            "LEFT JOIN weaponform w ON w.ID = a.WeaponFormID "
            "LEFT JOIN disciplines wd ON wd.ID = w.DisciplineID "
            "LEFT JOIN marshallevel m ON m.ID = a.MarshalLevelID "
            "WHERE (m.Name = 'None' AND (ad.Name IN ('Target Archery', 'Thrown Weapons') "
            "OR wd.Name IN ('Target Archery', 'Thrown Weapons'))) "
            "OR w.Name = 'Experimental Weapon' "
            "ORDER BY ad.Name, w.Name, p.SCAName, a.ID"
        )
        for row in rows:
            if row["weaponform"] == "Experimental Weapon":
                row["exclude_reason"] = "society_level_experimental_weapon"
                row["suggested_action"] = "drop_authorization"
                row["notes"] = "Experimental Weapon is handled at Society level and is not represented on current fighter cards."
            else:
                row["exclude_reason"] = "standalone_target_archery_or_thrown_weapons"
                row["suggested_action"] = "drop_authorization"
                row["notes"] = (
                    "Current system does not have standalone non-marshal authorizations for Target Archery or Thrown Weapons."
                )
        self._write_csv(
            path,
            [
                "exclude_reason",
                "legacy_auth_id",
                "legacy_person_id",
                "sca_name",
                "legal_name",
                "auth_discipline",
                "weaponform",
                "weaponform_discipline",
                "legacy_status",
                "legacy_expires_on",
                "marshal_level",
                "suggested_action",
                "notes",
            ],
            rows,
        )
        return path

    def _write_avacal_exclusion_review(self):
        path = self.output_dir / "avacal_exclusion_review.csv"
        rows = self._fetch_dicts(
            "SELECT p.ID AS legacy_person_id, p.SCAName AS sca_name, p.LegalName AS legal_name, "
            "p.Email AS email, b.Name AS branch_name, b.Type AS branch_type, r.Name AS region_name, "
            "(SELECT COUNT(*) FROM authorizations a WHERE a.PersonID = p.ID) AS authorization_count, "
            "(SELECT SUM(CASE WHEN a.Status = 'Active' THEN 1 ELSE 0 END) FROM authorizations a WHERE a.PersonID = p.ID) "
            "AS active_authorization_count, "
            "(SELECT MAX(a.ExpiresOn) FROM authorizations a WHERE a.PersonID = p.ID) AS latest_auth_expiration "
            "FROM people p "
            "LEFT JOIN branches b ON b.ID = p.BranchID "
            "LEFT JOIN regions r ON r.ID = b.RegionID "
            "WHERE b.Name = 'Avacal' OR r.Name = 'Avacal' "
            "ORDER BY p.SCAName, p.ID"
        )
        for row in rows:
            row["suggested_action"] = "exclude_person_and_authorizations"
            row["notes"] = (
                "Avacal now maintains its own authorization records; keep legacy DB for lookup if someone returns to An Tir."
            )
        self._write_csv(
            path,
            [
                "legacy_person_id",
                "sca_name",
                "legal_name",
                "email",
                "branch_name",
                "branch_type",
                "region_name",
                "authorization_count",
                "active_authorization_count",
                "latest_auth_expiration",
                "suggested_action",
                "notes",
            ],
            rows,
        )
        return path

    def _write_reference_mapping_review(self):
        path = self.output_dir / "reference_mapping_review.csv"
        rows = []

        for row in self.legacy_disciplines:
            target = self._target_discipline_name(row["Name"])
            target_obj = self.current_disciplines.get(self._norm(target))
            rows.append(
                {
                    "reference_type": "discipline",
                    "legacy_id": row["ID"],
                    "legacy_name": row["Name"],
                    "legacy_context": "",
                    "suggested_action": "map_to_current" if target_obj else "needs_manual_mapping",
                    "suggested_current_value": target or "",
                    "confidence": "high" if target_obj else "none",
                    "notes": "Current canonical discipline is retained; legacy row is a mapping input.",
                }
            )

        for row in self.legacy_weaponforms:
            target_discipline = self._target_discipline_name(row["DisciplineName"])
            target_style, confidence, notes = self._target_style_name(target_discipline, row["Name"])
            rows.append(
                {
                    "reference_type": "weaponform",
                    "legacy_id": row["ID"],
                    "legacy_name": row["Name"],
                    "legacy_context": row["DisciplineName"],
                    "suggested_action": "ignore_for_marshal_transform"
                    if row["Name"] in IGNORE_WEAPON_FORMS
                    else ("map_to_current" if target_style else "needs_manual_mapping"),
                    "suggested_current_value": f"{target_discipline} / {target_style}" if target_style else "",
                    "confidence": confidence,
                    "notes": notes,
                }
            )

        for row in self.legacy_marshallevels:
            target_discipline = self._target_discipline_name(row["DisciplineName"])
            marshal_name = self._target_marshal_style_name(row["Name"])
            rows.append(
                {
                    "reference_type": "marshallevel",
                    "legacy_id": row["ID"],
                    "legacy_name": row["Name"],
                    "legacy_context": row["DisciplineName"],
                    "suggested_action": "create_extra_marshal_authorization"
                    if marshal_name
                    else "no_extra_marshal_authorization",
                    "suggested_current_value": f"{target_discipline} / {marshal_name}" if marshal_name else "",
                    "confidence": "high",
                    "notes": (
                        "Legacy marshal level becomes a separate current WeaponStyle authorization."
                        if marshal_name
                        else "Legacy None does not create a marshal authorization."
                    ),
                }
            )

        for row in self.legacy_branches:
            target = self.current_branches.get(self._norm(row["Name"]))
            rows.append(
                {
                    "reference_type": "branch",
                    "legacy_id": row["ID"],
                    "legacy_name": row["Name"],
                    "legacy_context": f'{row["Type"]}; region={row["RegionName"] or ""}',
                    "suggested_action": "map_to_current" if target else "needs_manual_mapping",
                    "suggested_current_value": target.name if target else "",
                    "confidence": "high" if target else "none",
                    "notes": "Current canonical branch is retained; legacy branch is a mapping input.",
                }
            )

        for row in self._fetch_dicts(
            "SELECT DISTINCT Title FROM people WHERE Title IS NOT NULL AND TRIM(Title) <> '' ORDER BY Title"
        ):
            target = self.current_titles.get(self._norm(row["Title"]))
            rows.append(
                {
                    "reference_type": "title",
                    "legacy_id": "",
                    "legacy_name": row["Title"],
                    "legacy_context": "",
                    "suggested_action": "map_to_current" if target else "leave_blank_or_manual_map",
                    "suggested_current_value": target.name if target else "",
                    "confidence": "high" if target else "none",
                    "notes": "Unknown titles should not create canonical Title rows during migration.",
                }
            )

        self._write_csv(
            path,
            [
                "reference_type",
                "legacy_id",
                "legacy_name",
                "legacy_context",
                "suggested_action",
                "suggested_current_value",
                "confidence",
                "notes",
            ],
            rows,
        )
        return path

    def _write_authorization_issue_review(self):
        path = self.output_dir / "authorization_issue_review.csv"
        rows = []
        auth_rows = self._fetch_dicts(
            "SELECT a.ID, a.PersonID, p.SCAName, p.LegalName, a.DisciplineID, ad.Name AS AuthDisciplineName, "
            "a.WeaponFormID, w.Name AS WeaponFormName, w.DisciplineID AS WeaponDisciplineID, "
            "wd.Name AS WeaponDisciplineName, a.AuthorizingMarshalID, mp.SCAName AS MarshalSCAName, "
            "a.MarshalLevelID, m.Name AS MarshalLevelName, a.ExpiresOn, a.Status, a.IsSuspended "
            "FROM authorizations a "
            "LEFT JOIN people p ON p.ID = a.PersonID "
            "LEFT JOIN people mp ON mp.ID = a.AuthorizingMarshalID "
            "LEFT JOIN disciplines ad ON ad.ID = a.DisciplineID "
            "LEFT JOIN weaponform w ON w.ID = a.WeaponFormID "
            "LEFT JOIN disciplines wd ON wd.ID = w.DisciplineID "
            "LEFT JOIN marshallevel m ON m.ID = a.MarshalLevelID "
            "ORDER BY a.ID"
        )

        target_counter = Counter()
        row_targets = {}
        for row in auth_rows:
            for target in self._planned_targets_for_auth(row):
                key = (row["PersonID"], target)
                target_counter[key] += 1
                row_targets.setdefault(row["ID"], []).append(target)

        for row in auth_rows:
            base = {
                "legacy_auth_id": row["ID"],
                "legacy_person_id": row["PersonID"],
                "person_sca_name": row["SCAName"] or "",
                "person_legal_name": row["LegalName"] or "",
                "legacy_discipline": row["AuthDisciplineName"] or "",
                "legacy_weaponform": row["WeaponFormName"] or "",
                "weaponform_native_discipline": row["WeaponDisciplineName"] or "",
                "legacy_marshal_id": row["AuthorizingMarshalID"],
                "legacy_marshal_sca_name": row["MarshalSCAName"] or "",
                "legacy_marshal_level": row["MarshalLevelName"] or "",
                "legacy_status": row["Status"] or "",
                "legacy_expires_on": self._date_string(row["ExpiresOn"]),
                "legacy_is_suspended": row["IsSuspended"] or "",
            }
            if not row["SCAName"]:
                rows.append(
                    self._issue_row(
                        base,
                        "MISSING_PERSON",
                        "drop_authorization",
                        "Authorization references no legacy person. Do not import nobody being authorized.",
                        "high",
                    )
                )
            if self._auth_should_be_excluded(row):
                reason = self._auth_exclusion_reason(row)
                rows.append(
                    self._issue_row(
                        base,
                        reason,
                        "drop_authorization",
                        self._auth_exclusion_note(reason),
                        "high",
                    )
                )
            if not row["MarshalSCAName"]:
                rows.append(
                    self._issue_row(
                        base,
                        "MISSING_MARSHAL",
                        "use_seed_kingdom_authorization_officer",
                        "Set marshal to the seed Kingdom Authorization Officer and flag for audit.",
                        "high",
                    )
                )
            if row["WeaponDisciplineID"] and row["DisciplineID"] != row["WeaponDisciplineID"]:
                rows.append(
                    self._issue_row(
                        base,
                        "DISCIPLINE_WEAPONFORM_MISMATCH",
                        "prefer_weaponform_native_discipline_unless_manual_review_overrides",
                        "Most mismatches follow known legacy UI patterns; review grouped patterns before finalizing.",
                        "medium",
                    )
                )
            if row["IsSuspended"] not in ("", "No", "0", None):
                rows.append(
                    self._issue_row(
                        base,
                        "SUSPENDED_AUTHORIZATION",
                        "import_as_inactive_or_flag_for_manual_review",
                        "Do not create Sanction rows automatically; known sanctions will be entered manually.",
                        "medium",
                    )
                )
            if row["AuthDisciplineName"] in ("Rapier Combat", "Cut & Thrust") or row["WeaponDisciplineName"] in (
                "Rapier Combat",
                "Cut & Thrust",
            ):
                rows.append(
                    self._issue_row(
                        base,
                        "RAPIER_CUT_AND_THRUST_MAPPING_REVIEW",
                        "use_explicit_mapping_file",
                        "These forms changed during the missing period; verify mapping before import.",
                        "medium",
                    )
                )
            for target in row_targets.get(row["ID"], []):
                if target_counter[(row["PersonID"], target)] > 1:
                    rows.append(
                        self._issue_row(
                            {
                                **base,
                                "planned_current_authorization": target,
                            },
                            "DUPLICATE_TARGET_AUTHORIZATION",
                            "keep_latest_or_best_row_after_review",
                            "Multiple legacy rows map to the same current person/style target.",
                            "medium",
                        )
                    )

        fieldnames = [
            "issue_type",
            "legacy_auth_id",
            "legacy_person_id",
            "person_sca_name",
            "person_legal_name",
            "legacy_discipline",
            "legacy_weaponform",
            "weaponform_native_discipline",
            "legacy_marshal_id",
            "legacy_marshal_sca_name",
            "legacy_marshal_level",
            "legacy_status",
            "legacy_expires_on",
            "legacy_is_suspended",
            "planned_current_authorization",
            "suggested_action",
            "confidence",
            "notes",
        ]
        self._write_csv(path, fieldnames, rows)
        return path

    def _write_membership_duplicate_review(self):
        path = self.output_dir / "membership_duplicate_review.csv"
        duplicate_rows = self._fetch_dicts(
            "SELECT p.ID, p.SCAName, p.LegalName, p.MembershipNum, "
            "CASE WHEN EXISTS ("
            "  SELECT 1 FROM authorizations a JOIN marshallevel m ON m.ID = a.MarshalLevelID "
            "  WHERE a.PersonID = p.ID AND m.Name IN ('Junior', 'Senior')"
            ") THEN 1 ELSE 0 END AS HasMarshalAuthorization, "
            "(SELECT MAX(a.ExpiresOn) FROM authorizations a WHERE a.PersonID = p.ID) AS LatestAuthExpiration "
            "FROM people p "
            "WHERE p.MembershipNum IS NOT NULL AND p.MembershipNum <> 0 "
            "AND p.MembershipNum IN ("
            "  SELECT MembershipNum FROM people WHERE MembershipNum IS NOT NULL AND MembershipNum <> 0 "
            "  GROUP BY MembershipNum HAVING COUNT(*) > 1"
            ") "
            "ORDER BY p.MembershipNum, HasMarshalAuthorization DESC, LatestAuthExpiration DESC, p.ID"
        )

        grouped = defaultdict(list)
        for row in duplicate_rows:
            grouped[row["MembershipNum"]].append(row)

        rows = []
        for membership, group in grouped.items():
            marshal_rows = [row for row in group if row["HasMarshalAuthorization"]]
            if len(marshal_rows) == 1:
                chosen_id = marshal_rows[0]["ID"]
                group_action = "assign_membership_to_only_marshal_in_group"
                confidence = "medium"
            else:
                chosen_id = ""
                group_action = "manual_review_required"
                confidence = "low"
            for row in group:
                rows.append(
                    {
                        "legacy_person_id": row["ID"],
                        "sca_name": row["SCAName"],
                        "legal_name": row["LegalName"],
                        "membership_number": membership,
                        "has_marshal_authorization": "yes" if row["HasMarshalAuthorization"] else "no",
                        "latest_authorization_expiration": self._date_string(row["LatestAuthExpiration"]),
                        "suggested_action": "keep_membership" if row["ID"] == chosen_id else "clear_membership_and_flag",
                        "group_action": group_action,
                        "confidence": confidence,
                        "notes": "Current User.membership is unique; duplicate legacy memberships cannot all be retained.",
                    }
                )

        self._write_csv(
            path,
            [
                "legacy_person_id",
                "sca_name",
                "legal_name",
                "membership_number",
                "has_marshal_authorization",
                "latest_authorization_expiration",
                "suggested_action",
                "group_action",
                "confidence",
                "notes",
            ],
            rows,
        )
        return path

    def _write_possible_sanction_note_review(self):
        path = self.output_dir / "possible_sanction_note_review.csv"
        rows = self._fetch_dicts(
            "SELECT ID AS legacy_person_id, SCAName AS sca_name, LegalName AS legal_name, Comments AS legacy_comments "
            "FROM people "
            'WHERE Comments IS NOT NULL AND TRIM(Comments) <> "" '
            'AND (LOWER(Comments) LIKE "%sanction%" OR LOWER(Comments) LIKE "%suspend%" '
            'OR LOWER(Comments) LIKE "%revok%" OR LOWER(Comments) LIKE "%ban%") '
            "ORDER BY SCAName, ID"
        )
        for row in rows:
            row["suggested_action"] = "import_as_usernote_and_review_manual_sanction"
            row["notes"] = "Do not create Sanction rows automatically from unstructured legacy comments."
        self._write_csv(
            path,
            ["legacy_person_id", "sca_name", "legal_name", "legacy_comments", "suggested_action", "notes"],
            rows,
        )
        return path

    def _write_duplicate_person_candidate_review(self):
        path = self.output_dir / "duplicate_person_candidate_review.csv"
        rows = []

        duplicate_memberships = self._fetch_dicts(
            "SELECT p.ID, p.SCAName, p.LegalName, p.Email, p.MembershipNum, p.BranchID, b.Name AS BranchName, "
            "CAST(p.MembershipExpiration AS CHAR) AS MembershipExpiration, "
            "CASE WHEN EXISTS ("
            "  SELECT 1 FROM authorizations a JOIN marshallevel m ON m.ID = a.MarshalLevelID "
            "  WHERE a.PersonID = p.ID AND m.Name IN ('Junior', 'Senior')"
            ") THEN 1 ELSE 0 END AS HasMarshalAuthorization, "
            "(SELECT COUNT(*) FROM authorizations a WHERE a.PersonID = p.ID) AS AuthorizationCount, "
            "(SELECT MAX(a.ExpiresOn) FROM authorizations a WHERE a.PersonID = p.ID) AS LatestAuthExpiration "
            "FROM people p "
            "LEFT JOIN branches b ON b.ID = p.BranchID "
            "WHERE p.MembershipNum IS NOT NULL AND p.MembershipNum <> 0 "
            "AND p.MembershipNum IN ("
            "  SELECT MembershipNum FROM people WHERE MembershipNum IS NOT NULL AND MembershipNum <> 0 "
            "  GROUP BY MembershipNum HAVING COUNT(*) > 1"
            ") "
            "ORDER BY p.MembershipNum, p.SCAName, p.ID"
        )
        for row in duplicate_memberships:
            row["candidate_reason"] = "duplicate_membership_number"
            row["suggested_action"] = "review_for_same_human_then_merge_or_clear_duplicate_membership"
            row["notes"] = "Current migration should not import duplicate membership values into unique User.membership."
            rows.append(row)

        exact_name_duplicates = self._fetch_dicts(
            "SELECT p.ID, p.SCAName, p.LegalName, p.Email, p.MembershipNum, p.BranchID, b.Name AS BranchName, "
            "CAST(p.MembershipExpiration AS CHAR) AS MembershipExpiration, "
            "CASE WHEN EXISTS ("
            "  SELECT 1 FROM authorizations a JOIN marshallevel m ON m.ID = a.MarshalLevelID "
            "  WHERE a.PersonID = p.ID AND m.Name IN ('Junior', 'Senior')"
            ") THEN 1 ELSE 0 END AS HasMarshalAuthorization, "
            "(SELECT COUNT(*) FROM authorizations a WHERE a.PersonID = p.ID) AS AuthorizationCount, "
            "(SELECT MAX(a.ExpiresOn) FROM authorizations a WHERE a.PersonID = p.ID) AS LatestAuthExpiration "
            "FROM people p "
            "LEFT JOIN branches b ON b.ID = p.BranchID "
            "JOIN ("
            "  SELECT LOWER(TRIM(SCAName)) AS norm_sca, LOWER(TRIM(LegalName)) AS norm_legal "
            "  FROM people "
            "  WHERE SCAName IS NOT NULL AND TRIM(SCAName) <> '' "
            "  AND LegalName IS NOT NULL AND TRIM(LegalName) <> '' "
            "  GROUP BY LOWER(TRIM(SCAName)), LOWER(TRIM(LegalName)) "
            "  HAVING COUNT(*) > 1"
            ") dup ON dup.norm_sca = LOWER(TRIM(p.SCAName)) "
            "AND dup.norm_legal = LOWER(TRIM(p.LegalName)) "
            "ORDER BY p.SCAName, p.LegalName, p.ID"
        )
        seen = {(row["ID"], row["candidate_reason"]) for row in rows}
        for row in exact_name_duplicates:
            key = (row["ID"], "duplicate_sca_and_legal_name")
            if key in seen:
                continue
            row["candidate_reason"] = "duplicate_sca_and_legal_name"
            row["suggested_action"] = "review_for_same_human_then_merge_before_or_during_import"
            row["notes"] = "Exact normalized SCA/legal name duplicates are likely duplicate legacy person rows."
            rows.append(row)

        self._write_csv(
            path,
            [
                "candidate_reason",
                "ID",
                "SCAName",
                "LegalName",
                "Email",
                "MembershipNum",
                "MembershipExpiration",
                "BranchName",
                "HasMarshalAuthorization",
                "AuthorizationCount",
                "LatestAuthExpiration",
                "suggested_action",
                "notes",
            ],
            rows,
        )
        return path

    def _planned_targets_for_auth(self, row):
        if not row["SCAName"]:
            return []
        if self._auth_should_be_excluded(row):
            return []
        targets = []
        target_discipline = self._target_discipline_name(row["WeaponDisciplineName"] or row["AuthDisciplineName"])
        target_style, _confidence, _notes = self._target_style_name(target_discipline, row["WeaponFormName"])
        if target_style:
            targets.append(f"{target_discipline} / {target_style}")
        marshal_style = self._target_marshal_style_name(row["MarshalLevelName"])
        if marshal_style:
            targets.append(f"{target_discipline} / {marshal_style}")
        return targets

    def _issue_row(self, base, issue_type, suggested_action, notes, confidence):
        return {
            **base,
            "issue_type": issue_type,
            "planned_current_authorization": base.get("planned_current_authorization", ""),
            "suggested_action": suggested_action,
            "confidence": confidence,
            "notes": notes,
        }

    def _target_discipline_name(self, legacy_name):
        return DISCIPLINE_ALIASES.get(self._norm(legacy_name), legacy_name)

    def _target_style_name(self, target_discipline, legacy_style_name):
        if not legacy_style_name:
            return "", "none", "Missing legacy weapon form."
        if legacy_style_name in IGNORE_WEAPON_FORMS:
            return "", "high", "Non-fighting forms are represented by marshal-level transform."
        if legacy_style_name in EXCLUDED_LEGACY_WEAPON_FORMS:
            return "", "high", "Legacy weapon form is intentionally excluded from the current app."
        if target_discipline in EXCLUDED_STANDALONE_DISCIPLINES:
            return "", "high", "Standalone Target Archery and Thrown Weapons authorizations are intentionally excluded."
        aliased = WEAPON_STYLE_ALIASES.get((target_discipline, legacy_style_name), legacy_style_name)
        if (self._norm(target_discipline), self._norm(aliased)) in self.current_styles:
            confidence = "high" if aliased == legacy_style_name else "medium"
            return aliased, confidence, "Maps to existing current WeaponStyle."
        return aliased, "low", "No exact current WeaponStyle match; requires review."

    def _target_marshal_style_name(self, marshal_level_name):
        if marshal_level_name == "Junior":
            return "Junior Marshal"
        if marshal_level_name == "Senior":
            return "Senior Marshal"
        return ""

    def _mismatch_category(self, auth_discipline, weaponform_discipline):
        if {auth_discipline, weaponform_discipline} <= {"Armored Combat", "Youth Combat"}:
            return "adult_youth_armored_ambiguous"
        if auth_discipline == "Armored Combat" and weaponform_discipline == "Missile Combat":
            return "missile_was_legacy_armored"
        return "manual_review"

    def _mismatch_action(self, category):
        if category == "adult_youth_armored_ambiguous":
            return "use_person_minor_context_to_choose_current_armored_or_youth_armored"
        if category == "missile_was_legacy_armored":
            return "map_to_current_missile_combat"
        return "manual_review_required"

    def _auth_should_be_excluded(self, row):
        if row.get("WeaponFormName") in EXCLUDED_LEGACY_WEAPON_FORMS:
            return True
        marshal_level = row.get("MarshalLevelName")
        auth_discipline = row.get("AuthDisciplineName")
        weapon_discipline = row.get("WeaponDisciplineName")
        return marshal_level == "None" and (
            auth_discipline in EXCLUDED_STANDALONE_DISCIPLINES
            or weapon_discipline in EXCLUDED_STANDALONE_DISCIPLINES
        )

    def _auth_exclusion_reason(self, row):
        if row.get("WeaponFormName") in EXCLUDED_LEGACY_WEAPON_FORMS:
            return "EXCLUDED_EXPERIMENTAL_WEAPON"
        return "EXCLUDED_STANDALONE_TARGET_ARCHERY_OR_THROWN_WEAPONS"

    def _auth_exclusion_note(self, reason):
        if reason == "EXCLUDED_EXPERIMENTAL_WEAPON":
            return "Experimental Weapon is handled at Society level and is not represented on current fighter cards."
        return "Current system does not have standalone Target Archery or Thrown Weapons non-marshal authorizations."

    def _current_name_map(self, queryset):
        return {self._norm(obj.name): obj for obj in queryset}

    def _fetch_dicts(self, sql):
        self.legacy_cursor.execute(sql)
        columns = [column[0] for column in self.legacy_cursor.description]
        return [dict(zip(columns, row)) for row in self.legacy_cursor.fetchall()]

    def _scalar(self, sql):
        self.legacy_cursor.execute(sql)
        return self.legacy_cursor.fetchone()[0]

    def _write_csv(self, path, fieldnames, rows):
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _norm(self, value):
        return " ".join(str(value or "").casefold().split())

    def _date_string(self, value):
        if not value or str(value) == ZERO_DATE:
            return ""
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    def _date_or_none(self, value):
        if not value or str(value) == ZERO_DATE:
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
