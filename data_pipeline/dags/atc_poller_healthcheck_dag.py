"""Supervisory check on the always-on opensky-poller service. Gives Airflow a genuine role
over the hot-path ingestion pipeline without trying to do the sub-minute polling itself.
"""

import logging
import os
from datetime import datetime, timedelta

import psycopg2
from airflow.sdk import dag, task

STALL_THRESHOLD = timedelta(minutes=1)

log = logging.getLogger(__name__)


def _connect():
    return psycopg2.connect(os.environ["POSTGIS_DSN"])


@dag(
    dag_id="atc_poller_healthcheck",
    schedule="*/2 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["housekeeping", "phase1"],
)
def atc_poller_healthcheck():
    @task
    def check_last_successful_poll() -> None:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT MAX(poll_completed_at) FROM ingestion_audit_log WHERE error IS NULL")
            (last_success,) = cur.fetchone()

        if last_success is None:
            log.warning("No successful poll has ever been recorded — is opensky-poller running?")
            return

        # last_success is timezone-aware (timestamptz); compare against an aware now().
        staleness = datetime.now(last_success.tzinfo) - last_success
        if staleness > STALL_THRESHOLD:
            # Stub: replace with a real alert hook (Slack webhook, PagerDuty, etc.) later.
            log.warning(
                "opensky-poller looks stalled: last successful poll was %s ago (threshold %s)",
                staleness,
                STALL_THRESHOLD,
            )

    check_last_successful_poll()


atc_poller_healthcheck()
