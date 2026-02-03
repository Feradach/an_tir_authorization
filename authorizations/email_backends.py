import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class GmailAPIBackend(BaseEmailBackend):
    """
    Django email backend that sends mail via Gmail API (HTTPS).
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        creds = Credentials.from_authorized_user_file(
            settings.GMAIL_TOKEN_FILE,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

        service = build("gmail", "v1", credentials=creds)

        sent_count = 0

        for message in email_messages:
            mime_message = MIMEMultipart()
            mime_message["To"] = ", ".join(message.to)
            mime_message["From"] = message.from_email
            mime_message["Subject"] = message.subject

            mime_message.attach(
                MIMEText(message.body, message.content_subtype or "plain")
            )

            raw_message = base64.urlsafe_b64encode(
                mime_message.as_bytes()
            ).decode()

            service.users().messages().send(
                userId="me",
                body={"raw": raw_message},
            ).execute()

            sent_count += 1

        return sent_count
