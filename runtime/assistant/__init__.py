"""Assistant-oriented utilities for interactive CLI behavior."""

from .personal_assistant import (
    OllamaResponseEngine,
    StartupBriefingService,
    compose_reply,
    normalize_language,
)

__all__ = [
    "normalize_language",
    "compose_reply",
    "StartupBriefingService",
    "OllamaResponseEngine",
]
