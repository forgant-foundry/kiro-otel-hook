#!/usr/bin/env bash
# Copy this hook into another project's .kiro/ directory and install the
# package into whatever Python environment is currently active.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: scripts/install.sh /path/to/target-repo" >&2
    exit 1
fi

target="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$target" ]; then
    echo "error: target directory does not exist: $target" >&2
    exit 1
fi

mkdir -p "$target/.kiro/hooks"
cp "$repo_root/.kiro/hooks/mlflow-otel-metrics.json" "$target/.kiro/hooks/mlflow-otel-metrics.json"
echo "copied .kiro/hooks/mlflow-otel-metrics.json into $target"

if [ ! -f "$target/.env" ]; then
    cp "$repo_root/.env.example" "$target/.env.example"
    echo "copied .env.example into $target (copy it to .env and fill in OTEL_EXPORTER_OTLP_ENDPOINT)"
else
    echo "$target/.env already exists, leaving it alone"
fi

pip install -e "$repo_root"
echo "installed kiro-mlflow-hook into the active Python environment ($(command -v python3))"
