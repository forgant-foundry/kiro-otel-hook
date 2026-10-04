#!/usr/bin/env bash
# Install the kiro-otel-hook package into the active Python environment and
# register the hook with kiro-cli (3.0+, or 2.13+ launched with --v3).
#
#   scripts/install.sh /path/to/target-repo   per-repo: <repo>/.kiro/hooks/
#   scripts/install.sh --user                 user-level: ~/.kiro/hooks/, fires
#                                             in every workspace (CI runner images)
#
# Use one or the other for a given workspace: if both are present, kiro-cli
# fires the hook twice and every event is recorded twice.
set -euo pipefail

usage() {
    echo "usage: scripts/install.sh /path/to/target-repo" >&2
    echo "       scripts/install.sh --user" >&2
    exit 1
}

[ $# -eq 1 ] || usage

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hook_file="$repo_root/.kiro/hooks/kiro-otel.json"

if [ "$1" = "--user" ]; then
    hooks_dir="$HOME/.kiro/hooks"
    pip install "$repo_root"
else
    target="$1"
    if [ ! -d "$target" ]; then
        echo "error: target directory does not exist: $target" >&2
        exit 1
    fi
    hooks_dir="$target/.kiro/hooks"
    if [ ! -f "$target/.env" ]; then
        cp "$repo_root/.env.example" "$target/.env.example"
        echo "copied .env.example into $target (copy it to .env and fill in OTEL_EXPORTER_OTLP_ENDPOINT)"
    else
        echo "$target/.env already exists, leaving it alone"
    fi
    pip install -e "$repo_root"
fi

mkdir -p "$hooks_dir"
cp "$hook_file" "$hooks_dir/kiro-otel.json"
echo "installed hook config at $hooks_dir/kiro-otel.json"
echo "installed kiro-otel-hook into the active Python environment ($(command -v python3))"

if ! command -v kiro-otel-hook >/dev/null 2>&1; then
    echo "warning: 'kiro-otel-hook' is not on PATH; kiro-cli runs it by that name." >&2
    echo "         Activate this Python environment before starting kiro-cli, or add its bin/ to PATH." >&2
fi
