#!/usr/bin/env python3
"""Production DAG entrypoint for Airflow scheduler discovery.

This file is intentionally thin: it imports the project module and exposes a
single DAG object so Airflow can scan it from the dags directory.
"""

from __future__ import annotations

from xiaolinnote_agent_analysis.examples.agent_capabilities_airflow_langgraph import (
    AIRFLOW_AVAILABLE,
    create_airflow_dag,
)

# Airflow scans module globals named `dag` by default in many deployments.
dag = create_airflow_dag() if AIRFLOW_AVAILABLE else None
