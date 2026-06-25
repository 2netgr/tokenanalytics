# ADR-0004: Multi-device sync over the user's own trust boundary

- **Status:** Accepted
- **Date:** 2026-06-24
- **Deciders:** VasiHemanth, nmavra
- **Related:** [ADR-0002](0002-durable-history-rollup.md) (durable history rollup), the
  `[[local-first-no-user-network]]` principle, `CLAUDE_IMPLEMENTATION_PROMPT.md` handoff.

## Context

TokenTelemetry was strictly on-device: every analytic was computed from logs on the
local machine, and the only outbound call was the update check. The `local-first /
no-user-network` principle (see [ADR-0002](0002-durable-history-rollup.md) and
`docs/design/product-telemetry.md`) made "no data leaves the machine" a load-bearing
promise of the product.

Multi-device analytics asks for the opposite at first glance: a user with two or three
Macs wants one dashboard that sums all of them. That is the **first** feature that moves
session data off the device that produced it. Done naively it would either (a) break the
local-first promise, or (b) silently overwrite one Mac's data with another's, because the
durable-history `sessions` table is keyed by `(agent, id)` and agent session ids are only
unique *per machine* — two Macs can collide.

Two facts narrowed the options:

- The existing `RemoteAuthMiddleware` already gates **every** route with a bearer token
  (`TT_AUTH_TOKEN`) for non-loopback callers, with loopback exempt. We do not need — and
  should not invent — a second auth scheme for sync.
- The core-rollup tier from ADR-0002 already stores exactly the raw facts a remote
  dashboard needs (tokens/cost/model/timestamps), and recomputes energy/cost insights at
  read time. Shipping that tier over the wire keeps the "store raw, derive at read"
  invariant intact across devices.

## Decision

We will add **local hub sync** and redefine the principle as **"no *third-party*
network," not "no network."** Session data may travel **between the user's own devices**,
on the user's own LAN or Tailscale, and never to any external/cloud service.

Concretely:

- Each install gets a stable `device_id` (+ `device_name`, `device_role`). Every stored
  session is stamped with the `device_id` that produced it and a `source_origin`
  (`local_scan` | `remote_sync`).
- A **collector** scans locally and POSTs **session rollups only** (the core tier — no
  transcripts, no prompt/output text) to the hub's `/sync/sessions`, authenticated by the
  existing bearer token. The hub validates the payload is rollup-shaped and rejects any
  transcript/prompt fields (defense in depth).
- The durable-history `sessions` (and `transcripts`/`summaries`) primary key widens from
  `(agent, id)` to **`(device_id, agent, id)`** so two devices can never overwrite each
  other. This is migrated in place: bump `PRAGMA user_version`, rebuild the table with the
  new key, and backfill every existing row with the local `device_id` and
  `source_origin = 'local_scan'`. No row is lost; `history.db` remains deletable to undo.
- `mark_absent` is scoped to the **local** `device_id` only — the hub must not flag a
  collector's rows absent just because it didn't scan them itself. A collector reports its
  own absences.
- `/analytics` and `/sessions` gain a `device` filter (`local` | `all` | `<device_id>`),
  defaulting to `local` so the standalone experience is byte-for-byte unchanged.

## Alternatives considered

- **Add `device_id` as a plain column + index, keep PK `(agent, id)`** — lighter
  migration, but the upsert would still collapse colliding sessions from two Macs into one
  row; correctness would depend on every query remembering to group by device. Rejected:
  the safety must live in the key, not in every call site.
- **A separate cloud relay / hosted sync** — simplest networking, but breaks the
  local-first promise outright and adds an operator, a cost, and a data-custody question.
  Rejected.
- **A second, sync-specific auth scheme** — redundant with the existing bearer gate and a
  larger attack surface. Rejected in favour of reusing `RemoteAuthMiddleware`.
- **Upload full transcripts so the hub can drill in** — heavy, and ships prompt/output
  text across the network by default. Rejected as the default; left as a possible future
  opt-in, never on by default.

## Consequences

- ✅ One dashboard can aggregate all of a user's Macs (`device=all`), filter to one
  (`device=<id>`), or stay local (`device=local`, the default).
- ✅ The local-first promise survives in its real, defensible form: data stays inside the
  user's own device set, authenticated, with no third party.
- ✅ Reusing the bearer gate means `/sync/sessions` is protected the moment the hub runs
  with `TT_AUTH_TOKEN` set; there is no new unauthenticated surface.
- ✅ Raw-fact rollups keep the "derive at read" invariant: cross-device totals recompute
  against the hub's power config.
- ⚠️ **Schema migration on real user data.** The PK change rebuilds three tables. It is
  idempotent and backfills the local device, but it is the riskiest single step — covered
  by a migration test that asserts zero row loss and correct backfill.
- ⚠️ **Remote freshness is sync-bound.** The hub can only live-scan its own disk; a
  collector's data is as fresh as its last successful POST. `/devices` exposes
  `last_seen_at` so a stale collector is visible.
- ⚠️ A user who points a collector at a hub over the open internet (rather than
  LAN/Tailscale) is outside the intended trust boundary; docs steer to Tailscale/LAN and
  the bearer token is mandatory for non-loopback.
- 🔁 To undo: revert the feature PR and delete `history.db` (self-contained under the data
  dir; no agent data is ever modified). Collectors simply stop being able to POST.
