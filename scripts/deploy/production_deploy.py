"""Deploy and tag the exact commit approved for production."""

import argparse
import re
import subprocess
import time
from pathlib import Path
import requests


REPO = Path(__file__).resolve().parents[2]
LIVE_HOST = "https://authorizations.thebusinessduck.com"
SSH = [
    "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3", "antir@138.68.242.105",
]


def git(*args, capture=False):
    return subprocess.run(
        ["git", "-c", "credential.interactive=false", *args],
        cwd=REPO, check=True, text=True, capture_output=capture,
    ).stdout


def verify_live(version, extra_paths=()):
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(10):
            try:
                login = session.get(f"{LIVE_HOST}/authorizations/login", timeout=8, allow_redirects=False)
                notes = session.get(f"{LIVE_HOST}/roadmap/", timeout=8, allow_redirects=False)
                login_available = login.status_code == 200 and 'name="password"' in login.text
                release_visible = notes.status_code == 200 and re.search(
                    rf"<h2>\s*\[{re.escape(version)}\]\s*-\s*\d{{4}}-\d{{2}}-\d{{2}}\s*</h2>",
                    notes.text,
                )
                if login_available and release_visible:
                    for path in extra_paths:
                        response = session.get(LIVE_HOST + path, timeout=8, allow_redirects=False)
                        if response.status_code != 200:
                            raise RuntimeError(f"Live smoke check failed for {path}.")
                    return
            except requests.RequestException:
                pass
            time.sleep(3)
    raise RuntimeError("Live verification did not pass. No release tag was pushed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Full SHA explicitly approved after final staging")
    parser.add_argument("--version", required=True, help="Approved release version, without v")
    parser.add_argument("--smoke-path", action="append", default=[], help="Additional public path to verify")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{40}", args.commit):
        parser.error("use a full, lowercase commit SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("use a version like 1.2.3")
    if any(not re.fullmatch(r"/[A-Za-z0-9_/-]*", path) or path.startswith("//") for path in args.smoke_path):
        parser.error("smoke paths must be relative to the configured production host")
    tag = f"v{args.version}"
    tag_ref = f"refs/tags/{tag}"

    git("fetch", "--no-tags", "origin", "main")
    git("merge-base", "--is-ancestor", args.commit, "origin/main")
    notes = git("show", f"{args.commit}:CHANGELOG.md", capture=True)
    next_release = re.search(r"(?m)^## \[([0-9]+\.[0-9]+\.[0-9]+)\] - \d{4}-\d{2}-\d{2}$", notes)
    if not next_release or next_release.group(1) != args.version:
        raise RuntimeError("The approved version is not the first numbered release in the changelog.")
    if git("ls-remote", "origin", tag_ref, capture=True).strip():
        raise RuntimeError("The release tag has already been published. Inspect it before retrying.")
    local_tag = git("tag", "--list", tag, capture=True).strip()
    if local_tag and git("rev-list", "-n", "1", tag_ref, capture=True).strip() != args.commit:
        raise RuntimeError("The local release tag points to a different commit.")

    runner = Path(__file__).with_name("deploy_checked_commit.sh").read_bytes().replace(b"\r\n", b"\n")
    subprocess.run(
        [*SSH, f"bash -s -- production {args.commit} main"], input=runner, check=True,
    )
    verify_live(args.version, args.smoke_path)
    revision = subprocess.check_output(
        [*SSH, "cd /home/antir/apps/an_tir_authorizations && "
         "systemctl is-active --quiet an_tir_authorizations.service && git rev-parse HEAD"],
        text=True,
    ).strip()
    if revision != args.commit:
        raise RuntimeError("The production checkout moved during verification. No tag was pushed.")
    if not local_tag:
        git("tag", "-a", tag, args.commit, "-m", f"Release {tag}")
    git("push", "origin", f"{tag_ref}:{tag_ref}")
    published = git("ls-remote", "origin", f"{tag_ref}^{{}}", capture=True).split()
    if not published or published[0] != args.commit:
        raise RuntimeError("The published tag could not be verified.")
    print(f"Live production verification passed and {tag} points to {args.commit}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
