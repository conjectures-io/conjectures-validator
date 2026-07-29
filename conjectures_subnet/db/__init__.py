"""Database access for the conjectures.io validator.

The schema itself is owned by the plain-SQL migrations in
``deploy/migrate/sql/``, applied by Flyway. This package is the runtime view of
that schema, not its source of truth.
"""

from .models import Base

__all__ = ["Base"]
