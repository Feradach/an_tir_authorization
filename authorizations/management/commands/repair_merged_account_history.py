from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from authorizations.models import (
    Authorization,
    AuthorizationAuditEntry,
    AuthorizationNote,
    AuthorizationPortalSetting,
    AuthorizationStatus,
    AuthorizationValidityInterval,
    Branch,
    BranchMarshal,
    Discipline,
    LegacyAuthorizationRecoveryEntry,
    MembershipRosterImport,
    Person,
    Sanction,
    SupportingDocument,
    SupportingDocumentAuthorization,
    SupportingDocumentPerson,
    Title,
    User,
    UserNote,
    WaiverRecord,
    WeaponStyle,
)


class Command(BaseCommand):
    help = "Reattach surviving history rows from tombstoned merged accounts to their survivor accounts."

    USER_REFERENCE_FIELDS = [
        (User, "created_by"),
        (User, "updated_by"),
        (AuthorizationPortalSetting, "updated_by"),
        (MembershipRosterImport, "imported_by"),
        (Branch, "created_by"),
        (Branch, "updated_by"),
        (Discipline, "created_by"),
        (Discipline, "updated_by"),
        (WeaponStyle, "created_by"),
        (WeaponStyle, "updated_by"),
        (AuthorizationStatus, "created_by"),
        (AuthorizationStatus, "updated_by"),
        (Title, "created_by"),
        (Title, "updated_by"),
        (Person, "created_by"),
        (Person, "updated_by"),
        (Authorization, "created_by"),
        (Authorization, "updated_by"),
        (AuthorizationValidityInterval, "created_by"),
        (AuthorizationAuditEntry, "changed_by"),
        (AuthorizationAuditEntry, "before_created_by"),
        (AuthorizationAuditEntry, "after_created_by"),
        (AuthorizationAuditEntry, "before_updated_by"),
        (AuthorizationAuditEntry, "after_updated_by"),
        (BranchMarshal, "created_by"),
        (BranchMarshal, "updated_by"),
        (Sanction, "issued_by"),
        (Sanction, "lifted_by"),
        (Sanction, "created_by"),
        (Sanction, "updated_by"),
        (AuthorizationNote, "created_by"),
        (UserNote, "created_by"),
        (WaiverRecord, "covered_user"),
        (WaiverRecord, "signer_user"),
        (WaiverRecord, "recorded_by"),
        (SupportingDocument, "uploaded_by"),
        (SupportingDocument, "reviewed_by"),
        (LegacyAuthorizationRecoveryEntry, "created_by"),
    ]

    PERSON_REFERENCE_FIELDS = [
        (Person, "parent"),
        (Authorization, "marshal"),
        (Authorization, "concurring_fighter"),
        (AuthorizationAuditEntry, "person"),
        (AuthorizationAuditEntry, "before_person"),
        (AuthorizationAuditEntry, "after_person"),
        (AuthorizationAuditEntry, "before_marshal"),
        (AuthorizationAuditEntry, "after_marshal"),
        (AuthorizationAuditEntry, "before_concurring_fighter"),
        (AuthorizationAuditEntry, "after_concurring_fighter"),
        (BranchMarshal, "person"),
        (Sanction, "person"),
        (UserNote, "person"),
        (LegacyAuthorizationRecoveryEntry, "person"),
        (LegacyAuthorizationRecoveryEntry, "marshal"),
        (LegacyAuthorizationRecoveryEntry, "second_marshal"),
        (LegacyAuthorizationRecoveryEntry, "concurring_officer"),
        (LegacyAuthorizationRecoveryEntry, "previous_marshal"),
        (LegacyAuthorizationRecoveryEntry, "previous_concurring_fighter"),
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this flag, only report planned repairs.",
        )
        parser.add_argument(
            "--source-user-id",
            action="append",
            type=int,
            default=[],
            help="Limit repair to one tombstoned source user ID. May be provided more than once.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        source_user_ids = options["source_user_id"]

        source_users = self._source_users(source_user_ids)
        if not source_users:
            self.stdout.write("No merged source accounts were found.")
            return

        self.stdout.write(f"Merged source accounts found: {len(source_users)}")

        totals = Counter()
        per_account = []
        if apply_changes:
            with transaction.atomic():
                for source_user in source_users:
                    per_account.append(self._repair_source_user(source_user, apply_changes=True))
        else:
            for source_user in source_users:
                per_account.append(self._repair_source_user(source_user, apply_changes=False))

        for result in per_account:
            totals.update(result["counts"])
            self.stdout.write(
                f"- source_user_id={result['source_user_id']} -> survivor_user_id={result['survivor_user_id'] or '-'} "
                f"changes={sum(result['counts'].values())} "
                f"duplicate_authorizations={result['counts'].get('duplicate_authorizations_removed', 0)} "
                f"status={result['status']}"
            )

        self.stdout.write("Repair summary:")
        for key, value in sorted(totals.items()):
            self.stdout.write(f"{key}: {value}")

        if not apply_changes:
            self.stdout.write("Dry run only. Re-run with --apply to reattach these history rows.")
            return

        self.stdout.write(self.style.SUCCESS("Merged account history repair complete."))

    def _source_users(self, source_user_ids):
        query = User.objects.select_related("merged_into").filter(merged_into__isnull=False).order_by("id")
        if source_user_ids:
            query = query.filter(id__in=source_user_ids)
        return list(query)

    def _repair_source_user(self, source_user, *, apply_changes):
        counts = Counter()
        survivor_user = self._terminal_survivor(source_user)
        if not survivor_user:
            counts["skipped_missing_survivor"] += 1
            return self._result(source_user, None, "skipped_missing_survivor", counts)

        try:
            source_person = source_user.person
            survivor_person = survivor_user.person
        except Person.DoesNotExist:
            counts["skipped_missing_person"] += 1
            return self._result(source_user, survivor_user, "skipped_missing_person", counts)

        if source_user.merged_into_id != survivor_user.id:
            counts["source_user_terminal_survivor_updates"] += self._count_or_update(
                User.objects.filter(pk=source_user.pk),
                apply_changes,
                merged_into=survivor_user,
            )

        for model, field_name in self.USER_REFERENCE_FIELDS:
            counts[f"{model.__name__}.{field_name}"] += self._move_fk(
                model,
                field_name,
                source_user,
                survivor_user,
                apply_changes,
            )

        for model, field_name in self.PERSON_REFERENCE_FIELDS:
            counts[f"{model.__name__}.{field_name}"] += self._move_fk(
                model,
                field_name,
                source_person,
                survivor_person,
                apply_changes,
            )

        counts.update(self._move_supporting_document_person_links(source_person, survivor_person, apply_changes))
        counts.update(self._repair_authorizations(source_person, survivor_person, apply_changes))

        return self._result(source_user, survivor_user, "planned" if not apply_changes else "applied", counts)

    def _repair_authorizations(self, source_person, survivor_person, apply_changes):
        counts = Counter()
        authorizations = list(
            Authorization.objects.select_related("style")
            .filter(person__in=[source_person, survivor_person])
            .order_by("style_id", "updated_at", "id")
        )

        with_style = defaultdict(list)
        without_style = []
        for authorization in authorizations:
            if authorization.style_id:
                with_style[authorization.style_id].append(authorization)
            else:
                without_style.append(authorization)

        for auth in without_style:
            if auth.person_id == source_person.user_id:
                counts["authorizations_without_style_moved"] += 1
                if apply_changes:
                    auth.person = survivor_person
                    auth.save(update_fields=["person", "updated_at"])

        for candidates in with_style.values():
            winner = max(candidates, key=self._updated_sort_key)
            losers = [candidate for candidate in candidates if candidate.id != winner.id]
            for loser in losers:
                counts.update(self._reattach_authorization_history(loser, winner, apply_changes))
                counts["duplicate_authorizations_removed"] += 1
                if apply_changes:
                    loser.delete()

            if winner.person_id == source_person.user_id:
                counts["authorization_person_updates"] += 1
                if apply_changes:
                    winner.person = survivor_person
                    winner.save(update_fields=["person", "updated_at"])

        return counts

    def _reattach_authorization_history(self, loser, winner, apply_changes):
        counts = Counter()
        for model, field_name in [
            (AuthorizationNote, "authorization"),
            (AuthorizationValidityInterval, "authorization"),
            (AuthorizationAuditEntry, "authorization"),
            (LegacyAuthorizationRecoveryEntry, "authorization"),
        ]:
            counts[f"{model.__name__}.{field_name}"] += self._move_fk(
                model,
                field_name,
                loser,
                winner,
                apply_changes,
            )
        counts.update(self._move_supporting_document_authorization_links(loser, winner, apply_changes))
        return counts

    def _move_supporting_document_person_links(self, source_person, survivor_person, apply_changes):
        counts = Counter()
        links = list(SupportingDocumentPerson.objects.filter(person=source_person).select_related("document"))
        for link in links:
            duplicate = SupportingDocumentPerson.objects.filter(
                document=link.document,
                person=survivor_person,
            ).exclude(pk=link.pk).exists()
            if duplicate:
                counts["SupportingDocumentPerson.duplicates_removed"] += 1
                if apply_changes:
                    link.delete()
            else:
                counts["SupportingDocumentPerson.person"] += 1
                if apply_changes:
                    link.person = survivor_person
                    link.save(update_fields=["person"])
        return counts

    def _move_supporting_document_authorization_links(self, loser, winner, apply_changes):
        counts = Counter()
        links = list(SupportingDocumentAuthorization.objects.filter(authorization=loser).select_related("document"))
        for link in links:
            duplicate = SupportingDocumentAuthorization.objects.filter(
                document=link.document,
                authorization=winner,
            ).exclude(pk=link.pk).exists()
            if duplicate:
                counts["SupportingDocumentAuthorization.duplicates_removed"] += 1
                if apply_changes:
                    link.delete()
            else:
                counts["SupportingDocumentAuthorization.authorization"] += 1
                if apply_changes:
                    link.authorization = winner
                    link.save(update_fields=["authorization"])
        return counts

    def _move_fk(self, model, field_name, source, target, apply_changes):
        query = model.objects.filter(**{field_name: source})
        return self._count_or_update(query, apply_changes, **{field_name: target})

    def _count_or_update(self, query, apply_changes, **updates):
        count = query.count()
        if count and apply_changes:
            query.update(**updates)
        return count

    def _terminal_survivor(self, source_user):
        user = source_user.merged_into
        seen = {source_user.id}
        while user and user.merged_into_id and user.id not in seen:
            seen.add(user.id)
            user = user.merged_into
        return user if user and user.id not in seen else None

    def _updated_sort_key(self, record):
        return (record.updated_at, record.id)

    def _result(self, source_user, survivor_user, status, counts):
        return {
            "source_user_id": source_user.id,
            "survivor_user_id": survivor_user.id if survivor_user else None,
            "status": status,
            "counts": counts,
        }
