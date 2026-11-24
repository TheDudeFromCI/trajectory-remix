"""Graph validation entry point backed by the compiler's structural dry run."""

from .compiler_diagnostics import Diagnostic, reject_constant
from .compiler_diagnostics import dry_run as diagnose

__all__ = ["Diagnostic", "diagnose", "reject_constant"]
