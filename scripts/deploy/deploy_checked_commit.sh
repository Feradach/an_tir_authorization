#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: deploy_checked_commit.sh staging|production FULL_SHA REF" >&2
    exit 2
fi

environment=$1
approved_sha=$2
branch=$3
[[ $approved_sha =~ ^[a-f0-9]{40}$ ]]
[[ $branch =~ ^(main|agent/[a-z0-9][a-z0-9/-]*)$ ]]
git check-ref-format "refs/heads/$branch"

case "$environment" in
    staging)
        app_dir=/home/feradach/an_tir_authorization
        python=/home/feradach/.virtualenvs/antir/bin/python
        ;;
    production)
        [[ $branch == main && $(hostname -s) == antir-authorizations ]]
        app_dir=/home/antir/apps/an_tir_authorizations
        python="$app_dir/venv/bin/python"
        sudo -n -l -- /usr/bin/systemctl restart an_tir_authorizations.service
        systemctl is-active --quiet an_tir_authorizations.service
        for service in an-tir-auth-backup-local.service an-tir-auth-backup-upload.service; do
            [[ $(systemctl show -p Result --value "$service") == success ]]
            completed=$(systemctl show -p ExecMainExitTimestamp --value "$service")
            [[ -n $completed ]]
            age=$(( $(date +%s) - $(date -d "$completed" +%s) ))
            [[ $age -ge 0 && $age -lt 172800 ]] || {
                echo "$service has no successful run within the last two days." >&2
                exit 1
            }
            echo "$service last completed: $completed"
        done
        ;;
    *)
        echo "Unknown deployment environment." >&2
        exit 2
        ;;
esac

cd "$app_dir"
exec 9>"$(git rev-parse --git-path an-tir-deploy.lock)"
flock -n 9 || { echo "Another deployment is in progress." >&2; exit 1; }
if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo "Tracked checkout changes need review." >&2
    exit 1
fi

previous_sha=$(git rev-parse HEAD)
echo "Previous checkout: $previous_sha"
git fetch --no-tags origin "refs/heads/$branch"
git merge-base --is-ancestor "$approved_sha" FETCH_HEAD
git checkout --detach "$approved_sha"
[[ $(git rev-parse HEAD) == "$approved_sha" ]]
"$python" -m pip install -r requirements.txt

"$python" manage.py check
if [[ $environment == production ]]; then
    "$python" manage.py check_release_ready
fi

"$python" manage.py migrate --plan
if ! "$python" manage.py migrate --check; then
    echo "Pending migrations require the reviewed migration procedure before restart." >&2
    exit 1
fi

"$python" manage.py collectstatic --noinput
if [[ $environment == production ]]; then
    previous_start=$(systemctl show -p ExecMainStartTimestampMonotonic --value an_tir_authorizations.service)
    sudo -n -- /usr/bin/systemctl restart an_tir_authorizations.service
    systemctl is-active --quiet an_tir_authorizations.service
    new_start=$(systemctl show -p ExecMainStartTimestampMonotonic --value an_tir_authorizations.service)
    [[ $new_start =~ ^[1-9][0-9]*$ && $new_start != "$previous_start" ]]
fi
echo "Checked out and validated: $approved_sha"
