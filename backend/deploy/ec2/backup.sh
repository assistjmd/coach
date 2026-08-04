#!/usr/bin/env bash
# Nightly Postgres backup → S3. Schedule via cron at 03:00 UTC.
set -euo pipefail

source /etc/coach/env

STAMP=$(date -u +%Y%m%d-%H%M%S)
TMP=/tmp/coach-$STAMP.sql.gz
pg_dump -U coach -h localhost coach | gzip > "$TMP"

if [ -n "${BACKUP_BUCKET:-}" ]; then
  aws s3 cp "$TMP" "s3://${BACKUP_BUCKET}/coach-${STAMP}.sql.gz" --region "${AWS_REGION:-us-east-2}"
fi

# Keep 7 days locally
find /tmp -maxdepth 1 -name "coach-*.sql.gz" -mtime +7 -delete
