"""Custom exceptions for schema validation middleware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ValidationFailure:
    """Represents a single schema validation failure entry."""

    path: str
    message: str


class SchemaValidationMiddlewareError(Exception):
    """Raised when payload validation fails for a contract type."""

    def __init__(self, contract_type: str, failures: Iterable[ValidationFailure]) -> None:
        self.contract_type = contract_type
        self.failures = list(failures)
        details = "; ".join(f"{failure.path}: {failure.message}" for failure in self.failures)
        super().__init__(f"Schema validation failed for {contract_type}. {details}")
