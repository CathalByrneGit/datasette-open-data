# scripts/dev.py

from pathlib import Path
import sqlite3

db = Path("data.db")

if not db.exists():
    sqlite3.connect(db).close()
    print("Created data.db")

print("Run:")
print("  uv run datasette data.db -m examples/metadata.yml")