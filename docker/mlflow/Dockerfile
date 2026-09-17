FROM python:3.12-slim

RUN pip install --no-cache-dir "mlflow>=3.6.0"

# OTLP trace ingestion (mlflow server /v1/traces) requires a SQL backend store,
# not the default file store.
VOLUME ["/mlflow"]
EXPOSE 5000

CMD ["mlflow", "server", \
     "--host", "0.0.0.0", \
     "--port", "5000", \
     "--backend-store-uri", "sqlite:////mlflow/mlflow.db", \
     "--default-artifact-root", "/mlflow/artifacts", \
     "--allowed-hosts", "localhost,localhost:5001,127.0.0.1:5001,mlflow:5000,mlflow", \
     "--cors-allowed-origins", "http://localhost:5001"]
