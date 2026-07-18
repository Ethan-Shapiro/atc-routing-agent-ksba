# Phase 3 (LangGraph orchestration) — stub only. Not built by a plain `docker compose up` (see
# profiles in docker-compose.yml). Fill in once agent_orchestration/main.py has a real FastAPI app.
FROM python:3.12-slim

WORKDIR /app

COPY agent_orchestration/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_orchestration/ .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
