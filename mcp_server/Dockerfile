FROM python:3.12-slim

# poppler-utils provides pdftotext, used by vector_db/ingest.py for section-aware chunking.
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY mcp_server/requirements.txt .
# CPU-only torch wheel first — sentence-transformers otherwise pulls the full CUDA toolkit
# (~2GB of unused nvidia-* packages) even though this image never touches a GPU.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY mcp_server/ .

CMD ["python", "server.py"]
