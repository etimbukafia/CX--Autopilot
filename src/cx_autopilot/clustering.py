"""Deterministic opportunity clustering and inspectable prioritization."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from pydantic import Field, model_validator

from .contracts import (
    Opportunity,
    OpportunityCluster,
    OpportunityPattern,
    OpportunityPriorityFactors,
)
from .contracts.common import ImmutableModel

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class OpportunityClusteringConfig(ImmutableModel):
    """Typed window and prioritization parameters."""

    window_size: timedelta = timedelta(days=7)
    frequency_cap: float = Field(default=10.0, gt=0.0)
    frequency_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    impact_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    confidence_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    operational_effort_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    predictability_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    risk_penalty: float = Field(default=0.20, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def parameters_are_valid(self) -> "OpportunityClusteringConfig":
        if self.window_size <= timedelta(0):
            raise ValueError("window_size must be positive")
        weight_sum = (
            self.frequency_weight
            + self.impact_weight
            + self.confidence_weight
            + self.operational_effort_weight
            + self.predictability_weight
        )
        if abs(weight_sum - 1.0) > 1e-9:
            raise ValueError("prioritization weights must sum to 1")
        return self


class OpportunityClusterer:
    """Group opportunity records by tenant, pattern, and half-open time window."""

    def __init__(self, config: OpportunityClusteringConfig | None = None) -> None:
        self.config = config or OpportunityClusteringConfig()

    def cluster(
        self,
        opportunities: Iterable[Opportunity],
        *,
        tenant_id: str | None = None,
    ) -> tuple[OpportunityCluster, ...]:
        """Return reproducible clusters and ranks for one evidence set."""

        unique_opportunities = _deduplicate_opportunities(opportunities)
        if tenant_id is not None and any(
            opportunity.tenant_id != tenant_id for opportunity in unique_opportunities
        ):
            raise ValueError("all opportunities must belong to the requested tenant")
        grouped: dict[
            tuple[str, OpportunityPattern, str, datetime, datetime], list[Opportunity]
        ] = defaultdict(list)
        for opportunity in unique_opportunities:
            timestamp = opportunity.window_start or opportunity.created_at
            window_start, window_end = _window(timestamp, self.config.window_size)
            grouped[
                (
                    opportunity.tenant_id,
                    opportunity.pattern_type,
                    opportunity.pattern_key,
                    window_start,
                    window_end,
                )
            ].append(opportunity)

        clusters = [
            self._build_cluster(
                key,
                tuple(sorted(items, key=lambda item: item.opportunity_id)),
            )
            for key, items in sorted(grouped.items(), key=lambda item: item[0])
        ]
        ranked = sorted(
            clusters,
            key=lambda cluster: (
                -cluster.priority_score,
                -cluster.frequency,
                -cluster.impact,
                cluster.cluster_id,
            ),
        )
        return tuple(
            cluster.model_copy(update={"priority_rank": rank})
            for rank, cluster in enumerate(ranked, start=1)
        )

    def _build_cluster(
        self,
        key: tuple[str, OpportunityPattern, str, datetime, datetime],
        opportunities: tuple[Opportunity, ...],
    ) -> OpportunityCluster:
        tenant_id, pattern_type, pattern_key, window_start, window_end = key
        opportunity_ids = tuple(sorted(opportunity.opportunity_id for opportunity in opportunities))
        evidence_refs = tuple(
            sorted(
                {
                    evidence_ref
                    for opportunity in opportunities
                    for evidence_ref in opportunity.evidence_refs
                }
            )
        )
        source_signal_ids = {
            signal_id
            for opportunity in opportunities
            for signal_id in opportunity.source_signal_ids
        }
        occurrence_keys = {
            occurrence_key
            for opportunity in opportunities
            for occurrence_key in opportunity.occurrence_keys
        }
        frequency = float(len(occurrence_keys or source_signal_ids or set(opportunity_ids)))
        impact = max(opportunity.impact_estimate for opportunity in opportunities)
        confidence = sum(opportunity.confidence for opportunity in opportunities) / len(
            opportunities
        )
        effort = sum(
            opportunity.operational_effort_estimate for opportunity in opportunities
        ) / len(opportunities)
        predictability = sum(
            opportunity.predictability_estimate for opportunity in opportunities
        ) / len(opportunities)
        risk = max(opportunity.risk_estimate for opportunity in opportunities)
        risk_factors = tuple(
            sorted({factor for opportunity in opportunities for factor in opportunity.risk_factors})
        )
        factors = OpportunityPriorityFactors(
            frequency=min(1.0, frequency / self.config.frequency_cap),
            impact=min(1.0, impact),
            confidence=min(1.0, confidence),
            operational_effort=min(1.0, effort),
            predictability=min(1.0, predictability),
            risk=min(1.0, risk),
        )
        score = (
            self.config.frequency_weight * factors.frequency
            + self.config.impact_weight * factors.impact
            + self.config.confidence_weight * factors.confidence
            + self.config.operational_effort_weight * factors.operational_effort
            + self.config.predictability_weight * factors.predictability
            - self.config.risk_penalty * factors.risk
        )
        priority_score = round(max(0.0, min(1.0, score)), 6)
        identity_payload = {
            "tenant_id": tenant_id,
            "pattern_type": pattern_type.value,
            "pattern_key": pattern_key,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "opportunity_ids": opportunity_ids,
        }
        cluster_id = (
            "cluster_"
            + hashlib.sha256(
                json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:32]
        )
        return OpportunityCluster(
            cluster_id=cluster_id,
            tenant_id=tenant_id,
            window_start=window_start,
            window_end=window_end,
            opportunity_ids=opportunity_ids,
            pattern_summary=(
                f"{pattern_type.value} for {pattern_key} across "
                f"{len(opportunity_ids)} opportunity record(s)."
            ),
            evidence_refs=evidence_refs,
            frequency=frequency,
            impact=impact,
            confidence=confidence,
            risk_factors=risk_factors,
            pattern_type=pattern_type,
            pattern_key=pattern_key,
            prioritization_factors=factors,
            priority_score=priority_score,
        )


def cluster_opportunities(
    opportunities: Iterable[Opportunity],
    *,
    config: OpportunityClusteringConfig | None = None,
    tenant_id: str | None = None,
) -> tuple[OpportunityCluster, ...]:
    """Convenience boundary for deterministic clustering and ranking."""

    return OpportunityClusterer(config).cluster(opportunities, tenant_id=tenant_id)


def _deduplicate_opportunities(
    opportunities: Iterable[Opportunity],
) -> tuple[Opportunity, ...]:
    by_id: dict[str, Opportunity] = {}
    for opportunity in opportunities:
        previous = by_id.get(opportunity.opportunity_id)
        if previous is not None and previous != opportunity:
            raise ValueError(
                f"opportunity ID has conflicting content: {opportunity.opportunity_id!r}"
            )
        by_id[opportunity.opportunity_id] = opportunity
    return tuple(sorted(by_id.values(), key=lambda item: item.opportunity_id))


def _window(timestamp: datetime, size: timedelta) -> tuple[datetime, datetime]:
    elapsed = timestamp - _EPOCH
    slot = elapsed // size
    start = _EPOCH + slot * size
    return start, start + size


__all__ = [
    "OpportunityClusterer",
    "OpportunityClusteringConfig",
    "cluster_opportunities",
]
