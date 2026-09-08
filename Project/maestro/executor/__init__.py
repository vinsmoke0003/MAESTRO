"""L1 — executors.

Importing this package registers every verb in the closed registry AND its
executor. `maestro.registry.known_verbs()` is empty until this import happens,
which is deliberate: the registry is defined by what can actually be executed.
"""

# Importing the executor modules registers their verbs + implementations.
from maestro.executor import app as _app  # noqa: F401,E402
from maestro.executor import browser as _browser  # noqa: F401,E402
from maestro.executor import draft as _draft  # noqa: F401,E402
from maestro.executor import fs as _fs  # noqa: F401,E402
from maestro.executor import search as _search  # noqa: F401,E402
from maestro.executor import system as _system  # noqa: F401,E402
from maestro.executor.base import (
    Context,
    EffectManifest,
    Executor,
    NotAvailable,
    Result,
    UnboundVariable,
    get_executor,
    has_executor,
    register_executor,
    registered_verbs,
    resolve,
)

__all__ = [
    "Context",
    "EffectManifest",
    "Executor",
    "NotAvailable",
    "Result",
    "UnboundVariable",
    "get_executor",
    "has_executor",
    "register_executor",
    "registered_verbs",
    "resolve",
]
