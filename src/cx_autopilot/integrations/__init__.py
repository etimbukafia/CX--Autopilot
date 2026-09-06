"""External source adapters kept outside Autopilot domain contracts."""

from .cx_platform import (
    CXPlatformDataError,
    CXPlatformEvidenceAdapter,
    CXPlatformEvidencePort,
    CXPlatformHTTPSource,
    CXPlatformSourceError,
    EvidenceIngestionResult,
)
from .harness import HarnessInventoryAdapter, HarnessInventoryError, HarnessInventoryPort
from .harness_candidate import (
    HarnessCandidateAdapter,
    HarnessCandidateBuild,
    HarnessCandidateError,
    HarnessFactoryPort,
)
from .improvement_lab import (
    EVALUATION_FAILED,
    EVALUATION_SUCCEEDED,
    ImprovementLabEvaluationAdapter,
    ImprovementLabEvaluationError,
    LabComparisonPort,
    LabEvaluationResult,
    LabEvaluationRunnerPort,
)

__all__ = [
    "CXPlatformDataError",
    "CXPlatformEvidenceAdapter",
    "CXPlatformEvidencePort",
    "CXPlatformHTTPSource",
    "CXPlatformSourceError",
    "EvidenceIngestionResult",
    "HarnessInventoryAdapter",
    "HarnessInventoryError",
    "HarnessInventoryPort",
    "HarnessCandidateAdapter",
    "HarnessCandidateBuild",
    "HarnessCandidateError",
    "HarnessFactoryPort",
    "EVALUATION_FAILED",
    "EVALUATION_SUCCEEDED",
    "ImprovementLabEvaluationAdapter",
    "ImprovementLabEvaluationError",
    "LabComparisonPort",
    "LabEvaluationResult",
    "LabEvaluationRunnerPort",
]
