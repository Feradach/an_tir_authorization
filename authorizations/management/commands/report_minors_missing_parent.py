import csv
from io import StringIO

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError

from authorizations.models import Person, SYSTEM_USER_IDS


class Command(BaseCommand):
    help = "Report current minors missing parent ID and parent name information."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email-to",
            default="viperzka@gmail.com",
            help="Comma-separated email recipient list for the report.",
        )
        parser.add_argument(
            "--subject",
            default="An Tir minors missing parent information",
            help="Email subject for the report.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Print the report only; do not send email.",
        )

    def handle(self, *args, **options):
        rows = self._build_rows()
        report = self._build_text_report(rows)
        csv_report = self._build_csv_report(rows)

        self.stdout.write(self._stdout_safe(report))

        if options["no_email"]:
            return

        recipients = self._parse_recipients(options.get("email_to"))
        if not recipients:
            return
        sender = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        if not sender:
            raise CommandError("DEFAULT_FROM_EMAIL must be configured to send the report.")

        message = EmailMessage(
            options["subject"],
            report,
            sender,
            recipients,
        )
        message.attach("minors_missing_parent_info.csv", csv_report, "text/csv")
        message.send(fail_silently=False)

    def _parse_recipients(self, raw_value):
        if not raw_value:
            return []
        return [email.strip() for email in raw_value.split(",") if email.strip()]

    def _stdout_safe(self, report):
        encoding = getattr(self.stdout, "encoding", None) or "utf-8"
        return report.encode(encoding, errors="replace").decode(encoding)

    def _build_rows(self):
        people = (
            Person.objects.select_related("user", "branch")
            .exclude(user_id__in=SYSTEM_USER_IDS)
            .filter(parent__isnull=True)
            .filter(parent_first_name="", parent_last_name="", parent_sca_name="")
            .order_by("sca_name", "user_id")
        )
        return [
            person
            for person in people
            if person.is_current_minor
        ]

    def _build_text_report(self, rows):
        lines = [
            "Current minors missing parent information",
            "",
            f"Count: {len(rows)}",
        ]
        if rows:
            lines.extend(["", "Records:"])
            for person in rows:
                user = person.user
                lines.append(
                    "- user_id={user_id}, sca_name=\"{sca_name}\", legal_name=\"{first} {last}\", "
                    "birthday={birthday}, branch=\"{branch}\"".format(
                        user_id=person.user_id,
                        sca_name=person.sca_name or "",
                        first=user.first_name or "",
                        last=user.last_name or "",
                        birthday=user.birthday or "",
                        branch=person.branch.name if person.branch else "",
                    )
                )
        return "\n".join(lines)

    def _build_csv_report(self, rows):
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "User ID",
            "SCA Name",
            "First Name",
            "Last Name",
            "Birthday",
            "Branch",
        ])
        for person in rows:
            user = person.user
            writer.writerow([
                person.user_id,
                person.sca_name or "",
                user.first_name or "",
                user.last_name or "",
                user.birthday.isoformat() if user.birthday else "",
                person.branch.name if person.branch else "",
            ])
        return output.getvalue()
