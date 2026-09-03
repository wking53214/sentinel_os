#!/usr/bin/env bash
# Back up the governance ledger, then prove the backup restores AND that the
# hash chain survives the round trip. A backup you have not restored and
# verified is not a backup.
#
#   scripts/ledger_backup_verify.sh [OUTFILE]
#
# OUTFILE defaults to ledger-<UTC-timestamp>.dump (custom pg_dump format).
# Reads POSTGRES_HOST/PORT/DB/USER/PASSWORD (defaults localhost:5432
# iceberg/iceberg/iceberg). Needs pg_dump / pg_restore / createdb / dropdb on
# PATH and CREATEDB on the connecting role. The throwaway verify database is
# always dropped, success or failure.
set -euo pipefail

HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"
DB="${POSTGRES_DB:-iceberg}"
USER="${POSTGRES_USER:-iceberg}"
export PGPASSWORD="${POSTGRES_PASSWORD:-iceberg}"
OUT="${1:-ledger-$(date -u +%Y%m%dT%H%M%SZ).dump}"
VERIFY_DB="ledger_verify_$$"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() { dropdb -h "$HOST" -p "$PORT" -U "$USER" --if-exists "$VERIFY_DB" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[1/3] pg_dump ${USER}@${HOST}:${PORT}/${DB}  ->  ${OUT}"
pg_dump -h "$HOST" -p "$PORT" -U "$USER" -Fc --no-owner --no-privileges "$DB" -f "$OUT"
echo "      $(du -h "$OUT" | cut -f1)"

echo "[2/3] restore into a throwaway database (${VERIFY_DB})"
createdb -h "$HOST" -p "$PORT" -U "$USER" "$VERIFY_DB"
pg_restore -h "$HOST" -p "$PORT" -U "$USER" -d "$VERIFY_DB" --no-owner --no-privileges "$OUT" \
  || echo "      (pg_restore reported non-fatal warnings -- verify_chain below is the real check)"

echo "[3/3] verify_chain() on the restored copy"
POSTGRES_DB="$VERIFY_DB" python3 "${HERE}/verify_ledger.py"

echo
echo "OK -- ${OUT} restores and its hash chain verifies clean."
echo "Note: a clean verify_chain() here does NOT prove the source was never"
echo "tampered before the dump -- that is what the independently-held twin is"
echo "for. See AUDIT_PLAYBOOK.md."
