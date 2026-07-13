#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONDONTWRITEBYTECODE=1
export RAG_ENV_FILE=/dev/null
export PYTHONPATH="$ROOT_DIR/RAG${PYTHONPATH:+:$PYTHONPATH}"
unset ADMIN_RUNTIME

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/iso42001rag-verify.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT
export TMPDIR="$TMP_ROOT"

section() {
    printf '\n== %s ==\n' "$1"
}

section "Compose configuration"
ADMIN_CARD_SERIALS=0000000 docker compose \
    --env-file .env.example \
    config --quiet
ADMIN_CARD_SERIALS=0000000 docker compose \
    --env-file .env.example \
    -f docker-compose.yaml \
    -f docker-compose.hardening.yml \
    config --quiet

expected_services="admin
code-server
db
embed-proxy
jupyter
keycloak
monitoring
nginx
openwebui
rag-api"
actual_services="$(ADMIN_CARD_SERIALS=0000000 docker compose \
    --env-file .env.example config --services | sort)"
if [[ "$actual_services" != "$expected_services" ]]; then
    printf 'Unexpected Compose services:\n%s\n' "$actual_services" >&2
    exit 1
fi

ADMIN_CARD_SERIALS=0000000 docker compose \
    --env-file .env.example \
    -f docker-compose.yaml \
    -f docker-compose.hardening.yml \
    config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
if config.get("name") != "iso42001rag":
    raise SystemExit("Compose project name must be iso42001rag")
ports = config["services"]["admin"].get("ports", [])
if not any(p.get("published") == "8300" and p.get("host_ip") == "127.0.0.1" for p in ports):
    raise SystemExit("hardening must bind admin:8300 to 127.0.0.1")
'
printf 'Compose project and 10-service topology: OK\n'

section "Tracked secret check"
IS_GIT_WORKTREE=false
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    IS_GIT_WORKTREE=true
    tracked_tls_keys="$(git ls-files 'nginx/ssl/*.key')"
    while IFS= read -r tracked_key; do
        [[ -z "$tracked_key" || ! -e "$tracked_key" ]] || {
            printf 'TLS private keys must not be present in the tracked working tree.\n' >&2
            exit 1
        }
    done <<< "$tracked_tls_keys"
    printf 'No tracked nginx TLS private key present in the working tree: OK\n'
else
    printf 'No Git metadata in offline package; tracked-key check skipped.\n'
fi

section "Shell syntax"
if [[ "$IS_GIT_WORKTREE" == true ]]; then
    while IFS= read -r -d '' script; do
        bash -n "$script"
    done < <(git ls-files -z -- '*.sh')
else
    while IFS= read -r -d '' script; do
        bash -n "$script"
    done < <(find . -type f -name '*.sh' -print0)
fi
bash -n scripts/verify_project.sh
printf 'bash -n: OK\n'

run_pytest() {
    local suite="$1"
    section "pytest $suite"
    python3 -m pytest -q -p no:cacheprovider "$suite"
}

if [[ -d tests ]]; then
    run_pytest tests
fi
run_pytest RAG/tests
run_pytest monitoring_addon/tests
run_pytest admin_console/tests

printf '\nAll local verification checks passed.\n'
