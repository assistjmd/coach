"""idempotency constraints for point measurements

Workouts, sleep sessions, and readiness scores already had unique constraints, so
re-syncing them was a no-op. HRV, heart-rate, and body-metric rows had none and
were written with a plain INSERT, so every re-sync or HealthKit re-ingest
duplicated them.

These three are point measurements, so a natural key already exists — one source
cannot report two different values for the same instant. That avoids adding a
`source_record_id` column, which would have required coordinated changes to the
ingest schema and the iOS DTOs that populate it.

Existing duplicates are collapsed first, keeping the physically-first row of each
group. On a fresh install these DELETEs are no-ops.

Revision ID: cf861f74da19
Revises: 47553bf72f5f
Create Date: 2026-08-04 00:22:12.227156+00:00
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "cf861f74da19"
down_revision: Union[str, None] = "47553bf72f5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, constraint name, key columns)
_CONSTRAINTS = [
    ("hrv_readings", "uq_hrv_measured_at", ["user_id", "source", "measured_at"]),
    ("heart_rate_samples", "uq_hr_measured_at", ["user_id", "source", "measured_at"]),
    (
        "body_metrics",
        "uq_body_metric_measured_at",
        ["user_id", "source", "metric", "measured_at"],
    ),
]


def upgrade() -> None:
    for table, name, cols in _CONSTRAINTS:
        predicate = " AND ".join(f"a.{c} = b.{c}" for c in cols)
        # ctid is the physical row address — the standard way to break ties
        # between rows that are otherwise identical on the key.
        op.execute(
            f"DELETE FROM {table} a USING {table} b WHERE a.ctid > b.ctid AND {predicate}"
        )
        op.create_unique_constraint(name, table, cols)


def downgrade() -> None:
    for table, name, _ in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique")
