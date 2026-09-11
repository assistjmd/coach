# Launch audit and implementation

Reviewed the supplied Coach Will HTML handoff and `fix/oauth-connect-flow` at
`c7af84d51ba9fedc18b251e23efd1f9e8781ff1b`. Changes are on
`audit/launch-safety-fixes`. This is a code review and regression pass, not a
production load test or certification of App Store purchase flows.

## Changes implemented

- **Purchase ownership:** new subscriptions require Apple's signed
  `appAccountToken` to match the authenticated Coach account. The iOS purchase
  supplies that token. Existing subscriptions cannot be transferred by replaying
  their receipt. Transaction-level database locks cover concurrent first claims.
- **Purchase lifecycle:** persist signed dates and transaction IDs; ignore stale
  receipts and older renewal periods. Handle refund/revoke notification enum
  values correctly. Retry unfinished StoreKit transactions after failed server
  verification, and discard responses belonging to a previous signed-in account.
- **Environment separation:** ordinary production accounts cannot gain access
  through Sandbox purchases. Dedicated testing accounts can be explicitly enabled.
- **Model spending:** quota checks serialize on the account row. Initial daily
  generation has a durable three-attempt daily ceiling and a ten-minute lease;
  failed headline attempts count against the existing three-revision ceiling.
  Model clients have a 90-second timeout and no automatic SDK retries. Expired
  accounts do not trigger scheduled briefs or ingestion-triggered model calls
  when subscription enforcement is enabled.
- **Consent:** health uploads, provider connection/sync, and coaching generation
  require the current terms/privacy acceptance and age eligibility on the server.
  Account export remains available without onboarding completion.
- **Stable plans:** late sleep updates readiness through the headline-only path.
  It preserves exercises, completed items, and logged weights.
- **Health data:** accept all five nutrition metric names sent by iOS; update
  changing daily totals instead of retaining the first steps value forever.
  Preserve contained workouts and simultaneous incompatible sports. Complete
  sleep snapshots replace partial stage totals and distinguish HealthKit writers.
- **Upload recovery:** HealthKit cursors are account-scoped and saved only after
  every upload chunk succeeds. Sync calls are serialized and old account work is
  abandoned when the session changes. Observer registration is idempotent.
- **Model output and chat:** validate model response types and finite readiness
  scores before saving. Remove raw model replies from validation logs. Versioned
  JSON SSE events preserve newlines; the old stream format remains supported.
- **Verification infrastructure:** add PostgreSQL-backed GitHub Actions tests and
  a test-database name guard before destructive schema setup.

## Verification

The starting branch produced 438 passing tests and three time-dependent overnight
test failures. Overnight fixtures now anchor the sample to the athlete's local
morning rather than subtracting hours from the wall clock. Consent-sensitive
fixtures distinguish onboarded and unconsented users.

The final backend run passed **465 tests** (two dependency/configuration
deprecation warnings); fatal-rule lint checks also passed.
The updated suite includes regression coverage for mutable totals, nutrition,
workout identity, sleep snapshots, failed-call budgets, account binding, sandbox
access, receipt ordering, stable plans, consent, SSE newlines, model response
validation, and a real two-connection PostgreSQL quota race. All external model
and Apple operations in these tests are mocked. Migrations run from scratch in
the suite against an isolated PostgreSQL 17 database.

An unsigned iOS Release device build was checked with Xcode 26.6. This verifies
compilation, not actual HealthKit background delivery or StoreKit behavior.
CI checks fatal Python lint rules; the wider inherited lint configuration still
reports existing and stylistic findings and is not represented as clean.

## Integration and deployment

1. Review against the handoff branch, not an older `main`. These changes depend
   on the OAuth-flow work already present in the handoff commit.
2. Back up the database and apply migration `a6f3912d804e` before serving this
   backend. It adds `generation_budgets`, subscription ordering fields, and sleep
   writer identity. The repository's Fly configuration **already runs**
   `alembic upgrade head` as a release command, contrary to the handoff's note.
   No production migration or deployment was performed during this audit.
3. Deploy the backend before the corresponding iOS release. Older iOS clients
   can still upload legacy sleep data and use the old SSE protocol. New purchases
   from old clients that omit an account token cannot establish a new subscription;
   coordinate the release and testing accordingly.
4. Configure `SANDBOX_SUBSCRIPTION_USER_IDS` with comma-separated Coach user UUIDs
   for dedicated TestFlight/App Review accounts if they use the production
   backend. Normal production users are deliberately excluded. Validate a fresh
   purchase, restore on the same Coach account, refund, renewal, and delayed
   webhook with Apple's real sandbox before release.
5. Legacy receipts already associated with the same Coach account can still be
   verified without an account token. Unassociated legacy purchases need a
   separately verified support/migration process; they are not automatically
   assigned to whoever submits a receipt first.
6. Test failed uploads and account switching on a physical device. The new cursor
   namespace intentionally causes a fresh backfill. Watch request volume and
   database growth when rolling this out.

## Remaining engineering priorities

- Move scheduled work into durable queued jobs before increasing web workers or
  promising capacity for 1,000 active users. The in-process scheduler still scans
  users and performs work serially. Generation leases limit duplicate initial
  briefs, but do not constitute a complete distributed scheduling system.
- Add service-wide model spend limits, bounded prompt context, and operational
  alerts. Per-user ceilings reduce repeated-call abuse; they do not bound total
  spending across many newly created accounts or prove predictable latency.
- Add physical-device automation/manual acceptance for HealthKit and StoreKit.
  HealthKit deletions are not propagated, and already-collapsed historical
  workouts cannot be reconstructed without source reimport. Sleep writer IDs
  identify an app source, not necessarily each physical device using that app.
- Add broader concurrent sync and out-of-order health-snapshot tests. Serializing
  a single phone's uploads does not serialize multiple devices or all provider
  jobs for an account.
- Introduce dependency locking and scheduled vulnerability review. Dependencies
  remain lower-bounded rather than reproducibly locked. Review token revocation
  and the existing long-lived JWT policy as a separate authentication change.
- Review session-editor save/dismiss races and add UI tests. The current pass
  protects plans from server refreshes; it does not claim full UI race coverage.

No production secrets were changed, production services deployed, or existing
branches rewritten. A limited history scan for common private-key/token patterns
found no matches; that does not prove the repository contains no sensitive data.
