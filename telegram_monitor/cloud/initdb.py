"""Initialize tables before starting the three cloud services."""
import os
from .db import Store
from .api import _db_url

if __name__ == "__main__":
    Store(os.environ.get("DATABASE_URL") or _db_url())
    print("Database initialized")
