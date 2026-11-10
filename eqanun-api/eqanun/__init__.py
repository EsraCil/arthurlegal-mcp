"""eqanun — dependency-free client for Azerbaijan's official legislation API.

See ``README.md`` for the reconstructed API surface and usage.
"""

from .client import (
    API_BASE,
    SITE_BASE,
    STATUS,
    EqanunClient,
    EqanunError,
    __version__,
)

__all__ = [
    "EqanunClient",
    "EqanunError",
    "STATUS",
    "API_BASE",
    "SITE_BASE",
    "__version__",
]
