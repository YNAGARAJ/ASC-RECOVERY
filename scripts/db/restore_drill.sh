#!/usr/bin/env bash
# F-21 (docs/audit/REGISTER.md): automates the restore-verification
# procedure documented in docs/RUNBOOK.md's "Restore (backup verification)"
# section -- restore the most recent automated backup to a throwaway
# instance, verify it, record the wall-clock time, tear it down.
#
# This script has NEVER been run against a real AWS or Azure account --
# no such account exists in the environment it was authored in, same
# disclosure as security/kms_aws.py and security/kms_azure.py. Writing it
# does not close F-21; the register entry stays OPEN until someone runs it
# for real and records the actual number ("restore rehearsed and timed" in
# the Phase 9 gate means a real number, not a script that could produce
# one). What this closes is "the procedure only exists as prose someone
# has to manually transcribe into commands" -- the commands themselves,
# and the timing/teardown discipline around them, are now pinned down.
#
# Usage:
#   CLOUD=aws \
#   AWS_SOURCE_DB_IDENTIFIER=asc-recovery-prod-postgres \
#   DATABASE_URL_TEMPLATE='postgresql://asc_owner:PASSWORD@%s:5432/asc_recovery?sslmode=require' \
#   AWS_DB_MASTER_PASSWORD='...' \
#     ./scripts/db/restore_drill.sh
#
#   CLOUD=azure \
#   AZURE_SOURCE_SERVER_NAME=asc-recovery-prod-postgres \
#   AZURE_RESOURCE_GROUP=asc-recovery-prod-rg \
#   DATABASE_URL_TEMPLATE='postgresql://asc_owner:PASSWORD@%s:5432/asc_recovery?sslmode=require' \
#   AZURE_DB_ADMIN_PASSWORD='...' \
#     ./scripts/db/restore_drill.sh
#
# Requires: the `aws` or `az` CLI (matching $CLOUD), `psql`, `alembic`
# (PYTHONPATH=src, same as every other script under scripts/), already
# authenticated against the target account/subscription.

set -euo pipefail

CLOUD="${CLOUD:?set CLOUD=aws or CLOUD=azure}"
DATABASE_URL_TEMPLATE="${DATABASE_URL_TEMPLATE:?set DATABASE_URL_TEMPLATE, a psql connection string with a single %s for the restored host}"

DRILL_ID="restore-drill-$(date -u +%Y%m%dt%H%M%Sz)"
START_EPOCH=$(date +%s)

log() {
    echo "[$(date -u +%H:%M:%S)] $*"
}

elapsed_seconds() {
    echo $(( $(date +%s) - START_EPOCH ))
}

cleanup() {
    local exit_code=$?
    if [[ "${SKIP_TEARDOWN:-}" == "1" ]]; then
        log "SKIP_TEARDOWN=1 set -- leaving ${DRILL_ID} in place, tear it down by hand."
        return
    fi
    log "Tearing down ${DRILL_ID} (avoid double-billing an idle drill instance)..."
    case "$CLOUD" in
        aws)
            aws rds delete-db-instance \
                --db-instance-identifier "$DRILL_ID" \
                --skip-final-snapshot >/dev/null || true
            ;;
        azure)
            az postgres flexible-server delete \
                --resource-group "$AZURE_RESOURCE_GROUP" \
                --name "$DRILL_ID" \
                --yes >/dev/null || true
            ;;
    esac
    exit "$exit_code"
}
trap cleanup EXIT

case "$CLOUD" in
    aws)
        SOURCE_DB_IDENTIFIER="${AWS_SOURCE_DB_IDENTIFIER:?set AWS_SOURCE_DB_IDENTIFIER}"
        log "Restoring ${SOURCE_DB_IDENTIFIER} to a new instance ${DRILL_ID} " \
            "from the latest automated backup (point-in-time restore, now)..."
        # Restoring to "now" pulls the latest automated backup plus WAL
        # replay, matching what an actual disaster-recovery restore would
        # use -- not a specific named snapshot, which could be stale.
        aws rds restore-db-instance-to-point-in-time \
            --source-db-instance-identifier "$SOURCE_DB_IDENTIFIER" \
            --target-db-instance-identifier "$DRILL_ID" \
            --use-latest-restorable-time \
            --no-multi-az \
            --no-publicly-accessible >/dev/null

        log "Waiting for ${DRILL_ID} to become available..."
        aws rds wait db-instance-available --db-instance-identifier "$DRILL_ID"

        RESTORED_HOST=$(aws rds describe-db-instances \
            --db-instance-identifier "$DRILL_ID" \
            --query "DBInstances[0].Endpoint.Address" --output text)
        ;;
    azure)
        SOURCE_SERVER_NAME="${AZURE_SOURCE_SERVER_NAME:?set AZURE_SOURCE_SERVER_NAME}"
        AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:?set AZURE_RESOURCE_GROUP}"
        log "Restoring ${SOURCE_SERVER_NAME} to a new server ${DRILL_ID} " \
            "from the latest automated backup..."
        az postgres flexible-server restore \
            --resource-group "$AZURE_RESOURCE_GROUP" \
            --name "$DRILL_ID" \
            --source-server "$SOURCE_SERVER_NAME" \
            --output none

        RESTORED_HOST=$(az postgres flexible-server show \
            --resource-group "$AZURE_RESOURCE_GROUP" \
            --name "$DRILL_ID" \
            --query "fullyQualifiedDomainName" --output tsv)
        ;;
    *)
        echo "CLOUD must be 'aws' or 'azure', got '$CLOUD'" >&2
        exit 1
        ;;
esac

log "Restored instance reachable at ${RESTORED_HOST}. Verifying contents..."

# shellcheck disable=SC2059
RESTORED_DATABASE_URL=$(printf "$DATABASE_URL_TEMPLATE" "$RESTORED_HOST")

# Step 3 of docs/RUNBOOK.md's procedure: alembic_version matches what's
# expected, row counts are sane, RLS policies survived the restore.
ALEMBIC_HEAD=$(PYTHONPATH=src alembic heads | awk '{print $1}')
ALEMBIC_CURRENT=$(psql "$RESTORED_DATABASE_URL" -Atqc \
    "select version_num from alembic_version")
if [[ "$ALEMBIC_CURRENT" != "$ALEMBIC_HEAD" ]]; then
    echo "FAIL: restored alembic_version ($ALEMBIC_CURRENT) does not match" \
        "the head revision ($ALEMBIC_HEAD)" >&2
    exit 1
fi
log "alembic_version OK: ${ALEMBIC_CURRENT}"

for table in claims findings recovery_packets; do
    row_count=$(psql "$RESTORED_DATABASE_URL" -Atqc "select count(*) from ${table}")
    log "${table}: ${row_count} rows"
done

RLS_GAP=$(psql "$RESTORED_DATABASE_URL" -Atqc "
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'
      and c.relname in ('claims', 'findings', 'recovery_packets')
      and not c.relrowsecurity
")
if [[ -n "$RLS_GAP" ]]; then
    echo "FAIL: restored copy is missing row-level security on: ${RLS_GAP}" >&2
    exit 1
fi
log "RLS enabled on claims/findings/recovery_packets in the restored copy: OK"

VERIFIED_ELAPSED=$(elapsed_seconds)
log "RESULT: restore + verification took ${VERIFIED_ELAPSED}s" \
    "(start-to-usable-and-verified, per docs/RUNBOOK.md step 4)."
log "Record this number in docs/RUNBOOK.md and docs/audit/REGISTER.md's F-21 row."
