---
inclusion: manual
---

# Installing kiro-mlflow-hook into another project

Referenced on demand as `#install-elsewhere` when a user wants this hook
running in a repo other than this one, or on every workspace of a machine.

Requires kiro-cli 3.0+ (or 2.13+ started with `kiro-cli chat --v3`); the
default 2.x engine does not read `.kiro/hooks/*.json` files at all.

1. Pick one install mode, never both for the same workspace (both means
   every event is recorded twice):
   - **Per repo:** `scripts/install.sh /path/to/target-repo`. It copies
     `.kiro/hooks/mlflow-otel-metrics.json` into the target repo's
     `.kiro/hooks/`, copies `.env.example` into the target repo root (if no
     `.env` exists there), and `pip install -e`'s this package into the
     active Python environment.
   - **User level:** `scripts/install.sh --user`. It installs the package
     and writes the hook config to `~/.kiro/hooks/`, so it fires in every
     workspace. This is the right choice for CI runner images; see the
     README's "Running it on a GitLab runner" section.
2. Make sure the `kiro-mlflow-hook` command is on `PATH` when kiro-cli
   starts (activate the venv it was installed into). The hook config runs
   it by that name.
3. Set `OTEL_EXPORTER_OTLP_ENDPOINT` for wherever their collector actually
   lives (their own stack, this repo's docker-compose stack, or a
   production collector). Leave `KIRO_OTEL_GENAI_DETAIL` at its default
   (`metadata`) unless the user explicitly wants prompt/tool content
   captured; `full` can export source code, secrets, or PII.
4. If they have no collector yet, point `OTEL_EXPORTER_OTLP_ENDPOINT` at
   this repo's validation stack (`docker compose up -d` here first) to
   confirm the hook fires correctly, then swap to their real endpoint.
5. Confirm activation the same way as in this repo: pipe a fake JSON
   payload into `kiro-mlflow-hook` and check it exits 0.
