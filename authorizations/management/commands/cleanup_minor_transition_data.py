from datetime import date

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.models import Person, SYSTEM_USER_IDS


class Command(BaseCommand):
    help = "Clean stale birthday, parent, and transitional minor-status data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without applying database updates.",
        )
        parser.add_argument(
            "--email-to",
            help="Comma-separated email recipient list for the cleanup report.",
        )
        parser.add_argument(
            "--subject",
            default="An Tir authorization minor transition cleanup report",
            help="Email subject for the cleanup report.",
        )

    def handle(self, *args, **options):
        recipients = self._parse_recipients(options.get("email_to"))
        dry_run = options["dry_run"]
        actions = self._build_actions()
        report = self._build_report(actions, dry_run=dry_run)

        if not dry_run:
            self._apply_actions(actions)
            report = self._build_report(actions, dry_run=False, applied=True)

        self.stdout.write(self._stdout_safe(report))

        if recipients:
            sender = getattr(settings, "DEFAULT_FROM_EMAIL", None)
            if not sender:
                raise CommandError("DEFAULT_FROM_EMAIL must be configured to send the cleanup report.")
            send_mail(
                options["subject"],
                report,
                sender,
                recipients,
                fail_silently=False,
            )

    def _parse_recipients(self, raw_value):
        if not raw_value:
            return []
        return [email.strip() for email in raw_value.split(",") if email.strip()]

    def _stdout_safe(self, report):
        encoding = getattr(self.stdout, "encoding", None) or "utf-8"
        return report.encode(encoding, errors="replace").decode(encoding)

    def _build_actions(self):
        actions = []
        today = date.today()
        people = (
            Person.objects.select_related("user", "parent")
            .exclude(user_id__in=SYSTEM_USER_IDS)
            .order_by("user_id")
        )
        for person in people:
            birthday = person.user.birthday
            inferred_minor = person.is_current_minor
            clear_birthday = bool(birthday and today >= birthday + relativedelta(years=20))
            clear_parent = bool(person.parent_id and not inferred_minor)
            stored_minor_mismatch = person.is_minor != inferred_minor

            if clear_birthday:
                inferred_minor = False
                stored_minor_mismatch = person.is_minor is not False
                clear_parent = bool(person.parent_id)

            if clear_birthday or clear_parent or stored_minor_mismatch:
                actions.append(
                    {
                        "person": person,
                        "clear_birthday": clear_birthday,
                        "clear_parent": clear_parent,
                        "old_is_minor": person.is_minor,
                        "new_is_minor": inferred_minor,
                        "stored_minor_mismatch": person.is_minor != inferred_minor,
                    }
                )
        return actions

    def _apply_actions(self, actions):
        with transaction.atomic():
            for action in actions:
                person = action["person"]
                user_changed = False
                person_changed = False

                if action["clear_birthday"] and person.user.birthday is not None:
                    person.user.birthday = None
                    user_changed = True

                if action["clear_parent"] and person.parent_id is not None:
                    person.parent = None
                    person_changed = True

                if person.is_minor != action["new_is_minor"]:
                    person.is_minor = action["new_is_minor"]
                    person_changed = True

                if user_changed:
                    person.user.save(update_fields=["birthday", "updated_at"])
                if person_changed:
                    person.save(update_fields=["is_minor", "parent", "updated_at"])

    def _build_report(self, actions, *, dry_run, applied=False):
        clear_birthday_count = sum(1 for action in actions if action["clear_birthday"])
        clear_parent_count = sum(1 for action in actions if action["clear_parent"])
        minor_true_count = sum(1 for action in actions if not action["old_is_minor"] and action["new_is_minor"])
        minor_false_count = sum(1 for action in actions if action["old_is_minor"] and not action["new_is_minor"])

        if dry_run:
            title = "Minor transition cleanup dry run"
            footer = "No changes were applied. Re-run without --dry-run to apply."
        elif applied:
            title = "Minor transition cleanup applied"
            footer = "Changes were applied."
        else:
            title = "Minor transition cleanup"
            footer = ""

        lines = [
            title,
            "",
            f"People to inspect/change: {len(actions)}",
            f"Birthdays to clear for people age 20+: {clear_birthday_count}",
            f"Adult parent links to clear: {clear_parent_count}",
            f"Stored is_minor false -> true: {minor_true_count}",
            f"Stored is_minor true -> false: {minor_false_count}",
        ]

        if actions:
            lines.extend(["", "Records:"])
            for action in actions:
                person = action["person"]
                user = person.user
                changes = []
                if action["clear_birthday"]:
                    changes.append("clear birthday")
                if action["clear_parent"]:
                    changes.append(f"clear parent_id={person.parent_id}")
                if action["stored_minor_mismatch"]:
                    changes.append(f"is_minor {action['old_is_minor']} -> {action['new_is_minor']}")
                lines.append(
                    "- user_id={user_id}, sca_name=\"{sca_name}\", birthday={birthday}, "
                    "country={country}, state_province={state_province}: {changes}".format(
                        user_id=person.user_id,
                        sca_name=person.sca_name or "",
                        birthday=user.birthday or "",
                        country=user.country or "",
                        state_province=user.state_province or "",
                        changes=", ".join(changes),
                    )
                )

        if footer:
            lines.extend(["", footer])
        return "\n".join(lines)
