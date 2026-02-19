"""AEX daemon utilities — logging, config, metrics, supervisor, invariants, policy, compat.

Individual modules are imported directly by consumers, e.g.:
    from ..utils.logging_config import StructuredLogger
    from ..utils.config_loader import config_loader

NOTE: We intentionally do NOT eagerly re-export modules that import from
the db package (metrics, supervisor, compat, budget, rate_limit) to avoid circular imports.
The db package imports logging_config from here, so we can only eagerly
export modules that have zero db dependencies.
"""

# Safe to re-export eagerly — no db dependency
from .logging_config import setup_logging, StructuredLogger, JSONFormatter
from .config_loader import config_loader, ConfigLoader, AEXConfig, ModelConfig, ProviderConfig
from .invariants import run_all_checks, InvariantResult
from .policy_engine import validate_request, validate_response

__all__ = [
    "setup_logging", "StructuredLogger", "JSONFormatter",
    "config_loader", "ConfigLoader", "AEXConfig", "ModelConfig", "ProviderConfig",
    "run_all_checks", "InvariantResult",
    "validate_request", "validate_response",
]
