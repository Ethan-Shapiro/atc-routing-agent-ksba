# Phase 2 (RAG/MCP) — stub only. Not built by a plain `docker compose up` (see profiles in
# docker-compose.yml). Fill in once mcp_server/server.py has a real implementation.
FROM python:3.12-slim

WORKDIR /app

COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/ .

CMD ["python", "server.py"]
