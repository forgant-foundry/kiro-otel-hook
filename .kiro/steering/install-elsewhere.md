---
inclusion: manual
---

# Installing kiro-mlflow-hook into another project

Referenced on demand as `#install-elsewhere` when a user wants this hook
running in a repo other than this one.

1. From this repo, run `scripts/install.sh /path/to/target-repo`. It:
   - copies `.kiro/hooks/mlflow-otel-metrics.json` into the target repo's
     `.kiro/hooks/`
   - copies `.env.example` into the target repo root (if one doesn't
     already exist there)
   - installs the `kiro-mlflow-hook` package into whatever Python
     environment is active when the script runs
2. In the target repo, copy `.env.example` to `.env`, fill in
   `OTEL_EXPORTER_OTLP_ENDPOINT` for wherever their collector actually
   lives (their own docker-compose stack, or this repo's, or a production
   collector), and `source .env` before starting `kiro-cli`.
3. If the target repo has no OTel Collector / MLflow of its own, point
   `OTEL_EXPORTER_OTLP_ENDPOINT` at this repo's validation stack
   (`docker compose up -d` here first) to confirm the hook fires correctly,
   then swap to their real endpoint.
4. Confirm activation the same way as in this repo: run the hook by hand
   with a fake JSON payload on stdin and check it exits 0.
