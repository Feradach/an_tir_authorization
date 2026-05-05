import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Gmail API OAuth token for the Django email backend."
    )
    parser.add_argument(
        "--client-secret",
        required=True,
        help="Path to the Google OAuth client_secret JSON file.",
    )
    parser.add_argument(
        "--token-file",
        required=True,
        help="Path where the authorized token JSON should be written.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local callback port. Defaults to an automatically selected port.",
    )
    args = parser.parse_args()

    client_secret = Path(args.client_secret).expanduser().resolve()
    token_file = Path(args.token_file).expanduser().resolve()
    token_file.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    creds = flow.run_local_server(port=args.port)

    token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"Wrote Gmail API token to {token_file}")


if __name__ == "__main__":
    main()
