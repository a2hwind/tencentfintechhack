# Internal Brain API: FastAPI + mock platforms + sync worker + audit log in one process.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BRAIN_DATA_DIR=/data

COPY pyproject.toml README.md ./
COPY internal_brain ./internal_brain
RUN pip install --no-cache-dir .
# The operator scripts (smoke_llm, slack_setup, quality_eval) run inside the container too:
#   docker compose exec api python scripts/slack_setup.py check
COPY scripts ./scripts

# Index, audit chain and the audit signing key live here; mount a volume to keep them.
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "internal_brain.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
