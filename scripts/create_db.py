from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("data.db")


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _datasette_open_data (
            id INTEGER PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()
    conn.close()

    print(f"Created {DB_PATH.resolve()}")


if __name__ == "__main__":
    main()