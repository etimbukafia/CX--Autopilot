"""External source adapters kept outside Autopilot domain contracts."""

from .cx_platform import (
    CXPlatformDataError,
    CXPlatformEvidenceAdapter,
    CXPlatformEvidencePort,
    CXPlatformHTTPSource,
    CXPlatformSourceError,
    EvidenceIngestionResult,
)

__all__ = [
    "CXPlatformDataError",
    "CXPlatformEvidenceAdapter",
    "CXPlatformEvidencePort",
    "CXPlatformHTTPSource",
    "CXPlatformSourceError",
    "EvidenceIngestionResult",
]
