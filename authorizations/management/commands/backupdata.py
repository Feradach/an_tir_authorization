import os
import sys
import subprocess
import tempfile
from django.core.management.base import BaseCommand
from django.core.mail import EmailMessage
from django.conf import settings

class Command(BaseCommand):
    help = "Dump all data and email the backup file as a JSON attachment"

    def handle(self, *args, **options):
        try:
            # Use sys.executable to ensure the correct interpreter is used
            backup_data = subprocess.check_output(
                [sys.executable, 'manage.py', 'dumpdata', '--indent', '2'],
                stderr=subprocess.STDOUT
            )
        except subprocess.CalledProcessError as e:
            error_output = e.output.decode('utf-8')
            self.stderr.write("Error running dumpdata: " + error_output)
            return

        # Open the temporary file with UTF-8 encoding to avoid encoding errors
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json', encoding='utf-8') as tmp_file:
            tmp_file.write(backup_data.decode('utf-8', errors='replace'))
            tmp_path = tmp_file.name

        self.stdout.write("Backup file created at: " + tmp_path)

        # Ensure DEFAULT_FROM_EMAIL is set
        default_from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
        if not default_from_email:
            self.stderr.write("DEFAULT_FROM_EMAIL is not set in settings.")
            return

        # Prepare the email
        email = EmailMessage(
            subject="Django Backup Data",
            body="Attached is the latest backup of the Django database in JSON format.",
            from_email=default_from_email,
            to=["viperzka@gmail.com"],  # Replace with your actual email address
        )
        email.attach_file(tmp_path)
        try:
            email.send(fail_silently=False)
            self.stdout.write(self.style.SUCCESS("Backup emailed successfully."))
        except Exception as e:
            self.stderr.write("Failed to send email: " + str(e))
