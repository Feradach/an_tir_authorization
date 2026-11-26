"""Use an existing PythonAnywhere staging shell without a browser session."""

import argparse
import base64
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from check_pythonanywhere_access import (
    API_ROOT,
    CREDENTIAL_NAME,
    NoRedirect,
    read_generic_credential,
)


STAGING_APP = "feradach.pythonanywhere.com"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def api(opener, token, path, fields=None):
    data = None if fields is None else urlencode(fields).encode("ascii")
    request = Request(f"{API_ROOT}/{path}", data=data, headers={"Authorization": f"Token {token}"})
    with opener.open(request, timeout=15) as response:
        body = response.read()
        return json.loads(body) if body else None


def run_console(opener, token, console, command):
    endpoint = f"consoles/{console}/"
    output = api(opener, token, endpoint + "get_latest_output/")
    if not ANSI_ESCAPE.sub("", output.get("output", "")).rstrip().endswith("$"):
        raise RuntimeError("The designated shell is unavailable or busy. No command was sent.")

    marker = "ANTIR_DEPLOY_" + secrets.token_hex(8)
    line = f"{command}; result=$?; printf '\\n%s %s\\n' {marker} \"$result\"\n"
    api(opener, token, endpoint + "send_input/", {"input": line})
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        time.sleep(2)
        response = api(opener, token, endpoint + "get_latest_output/")
        tail = ANSI_ESCAPE.sub("", response.get("output", "")).replace("\r", "")
        match = re.search(rf"(?m)^{marker} (\d+)$", tail)
        if match:
            return int(match.group(1))
    raise RuntimeError("The console did not confirm completion. Inspect it before retrying.")


def check_live_staging(opener):
    for _ in range(8):
        try:
            with opener.open(f"https://{STAGING_APP}/authorizations/login", timeout=10) as response:
                if response.status == 200 and b'name="password"' in response.read():
                    return
        except URLError:
            pass
        time.sleep(3)
    raise RuntimeError("Staging reloaded, but the hosted login form failed verification.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", required=True, type=int)
    parser.add_argument("--commit", help="Full commit SHA approved for staging. Omit for a shell probe.")
    parser.add_argument("--ref", default="main", help="Source branch for the approved staging commit.")
    args = parser.parse_args()
    if args.console <= 0:
        parser.error("the console ID must be positive")
    if args.commit and not re.fullmatch(r"[a-f0-9]{40}", args.commit):
        parser.error("use a full, lowercase commit SHA")
    if not re.fullmatch(r"(main|agent/[a-z0-9][a-z0-9/-]*)", args.ref):
        parser.error("use main or a branch under agent/")

    try:
        token = read_generic_credential(CREDENTIAL_NAME)
        if not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
            raise RuntimeError("The stored credential does not contain a valid API token.")
        opener = build_opener(ProxyHandler({}), NoRedirect())
        if args.commit is None:
            command = "test -d /home/feradach/an_tir_authorization/.git"
        else:
            runner = Path(__file__).with_name("deploy_checked_commit.sh").read_bytes()
            encoded = base64.b64encode(runner.replace(b"\r\n", b"\n")).decode("ascii")
            command = f"printf %s {encoded} | base64 -d | bash -s -- staging {args.commit} {args.ref}"
        status = run_console(opener, token, args.console, command)
        if status:
            raise RuntimeError(f"The staging shell exited with status {status}. The app was not reloaded.")
        if args.commit is None:
            print("PythonAnywhere staging shell executed the read-only probe through the API.")
            return 0
        api(opener, token, f"webapps/{STAGING_APP}/reload/", {})
        check_live_staging(opener)
        print(f"Staging reloaded at {args.commit}; the hosted login page passed its smoke check.")
        return 0
    except HTTPError as error:
        print(f"PythonAnywhere API returned HTTP {error.code}.", file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as error:
        # Raw transport or credential errors can include details unrelated to deployment.
        print(str(error) if isinstance(error, RuntimeError) else "Staging API access failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
