# September 2026 ledger forensics

This document records read-only evidence collection.  It is not an authorization
to change ledger rows, place orders, or apply a database migration.

## Fill

Command: `python scripts/forensics/ledger_forensics.py fill --order-id 1588108403 --symbol LTCUSDT`

Output: `Operator evidence pending; run the command against the read-only exchange adapter.`

Status: INCONCLUSIVE

## Write stop

Hypotheses retained for operator verification: direct-database removal; gateway
authentication configuration; exchange-side closes bypassing persistence;
updates to rows that were never created; and in-memory daily P&L reset/overwrite.

Command: `python scripts/forensics/ledger_forensics.py persist-trace --since 2026-09-26 --events-file <redacted-export>`

Output: `Operator export not present in the repository; no live endpoint was called.`

Status: INCONCLUSIVE

## MIN_NOTIONAL

Command: `python scripts/forensics/ledger_forensics.py rejections --log-file <redacted-pod-log>`

Output: `Operator pod log not present in the repository; no live endpoint was called.`

Status: INCONCLUSIVE

## Commission gap

Command: `python scripts/forensics/ledger_forensics.py commission --from 2026-08-26 --to 2026-09-30`

Output: `Operator exchange export not present in the repository; no live endpoint was called.`

Status: INCONCLUSIVE

## Operator runbook

Run `reconcile_positions_journal.py --dry-run` after PetroSa2/petrosa-data-manager#464
and #467 are deployed. Review the before-images and the exchange `positionRisk`
snapshot. Export the affected rows, record its SHA-256 as `evidence_ref`, and
obtain independent `approved_by` and `applied_by` values before applying.

The dry-run classes must sum to the examined rows: matching exchange
`(symbol, side)` rows remain open and are linked; rows without an exchange
position are superseded with `pnl_unknown=true`; undecidable rows are reported
for the operator. Apply requires `--confirm-count` equal to the supersede class,
uses the atomic data-manager supersede route by primary-key `id`, and skips rows
whose before-image drifted. It never writes `pnl=0` or invents P&L.

After apply, verify open rows per `(symbol, side)` equal exchange positions and
record the rollback procedure from the audit before-images. The agent does not
run this section.
