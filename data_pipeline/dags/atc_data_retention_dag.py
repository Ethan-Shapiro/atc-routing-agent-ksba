"""Minute-scale housekeeping over the domain PostGIS store. Airflow's job here is deliberately
limited to this kind of coarse-cadence maintenance — the hot-path OpenSky polling lives in the
standalone opensky-poller service (data_pipeline/poller/), not in a DAG. See docker-compose.yml
and the project plan for why.
"""

import os
from datetime import datetime, timedelta

import psycopg2
from airflow.sdk import dag, task

STALE_AFTER = timedelta(minutes=2)
HISTORY_RETENTION = timedelta(days=7)


def _connect():
    return psycopg2.connect(os.environ["POSTGIS_DSN"])


@dag(
    dag_id="atc_data_retention",
    schedule="*/15 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["housekeeping", "phase1"],
)
def atc_data_retention():
    @task
    def resolve_anomalies_for_departing_aircraft() -> int:
        """Aircraft that vanish from the bbox entirely (rather than moving apart while still
        visible) never re-fire the proximity trigger, so any anomaly referencing them would
        stay open forever without this step."""
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE anomaly_events
                SET resolved_at = now(), resolution_notes = 'aircraft left coverage area'
                WHERE resolved_at IS NULL
                  AND (aircraft_icao24_1 IN (
                          SELECT icao24 FROM aircraft_state_current
                          WHERE last_contact < now() - interval '%s seconds'
                      )
                      OR aircraft_icao24_2 IN (
                          SELECT icao24 FROM aircraft_state_current
                          WHERE last_contact < now() - interval '%s seconds'
                      ))
                """,
                (STALE_AFTER.total_seconds(), STALE_AFTER.total_seconds()),
            )
            return cur.rowcount

    @task
    def delete_stale_current_rows(_resolved: int) -> int:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM aircraft_state_current WHERE last_contact < now() - interval '%s seconds'",
                (STALE_AFTER.total_seconds(),),
            )
            return cur.rowcount

    @task
    def prune_old_history() -> int:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM aircraft_state_history WHERE ingested_at < now() - interval '%s seconds'",
                (HISTORY_RETENTION.total_seconds(),),
            )
            return cur.rowcount

    resolved = resolve_anomalies_for_departing_aircraft()
    delete_stale_current_rows(resolved)
    prune_old_history()


atc_data_retention()
