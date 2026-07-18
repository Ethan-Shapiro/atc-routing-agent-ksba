# Custom Airflow image with project dependencies baked in at build time. Deliberately not using
# _PIP_ADDITIONAL_REQUIREMENTS at runtime — Airflow's own docs discourage that beyond quick
# experiments (slow container start, non-reproducible builds).
FROM apache/airflow:3.3.0-python3.11

COPY data_pipeline/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
