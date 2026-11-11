"""HTTP layer: one module per resource.

``ROUTERS`` is the single registration order for the application.  Adding an
endpoint means adding a module here, not editing ``main.py``.
"""

from routers import (
    admin,
    auth,
    catalog,
    checkout,
    copilot,
    events,
    health,
    recs,
    reviews,
    search,
)

ROUTERS = (
    health.router,
    auth.router,
    admin.router,
    catalog.router,
    search.router,
    recs.router,
    reviews.router,
    events.router,
    checkout.router,
    copilot.router,
)
