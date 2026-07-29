# scripts/dev.py

import sqlite3
from pathlib import Path

db = Path("data.db")

if not db.exists():
    sqlite3.connect(db).close()
    print("Created data.db")

print("Run:")
print("  uv run datasette data.db -m examples/metadata.yml")
