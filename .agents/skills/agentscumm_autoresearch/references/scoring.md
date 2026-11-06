# Evidence and scoring

`round.json` pins the clean AgentSCUMM commit, origin, Queen provider SHA-256,
initial mode, laundry predicate, deadline, common context, and up to three strategy
snapshots. Trials are siblings under `.internal/flight-of-the-amazon-queen/`,
matching Play Game storage. Each has a fresh profile. The helper starts no games.

The primary score is **submitted primitive steps in unique accepted requests**,
from server `action-start.inputs` arrays. Count waits, screenshots, and trailing
steps even when success interrupts a batch. Cached replay creates no new accepted
request and is not charged again. Rejected calls and retries remain diagnostic
client calls. A logical click/drag step counts once under the existing schema;
the metric is not raw key/button events.

Use per-session server JSONL, not just the client response log: shutdown can
interrupt the last reply. Check receipt session against the launch event. Reject
multiple launches/restarts, ambiguous logs, duplicate accepted request IDs,
changed strategy/common context, and mismatched/dirty startup revisions.
Interrupted/uncertain steps are attempted diagnostics, not proven delivery.

Independently inspect each claimed success's audit screenshot. Only then write
the trial's `review.json`, hashing the exact receipt and its referenced image:

```json
{
  "endpoint_verified": true,
  "receipt_sha256": "FULL_SHA256_OF_RESULT_JSON",
  "screenshot_sha256": "FULL_SHA256_OF_RECEIPT_SCREENSHOT",
  "note": "The audit image visibly shows Joe in the hotel laundry room."
}
```

Missing/false reviews never win. Success also requires the exact room-71/ready
predicate and variables, client exit 0 and confirmed client/server exit, matching
session/revision/call count, and no action starts after success. A final action
can be `completed` when background monitoring detects arrival after it returned;
`stopped` is expected only when a batch was still active.
Fresh-opening telemetry must show ready room 70 before the target.

The helper refetches provider identity at scoring. A changed/unavailable runtime
invalidates comparability. A Queen binary hash alone does not pin every game-data
and configuration byte: use an immutable origin/build, retain source/runtime
manifests, and freeze model/effort, assistance and common context. Dirty source
provenance remains dirty. Report remaining environment-identity gaps honestly.

Each dispatch has its own clock/deadline in `dispatch.json`. Late success is not
eligible even if a worker missed its timeout. Every trial needs a terminal
`worker-result.json` before scoring. Missing files
or unexited processes mean pending. On a confirmed timeout/crash the coordinator
records facts without inventing an exit code. Infrastructure failures are separate
from strategy failures. No verified success means no winner. Equal primary scores
are ties; timing can identify the fastest tied trial without breaking the action tie.

The generated report is a factual skeleton. Add evidence-linked analysis of
mis-targeted clicks, ignored inputs during animations, redundant inventory changes,
dialogue bursts, extra waits or travel. Counts do not prove these causes. Do not
invent missing tokens/prices or treat one concurrent run per idea as repeatability
or speed evidence.
