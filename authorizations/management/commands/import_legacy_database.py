import csv
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from authorizations.models import (
    Authorization,
    AuthorizationNote,
    AuthorizationPortalSetting,
    AuthorizationStatus,
    Branch,
    BranchMarshal,
    Discipline,
    MembershipRosterEntry,
    MembershipRosterImport,
    Person,
    ReportValue,
    ReportingPeriod,
    Sanction,
    SupportingDocument,
    SupportingDocumentAuthorization,
    SupportingDocumentPerson,
    Title,
    User,
    UserNote,
    WeaponStyle,
)


KAO_USER_ID = 15050
KAO_USERNAME = "antir.authorization.database@gmail.com"
KAO_EMAIL = "antir.authorization.database@gmail.com"
KAO_MEMBERSHIP = "KAO-SEED"
KAO_EXPIRATION = date(2100, 12, 31)
KAO_FIRST_NAME = "Database"
KAO_LAST_NAME = "Administrator"
KAO_SCA_NAME = "Administrator"
ZERO_DATE = "0000-00-00"
REMOVE_MARKER = "---"
ACTIVE_CUTOFF = date(2026, 5, 1)
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

REFERENCE_MODELS = [
    AuthorizationStatus,
    Title,
    Discipline,
    Branch,
    WeaponStyle,
    AuthorizationPortalSetting,
    ReportingPeriod,
    ReportValue,
]

APP_MODELS_TO_CLEAR = [
    SupportingDocumentAuthorization,
    SupportingDocumentPerson,
    SupportingDocument,
    MembershipRosterEntry,
    MembershipRosterImport,
    BranchMarshal,
    AuthorizationNote,
    UserNote,
    Sanction,
    Authorization,
    Person,
    User,
    ReportValue,
    ReportingPeriod,
    AuthorizationPortalSetting,
    WeaponStyle,
    Branch,
    Discipline,
    Title,
    AuthorizationStatus,
]


