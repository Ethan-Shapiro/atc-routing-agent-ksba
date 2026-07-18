FROM python:3.12-slim

WORKDIR /app

COPY data_pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data_pipeline/poller/ .

CMD ["python", "main.py"]
