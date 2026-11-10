"""lexscholar — federated, dependency-free client for open-access legal scholarship.

One search surface over ten no-auth sources (DOAJ, DergiPark, Law Review Commons, SciELO,
HAL, Dialnet, OpenAIRE, Crossref, Unpaywall, OpenAlex), with a deterministic
router that picks the 2-3 that fit each question.

See ``API.md`` for the verified upstream reference and ``README.md`` for usage.
"""

from ._http import LexScholarError, RateLimited
from .client import LexScholarClient
from .record import FIELDS, make_record
from .router import route
from .sources import DISCOVERY, REGISTRY, RESOLVERS, capabilities

__version__ = "0.1.0"
__all__ = [
    "LexScholarClient",
    "LexScholarError",
    "RateLimited",
    "make_record",
    "FIELDS",
    "route",
    "capabilities",
    "REGISTRY",
    "DISCOVERY",
    "RESOLVERS",
    "__version__",
]