class Command(BaseCommand):
    help = "Dry-run or apply the legacy MySQL database import into the isolated trial database."

    def add_arguments(self, parser):
        parser.add_argument("--source-db", default="legacy", help="Read-only legacy source database alias.")
        parser.add_argument("--target-db", default="trial", help="Writable target database alias. Only trial is allowed.")
        parser.add_argument("--apply", action="store_true", help="Write to the target database.")
        parser.add_argument(
            "--reset-target",
            action="store_true",
            help="Clear target app tables and recopy canonical reference data before importing. Requires --apply.",
        )
        parser.add_argument(
            "--mapping-file",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_migration_decision_validation" / "normalized_reference_mapping.csv"),
            help="Validated normalized reference mapping CSV.",
        )
        parser.add_argument(
            "--duplicate-actions-file",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_migration_decision_validation" / "duplicate_person_import_actions.csv"),
            help="Validated duplicate person import actions CSV.",
        )
        parser.add_argument(
            "--output-dir",
            default=str(Path(settings.BASE_DIR) / "tmp" / "legacy_trial_import"),
            help="Directory where import reports will be written.",
        )

    def handle(self, *args, **options):
        self.source_alias = options["source_db"]
        self.target_alias = options["target_db"]
        self.apply = options["apply"]
        self.reset_target = options["reset_target"]
        self.output_dir = Path(options["output_dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if self.target_alias != "trial":
            raise CommandError("This importer currently only writes to --target-db trial.")
        if self.source_alias != "legacy":
            raise CommandError("This importer currently only reads from --source-db legacy.")
        if self.reset_target and not self.apply:
            raise CommandError("--reset-target requires --apply.")

        call_command("check_legacy_migration_databases", source_db=self.source_alias, target_db=self.target_alias)

        self.source = connections[self.source_alias]
        self.target = connections[self.target_alias]
        self.mapping_rows = self._read_csv(Path(options["mapping_file"]))
        self.duplicate_action_rows = self._read_csv(Path(options["duplicate_actions_file"]))

        self.ref_maps = self._build_reference_maps()
        self.person_actions = self._build_person_actions()
        self.avacal_people = self._legacy_id_set(
            "SELECT p.ID FROM people p LEFT JOIN branches b ON b.ID=p.BranchID "
            "LEFT JOIN regions r ON r.ID=b.RegionID WHERE b.Name='Avacal' OR r.Name='Avacal'"
        )
        self.legacy_people = self._legacy_people()
        self.legacy_auths = self._legacy_authorizations()

        self.summary = defaultdict(int)
        self.dropped_rows = []
        self.unresolved_rows = []

        if self.apply:
            with transaction.atomic(using=self.target_alias):
                if self.reset_target:
                    self._clear_target()
                    self._copy_reference_data()
                self._seed_kao()
                self._import_people()
                self._import_authorizations()
        else:
            self._plan_people()
            self._plan_authorizations()

        self._write_reports()
        self.stdout.write(self.style.SUCCESS("Legacy import dry-run complete." if not self.apply else "Legacy trial import complete."))
        self.stdout.write(str(self.output_dir / "import_summary.csv"))
        self.stdout.write(str(self.output_dir / "dropped_rows.csv"))
        self.stdout.write(str(self.output_dir / "unresolved_rows.csv"))

    def _clear_target(self):
        with self.target.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            for model in APP_MODELS_TO_CLEAR:
                cursor.execute(f"TRUNCATE TABLE `{model._meta.db_table}`")
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")

    def _copy_reference_data(self):
        # Branch has a self-FK, so copy it with region unset, then restore region links.
        for model in [AuthorizationStatus, Title, Discipline]:
            for obj in model.objects.using("default").all().order_by("pk"):
                self._save_reference_copy(obj)

        branch_regions = {}
        for branch in Branch.objects.using("default").all().order_by("pk"):
            branch_regions[branch.pk] = branch.region_id
            branch.region_id = None
            self._save_reference_copy(branch)
        for branch_id, region_id in branch_regions.items():
            if region_id:
                Branch.objects.using(self.target_alias).filter(pk=branch_id).update(region_id=region_id)

        for model in [WeaponStyle, AuthorizationPortalSetting, ReportingPeriod, ReportValue]:
            for obj in model.objects.using("default").all().order_by("pk"):
                self._save_reference_copy(obj)

    def _save_reference_copy(self, obj):
        for field_name in ["created_by_id", "updated_by_id", "imported_by_id"]:
            if hasattr(obj, field_name):
                setattr(obj, field_name, None)
        obj._state.adding = True
        obj.save(using=self.target_alias, force_insert=True)

    def _seed_kao(self):
        branch = Branch.objects.using(self.target_alias).get(name="An Tir")
        discipline = Discipline.objects.using(self.target_alias).get(name="Authorization Officer")
        existing = User.objects.using(self.target_alias).filter(pk=KAO_USER_ID).first()
        if existing and existing.username != KAO_USERNAME:
            raise CommandError(f"Trial target user ID {KAO_USER_ID} is already occupied by {existing.username}.")

        user, _created = User.objects.using(self.target_alias).get_or_create(
            pk=KAO_USER_ID,
            defaults={"username": KAO_USERNAME},
        )
        user.username = KAO_USERNAME
        user.email = KAO_EMAIL
        user.first_name = KAO_FIRST_NAME
        user.last_name = KAO_LAST_NAME
        user.membership = KAO_MEMBERSHIP
        user.membership_expiration = KAO_EXPIRATION
        user.waiver_expiration = KAO_EXPIRATION
        user.background_check_expiration = KAO_EXPIRATION
        user.address = "1"
        user.city = "An Tir"
        user.state_province = "OR"
        user.postal_code = "97000"
        user.country = "USA"
        user.phone_number = "111-111-1111"
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        if not user.has_usable_password():
            user.set_unusable_password()
        user.save(using=self.target_alias)

        if Person.objects.using(self.target_alias).filter(user_id=KAO_USER_ID).exists():
            Person.objects.using(self.target_alias).filter(user_id=KAO_USER_ID).update(
                sca_name=KAO_SCA_NAME,
                branch_id=branch.id,
                is_minor=False,
            )
        else:
            Person.objects.using(self.target_alias).bulk_create(
                [
                    Person(
                        user_id=KAO_USER_ID,
                        sca_name=KAO_SCA_NAME,
                        branch_id=branch.id,
                        is_minor=False,
                    )
                ]
            )

        BranchMarshal.objects.using(self.target_alias).update_or_create(
            person_id=KAO_USER_ID,
            branch=branch,
            discipline=discipline,
            defaults={"start_date": date.today(), "end_date": KAO_EXPIRATION, "created_by_id": KAO_USER_ID, "updated_by_id": KAO_USER_ID},
        )
        self.summary["kao_seeded"] = 1

    def _plan_people(self):
        for person_id, row in self.legacy_people.items():
            decision = self._person_decision(person_id)
            if not decision["import"]:
                self.summary[f'people_{decision["reason"]}'] += 1
                continue
            if decision["canonical_id"] == person_id:
                self.summary["people_to_import"] += 1
            else:
                self.summary["people_merged_away"] += 1

    def _import_people(self):
        self.legacy_to_user_id = {}
        used_usernames = {KAO_USERNAME}
        used_identity_keys = set()
        canonical_ids = []
        for person_id, row in self.legacy_people.items():
            decision = self._person_decision(person_id)
            if not decision["import"]:
                self.summary[f'people_{decision["reason"]}'] += 1
                continue
            self.legacy_to_user_id[person_id] = decision["canonical_id"]
            if decision["canonical_id"] == person_id:
                canonical_ids.append(person_id)
            else:
                self.summary["people_merged_away"] += 1

        for person_id in sorted(canonical_ids):
            row = self.legacy_people[person_id]
            action = self.person_actions.get(str(person_id), {})
            username = self._username_for(row, used_usernames)
            used_usernames.add(username)
            first_name, last_name = self._split_legal_name(row["LegalName"])
            email = (row["Email"] or "").strip() or f"legacy-import+{person_id}@invalid.local"
            identity_key = (first_name.casefold(), last_name.casefold(), email.casefold())
            email_replaced_for_identity_constraint = False
            if identity_key in used_identity_keys:
                original_email = email
                email = f"legacy-duplicate-email+{person_id}@invalid.local"
                email_replaced_for_identity_constraint = True
                self.summary["people_duplicate_identity_email_replaced"] += 1
            used_identity_keys.add((first_name.casefold(), last_name.casefold(), email.casefold()))
            membership, membership_expiration = self._membership_for(row, action)
            branch_id = self.ref_maps["branch"].get(str(row["BranchID"]))
            title_id = self.ref_maps["title"].get(self._norm(row["Title"]))
            if not branch_id:
                self._unresolved("person", person_id, "branch", row["BranchID"], "No branch mapping.")
                continue

            user = User(
                username=self._clip(username, 150),
                email=self._clip(email, 254),
                first_name=self._clip(first_name, 150),
                last_name=self._clip(last_name, 150),
            )
            user.set_unusable_password()
            user.membership = membership
            user.membership_expiration = membership_expiration
            user.address = self._clip(row["Address1"], 255)
            user.address2 = self._clip(row["Address2"], 255)
            user.city = self._clip(row["City"], 100)
            user.state_province = self._clip(row["State"], 100)
            user.postal_code = self._clip(row["PostCode"], 10)
            user.country = self._clip(row["Country"], 100)
            user.phone_number = self._clip(row["PhoneNumber"], 20)
            user.background_check_expiration = self._date_or_none(row["BackgroundCheckExpiration"])
            minor_expiration = self._date_or_none(row["MinorExpDate"])
            user.birthday = self._birthday_from_minor_expiration(row, minor_expiration)
            user.save(using=self.target_alias)

            Person.objects.using(self.target_alias).bulk_create(
                [
                    Person(
                        user_id=user.id,
                        sca_name=self._clip(row["SCAName"] or first_name, 255),
                        branch_id=branch_id,
                        title_id=title_id,
                        is_minor=bool(minor_expiration and minor_expiration >= ACTIVE_CUTOFF),
                        created_by_id=KAO_USER_ID,
                        updated_by_id=KAO_USER_ID,
                    )
                ]
            )
            self.legacy_to_user_id[person_id] = user.id
            if row["Comments"] and row["Comments"].strip():
                UserNote.objects.using(self.target_alias).create(
                    person_id=user.id,
                    created_by_id=KAO_USER_ID,
                    note=f"Legacy person note imported from prior authorization database.\n\n{row['Comments'].strip()}",
                )
            if email_replaced_for_identity_constraint:
                UserNote.objects.using(self.target_alias).create(
                    person_id=user.id,
                    created_by_id=KAO_USER_ID,
                    note=(
                        "Legacy email was replaced with a placeholder during import because another imported account "
                        f"had the same legal first name, legal last name, and email. Original legacy email: {original_email}"
                    ),
                )
            self.summary["people_imported"] += 1

        # Point merged-away source IDs at the newly created canonical target IDs.
        for source_id, action in self.person_actions.items():
            target_legacy_id = action.get("map_to_id")
            if source_id != target_legacy_id and target_legacy_id in self.legacy_to_user_id:
                self.legacy_to_user_id[int(source_id)] = self.legacy_to_user_id[int(target_legacy_id)]

    def _plan_authorizations(self):
        for auth in self.legacy_auths:
            for candidate in self._authorization_candidates(auth):
                if candidate["action"] == "drop":
                    self.summary[f'auth_drop_{candidate["reason"]}'] += 1
                elif candidate["action"] == "import":
                    self.summary["authorization_candidates_to_import"] += 1
                else:
                    self.summary["authorization_candidates_unresolved"] += 1

    def _import_authorizations(self):
        active_status = AuthorizationStatus.objects.using(self.target_alias).get(name="Active")
        inactive_status = AuthorizationStatus.objects.using(self.target_alias).get(name="Inactive")
        imported_person_ids = set(Person.objects.using(self.target_alias).values_list("user_id", flat=True))
        candidates_by_key = {}
        for auth in self.legacy_auths:
            for candidate in self._authorization_candidates(auth):
                if candidate["action"] == "drop":
                    self.summary[f'auth_drop_{candidate["reason"]}'] += 1
                    self.dropped_rows.append(candidate)
                    continue
                if candidate["action"] != "import":
                    self.summary["authorization_candidates_unresolved"] += 1
                    self.unresolved_rows.append(candidate)
                    continue
                if candidate["person_id"] not in imported_person_ids:
                    candidate["action"] = "drop"
                    candidate["reason"] = "target_person_not_imported"
                    self.summary["auth_drop_target_person_not_imported"] += 1
                    self.dropped_rows.append(candidate)
                    continue
                if candidate["marshal_id"] not in imported_person_ids:
                    candidate["marshal_id"] = KAO_USER_ID
                    self.summary["authorization_missing_marshal_set_to_kao"] += 1
                self.summary["authorization_candidates_to_import"] += 1
                key = (candidate["person_id"], candidate["style_id"])
                existing = candidates_by_key.get(key)
                if not existing or candidate["expiration"] > existing["expiration"]:
                    candidates_by_key[key] = candidate

        for candidate in candidates_by_key.values():
            status = active_status
            if candidate["legacy_status"] != "Active" or candidate["is_suspended"] == "Yes":
                status = inactive_status
            auth = Authorization.objects.using(self.target_alias).create(
                person_id=candidate["person_id"],
                style_id=candidate["style_id"],
                status_id=status.id,
                marshal_id=candidate["marshal_id"],
                expiration=candidate["expiration"],
                created_by_id=KAO_USER_ID,
                updated_by_id=KAO_USER_ID,
            )
            AuthorizationNote.objects.using(self.target_alias).create(
                authorization_id=auth.id,
                created_by_id=KAO_USER_ID,
                action="marshal_approved",
                note=(
                    "Authorization imported from legacy authorization database. "
                    f"Legacy authorization ID: {candidate['legacy_auth_id']}."
                ),
            )
            self.summary["authorizations_imported"] += 1
        self.summary["authorization_duplicates_collapsed"] = self.summary["authorization_candidates_to_import"] - len(candidates_by_key)

    def _authorization_candidates(self, auth):
        person_decision = self._person_decision(auth["PersonID"])
        if not person_decision["import"]:
            return [self._drop_candidate(auth, person_decision["reason"])]
        target_legacy_person = person_decision["canonical_id"]
        person_id = getattr(self, "legacy_to_user_id", {}).get(target_legacy_person, target_legacy_person)
        marshal_id = getattr(self, "legacy_to_user_id", {}).get(auth["AuthorizingMarshalID"], KAO_USER_ID)
        if not marshal_id:
            marshal_id = KAO_USER_ID
        expiration = self._date_or_none(auth["ExpiresOn"])
        if not expiration:
            return [self._unresolved_candidate(auth, "missing_expiration")]

        candidates = []
        weapon_action = self.ref_maps["weaponform_action"].get(str(auth["WeaponFormID"]))
        style_id = self.ref_maps["weaponform"].get(str(auth["WeaponFormID"]))
        if weapon_action == "remove":
            candidates.append(self._drop_candidate(auth, "reference_removed"))
        elif style_id:
            candidates.append(self._import_candidate(auth, person_id, style_id, marshal_id, expiration, "weaponform"))

        marshal_style_id = self.ref_maps["marshallevel"].get(str(auth["MarshalLevelID"]))
        if marshal_style_id:
            candidates.append(self._import_candidate(auth, person_id, marshal_style_id, marshal_id, expiration, "marshallevel"))

        if not candidates:
            candidates.append(self._drop_candidate(auth, "no_mapped_current_authorization"))
        return candidates

    def _person_decision(self, person_id):
        if person_id not in self.legacy_people:
            return {"import": False, "reason": "missing_legacy_person", "canonical_id": None}
        if person_id in self.avacal_people:
            return {"import": False, "reason": "excluded_avacal", "canonical_id": None}
        action = self.person_actions.get(str(person_id))
        if action and action["map_to_id"] and int(action["map_to_id"]) != person_id:
            target_id = int(action["map_to_id"])
            if target_id in self.avacal_people:
                return {"import": False, "reason": "merged_to_excluded_avacal", "canonical_id": None}
            return {"import": True, "reason": "merged", "canonical_id": target_id}
        return {"import": True, "reason": "canonical", "canonical_id": person_id}

    def _build_reference_maps(self):
        maps = {
            "branch": {},
            "title": {},
            "weaponform": {},
            "weaponform_action": {},
            "marshallevel": {},
        }
        for row in self.mapping_rows:
            ref_type = row["reference_type"]
            action = row["suggested_action"]
            legacy_id = row["legacy_id"]
            current_id = row["current_id"]
            if ref_type == "branch" and action == "map_to_current":
                maps["branch"][legacy_id] = int(current_id)
            elif ref_type == "title":
                if action == "map_to_current":
                    maps["title"][self._norm(row["legacy_name"])] = int(current_id)
                elif action == "remove":
                    maps["title"][self._norm(row["legacy_name"])] = None
            elif ref_type == "weaponform":
                maps["weaponform_action"][legacy_id] = action
                if action == "map_to_current":
                    maps["weaponform"][legacy_id] = int(current_id)
            elif ref_type == "marshallevel" and action == "create_extra_marshal_authorization":
                maps["marshallevel"][legacy_id] = int(current_id)
        return maps

    def _build_person_actions(self):
        return {row["legacy_person_id"]: row for row in self.duplicate_action_rows}

    def _legacy_people(self):
        rows = self._fetch_source(
            "SELECT ID, LegalName, SCAName, Title, Address1, Address2, City, State, PostCode, Country, "
            "PhoneNumber, CAST(MinorExpDate AS CHAR) AS MinorExpDate, BranchID, Email, MembershipNum, "
            "CAST(MembershipExpiration AS CHAR) AS MembershipExpiration, CAST(BackgroundCheckExpiration AS CHAR) AS BackgroundCheckExpiration, Comments "
            "FROM people ORDER BY ID"
        )
        return {row["ID"]: row for row in rows}

    def _legacy_authorizations(self):
        return self._fetch_source(
            "SELECT a.ID, a.PersonID, a.DisciplineID, a.WeaponFormID, a.AuthorizingMarshalID, a.MarshalLevelID, "
            "CAST(a.ExpiresOn AS CHAR) AS ExpiresOn, a.Status AS legacy_status, a.IsSuspended "
            "FROM authorizations a ORDER BY a.ID"
        )

    def _membership_for(self, row, action):
        membership = None if row["MembershipNum"] in (None, 0, "0") else str(row["MembershipNum"])
        expiration = self._date_or_none(row["MembershipExpiration"])
        if action.get("membership_action") == "clear_membership":
            return None, None
        if action.get("membership_action") == "override_membership":
            membership = action.get("membership_value") or None
        if action.get("membership_expiration_action") == "clear_membership_expiration":
            expiration = None
        elif action.get("membership_expiration_action") == "override_membership_expiration":
            expiration = self._date_or_none(action.get("membership_expiration_value"))
        if not membership or not expiration:
            return None, None
        return membership, expiration

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

    def _clip(self, value, max_length):
        return str(value or "")[:max_length]

    def _split_legal_name(self, value):
        parts = str(value or "").strip().split()
        if not parts:
            return "Legacy", "Fighter"
        if len(parts) == 1:
            return parts[0], "Fighter"
        return parts[0], " ".join(parts[1:])

    def _birthday_from_minor_expiration(self, row, minor_expiration):
        if not minor_expiration:
            return None
        return minor_expiration - relativedelta(years=19 if self._legacy_person_lives_in_canada(row) else 18)

    def _legacy_person_lives_in_canada(self, row):
        country = str(row.get("Country") or "").strip().casefold()
        state = str(row.get("State") or "").strip().upper().replace(".", "")
        return "canada" in country or state in CANADA_PROVINCES

    def _import_candidate(self, auth, person_id, style_id, marshal_id, expiration, source):
        return {
            "action": "import",
            "legacy_auth_id": auth["ID"],
            "person_id": person_id,
            "style_id": style_id,
            "marshal_id": marshal_id,
            "expiration": expiration,
            "legacy_status": auth["legacy_status"],
            "is_suspended": auth["IsSuspended"],
            "source": source,
        }

    def _drop_candidate(self, auth, reason):
        return {"action": "drop", "legacy_auth_id": auth["ID"], "reason": reason}

    def _unresolved_candidate(self, auth, reason):
        return {"action": "unresolved", "legacy_auth_id": auth["ID"], "reason": reason}

    def _unresolved(self, row_type, row_id, field, value, message):
        self.unresolved_rows.append({"action": "unresolved", "row_type": row_type, "row_id": row_id, "field": field, "value": value, "reason": message})

    def _legacy_id_set(self, sql):
        with self.source.cursor() as cursor:
            cursor.execute(sql)
            return {row[0] for row in cursor.fetchall()}

    def _fetch_source(self, sql):
        with self.source.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _read_csv(self, path):
        if not path.exists():
            raise CommandError(f"Required decision file not found: {path}")
        with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            return list(csv.DictReader(csv_file))

    def _write_reports(self):
        self._write_csv(
            self.output_dir / "import_summary.csv",
            ["metric", "value"],
            [{"metric": key, "value": value} for key, value in sorted(self.summary.items())],
        )
        self._write_csv(self.output_dir / "dropped_rows.csv", ["action", "legacy_auth_id", "reason"], self.dropped_rows)
        self._write_csv(
            self.output_dir / "unresolved_rows.csv",
            ["action", "legacy_auth_id", "row_type", "row_id", "field", "value", "reason"],
            self.unresolved_rows,
        )

    def _write_csv(self, path, fieldnames, rows):
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
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

    def _norm(self, value):
        return " ".join(str(value or "").casefold().split())
