# Phases 3-5 implementation

This document describes the implemented evidence, opportunity, and cluster
boundaries. Harness inventory and later phases are outside this implementation.

## CX Platform boundary

`CXPlatformEvidencePort` follows the current CX Platform read contract:

- `GET /events` with an event cursor;
- `GET /tickets` with a ticket cursor;
- `GET /tickets/{ticket_id}` for ticket detail;
- `GET /conversations/{conversation_id}` for conversation detail;
- `GET /outcomes` with an outcome cursor;
- `GET /executions/{execution_id}` for an execution reference.

`CXPlatformHTTPSource` is the HTTP adapter. The source models do not carry a
tenant identity. `CXPlatformEvidenceAdapter` therefore requires `tenant_id`
when it is created. The adapter never derives tenant identity from a source
ID.

The adapter stores one `OperationalSignal` for each source fact. It stores
stable references such as `cx-platform:events:{event_id}` and keeps only a
small allowlist of bounded normalized attributes. It does not copy message
content, outcome payloads, business truth, or Harness traces.

The adapter correlates records with the source-supported IDs:

- `conversation_id` becomes the interaction identity;
- `ticket_id` becomes the journey identity;
- `customer_id` and `execution_id` are retained when present;
- an execution `trace_reference` becomes `trace_id` for related signals.

This does not treat `interaction_id` as the only correlation key. A missing
related read is reported as an unavailable source reference. A relationship
conflict is marked `CONFLICTING`. Explicit stale and partial states are kept
separate from confidence.

Repeated source reads are idempotent by the existing source identity store.
The adapter also rejects a changed normalized record for the same source
identity.

## Opportunity discovery

`OpportunityDiscoverer` uses fixed seven-day windows by default. The window is
anchored at the UTC epoch and is half-open: `[window_start, window_end)`.
`OpportunityDetectionConfig.minimum_repetitions` defaults to two.

The initial detectors are deterministic and inspectable:

- repeated operation sequence;
- repeated escalation cause;
- repeat contact after an unresolved path;
- repeated lookup operation;
- repeated approval wait;
- repeated policy denial;
- repeated human workaround;
- repeated operator correction.

Each `Opportunity` records its detector, pattern key, fixed evidence window,
source signal IDs, evidence references, occurrence keys, supported estimates,
and risk factors. Unsupported impact, operational effort, predictability, and
risk estimates are `null`. A detector type never supplies a numeric prior.
The discovery boundary can use an explicitly supplied normalized evidence score
when a source provides one. It recognizes `impact_score`,
`customer_impact_score`, `operational_effort_score`, `effort_score`,
`predictability_score`, `risk_score`, `safety_risk_score`, and
`external_dependency_risk_score` in normalized signal attributes. These values
must be source-backed values in the range `[0, 1]`; invalid or missing values
remain unknown. Qualitative `risk_factors` describe observed boundaries and do
not create a numeric risk value. Detector input excludes `CONFLICTING` and
`UNAVAILABLE` signals. Duplicate signals are removed by source identity before
detection. Opportunity IDs are deterministic hashes of tenant, detector,
pattern, window, and source identities.

## Clustering and prioritization

`OpportunityClusterer` groups by tenant, pattern type, pattern key, and the
configured half-open window. Cluster membership retains all contributing
opportunity IDs and the union of their evidence references and occurrence
keys. Frequency is calculated from unique occurrence keys, so repeated source
evidence does not inflate it.

`OpportunityPriorityFactors` stores normalized factors separately from the
final score and rank. A factor is `null` when the evidence does not support it.
The record stores `available_factors`, `unavailable_factors`, and the effective
weights used by the rank calculation.

```text
frequency       0.25
impact          0.25
confidence      0.20
operational effort 0.15
predictability  0.15
risk penalty    0.20
```

The configured weights for available positive factors are normalized before
scoring. For example, if only frequency and confidence are available, their
effective weights are `0.25 / 0.45` and `0.20 / 0.45`. An unavailable factor is
not treated as zero. The risk penalty is applied only when observed risk is
available. The score is clamped to `[0, 1]`. Ranks sort by score, then
frequency, then known impact, then deterministic cluster identity. SQLite
persists the factor record, effective weights, and final score/rank in the
existing immutable cluster repository.
