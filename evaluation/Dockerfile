FROM python:3.12-slim

WORKDIR /app

COPY evaluation/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY evaluation/ .

CMD ["python", "score_episode.py", "--help"]
