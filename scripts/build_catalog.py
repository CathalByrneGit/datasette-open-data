from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datasette_open_data.registry import plugin_config, providers_from_config


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT,
            base_url TEXT,
            api_base_url TEXT,
            datastore_api_base_url TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS packages (
            provider TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT,
            title TEXT,
            notes TEXT,
            organization_id TEXT,
            organization_name TEXT,
            organization_title TEXT,
            license_title TEXT,
            url TEXT,
            metadata_created TEXT,
            metadata_modified TEXT,
            raw_json TEXT,
            PRIMARY KEY (provider, id)
        );

        CREATE TABLE IF NOT EXISTS resources (
            provider TEXT NOT NULL,
            id TEXT NOT NULL,
            package_id TEXT NOT NULL,
            name TEXT,
            description TEXT,
            format TEXT,
            url TEXT,
            datastore_active INTEGER,
            created TEXT,
            last_modified TEXT,
            raw_json TEXT,
            PRIMARY KEY (provider, id)
        );

        CREATE TABLE IF NOT EXISTS organizations (
            provider TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT,
            title TEXT,
            description TEXT,
            raw_json TEXT,
            PRIMARY KEY (provider, id)
        );

        CREATE TABLE IF NOT EXISTS groups (
            provider TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT,
            title TEXT,
            description TEXT,
            raw_json TEXT,
            PRIMARY KEY (provider, id)
        );

        CREATE TABLE IF NOT EXISTS tags (
            provider TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (provider, name)
        );

        CREATE TABLE IF NOT EXISTS package_tags (
            provider TEXT NOT NULL,
            package_id TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            PRIMARY KEY (provider, package_id, tag_name)
        );

        CREATE TABLE IF NOT EXISTS package_groups (
            provider TEXT NOT NULL,
            package_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            PRIMARY KEY (provider, package_id, group_id)
        );

        CREATE TABLE IF NOT EXISTS catalog_runs (
            id INTEGER PRIMARY KEY,
            provider TEXT,
            started_at TEXT,
            finished_at TEXT,
            package_count INTEGER,
            resource_count INTEGER
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS packages_fts USING fts5(
            provider,
            title,
            notes,
            organization_title,
            tags
        );

        CREATE TABLE IF NOT EXISTS packages_fts_map (
            fts_rowid INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            package_id TEXT NOT NULL,
            UNIQUE (provider, package_id)
        );

        CREATE INDEX IF NOT EXISTS idx_packages_provider
            ON packages(provider);

        CREATE INDEX IF NOT EXISTS idx_packages_org
            ON packages(provider, organization_id);

        CREATE INDEX IF NOT EXISTS idx_resources_package
            ON resources(provider, package_id);

        CREATE INDEX IF NOT EXISTS idx_package_tags_tag
            ON package_tags(provider, tag_name);

        CREATE INDEX IF NOT EXISTS idx_package_tags_package
            ON package_tags(provider, package_id);

        CREATE INDEX IF NOT EXISTS idx_package_groups_group
            ON package_groups(provider, group_id);

        CREATE INDEX IF NOT EXISTS idx_package_groups_package
            ON package_groups(provider, package_id);
        """
    )


def reset_provider(conn: sqlite3.Connection, provider_name: str) -> None:
    rows = conn.execute(
        "SELECT fts_rowid FROM packages_fts_map WHERE provider = ?",
        [provider_name],
    ).fetchall()

    for row in rows:
        fts_rowid = row["fts_rowid"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute("DELETE FROM packages_fts WHERE rowid = ?", [fts_rowid])

    conn.execute("DELETE FROM packages_fts_map WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM package_tags WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM package_groups WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM tags WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM resources WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM packages WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM organizations WHERE provider = ?", [provider_name])
    conn.execute("DELETE FROM groups WHERE provider = ?", [provider_name])


def upsert_provider(
    conn: sqlite3.Connection,
    provider_name: str,
    provider_config: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO providers (
            id, type, title, base_url, api_base_url, datastore_api_base_url, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            provider_name,
            provider_config.get("type", "ckan"),
            provider_config.get("title") or provider_name,
            provider_config.get("base_url"),
            provider_config.get("api_base_url"),
            provider_config.get("datastore_api_base_url"),
            json.dumps(provider_config),
        ],
    )


def upsert_package(
    conn: sqlite3.Connection,
    provider_name: str,
    package: dict[str, Any],
) -> int:
    organization = package.get("organization") or {}

    conn.execute(
        """
        INSERT OR REPLACE INTO packages (
            provider, id, name, title, notes,
            organization_id, organization_name, organization_title,
            license_title, url,
            metadata_created, metadata_modified,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            provider_name,
            package.get("id"),
            package.get("name"),
            package.get("title"),
            package.get("notes"),
            organization.get("id"),
            organization.get("name"),
            organization.get("title"),
            package.get("license_title"),
            package.get("url"),
            package.get("metadata_created"),
            package.get("metadata_modified"),
            json.dumps(package),
        ],
    )

    if organization.get("id"):
        conn.execute(
            """
            INSERT OR REPLACE INTO organizations (
                provider, id, name, title, description, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                provider_name,
                organization.get("id"),
                organization.get("name"),
                organization.get("title"),
                organization.get("description"),
                json.dumps(organization),
            ],
        )

    resource_count = 0

    for resource in package.get("resources") or []:
        resource_id = resource.get("id")
        if not resource_id:
            continue

        conn.execute(
            """
            INSERT OR REPLACE INTO resources (
                provider, id, package_id, name, description, format, url,
                datastore_active, created, last_modified, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                provider_name,
                resource_id,
                package.get("id"),
                resource.get("name"),
                resource.get("description"),
                resource.get("format"),
                resource.get("url"),
                1 if resource.get("datastore_active") else 0,
                resource.get("created"),
                resource.get("last_modified"),
                json.dumps(resource),
            ],
        )
        resource_count += 1

    for tag in package.get("tags") or []:
        tag_name = tag.get("display_name") or tag.get("name")
        if not tag_name:
            continue

        conn.execute(
            "INSERT OR IGNORE INTO tags (provider, name) VALUES (?, ?)",
            [provider_name, tag_name],
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO package_tags (provider, package_id, tag_name)
            VALUES (?, ?, ?)
            """,
            [provider_name, package.get("id"), tag_name],
        )

    for group in package.get("groups") or []:
        group_id = group.get("id")
        if not group_id:
            continue

        conn.execute(
            """
            INSERT OR REPLACE INTO groups (
                provider, id, name, title, description, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                provider_name,
                group_id,
                group.get("name"),
                group.get("title"),
                group.get("description"),
                json.dumps(group),
            ],
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO package_groups (provider, package_id, group_id)
            VALUES (?, ?, ?)
            """,
            [provider_name, package.get("id"), group_id],
        )

    return resource_count


def rebuild_provider_fts(conn: sqlite3.Connection, provider_name: str) -> None:
    rows = conn.execute(
        "SELECT fts_rowid FROM packages_fts_map WHERE provider = ?",
        [provider_name],
    ).fetchall()

    for row in rows:
        fts_rowid = row["fts_rowid"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute("DELETE FROM packages_fts WHERE rowid = ?", [fts_rowid])

    conn.execute("DELETE FROM packages_fts_map WHERE provider = ?", [provider_name])

    packages = conn.execute(
        """
        SELECT
            p.provider,
            p.id,
            p.title,
            p.notes,
            p.organization_title,
            COALESCE(group_concat(pt.tag_name, ' '), '') AS tags
        FROM packages p
        LEFT JOIN package_tags pt
            ON pt.provider = p.provider
            AND pt.package_id = p.id
        WHERE p.provider = ?
        GROUP BY p.provider, p.id
        """,
        [provider_name],
    ).fetchall()

    for package in packages:
        cursor = conn.execute(
            """
            INSERT INTO packages_fts (
                provider, title, notes, organization_title, tags
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                package["provider"],
                package["title"],
                package["notes"],
                package["organization_title"],
                package["tags"],
            ],
        )

        conn.execute(
            """
            INSERT INTO packages_fts_map (
                fts_rowid, provider, package_id
            )
            VALUES (?, ?, ?)
            """,
            [
                cursor.lastrowid,
                package["provider"],
                package["id"],
            ],
        )


async def build_catalog(
    provider_name: str,
    database: Path,
    rows_per_page: int = 100,
    limit: int | None = None,
) -> None:
    config = plugin_config(None)

    if provider_name not in config.get("providers", {}):
        available = ", ".join(config.get("providers", {}).keys())
        raise ValueError(
            f"Unknown provider: {provider_name}. Available providers: {available}"
        )

    provider_config = config["providers"][provider_name]
    providers = providers_from_config(config)
    provider = providers[provider_name]

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row

    init_db(conn)
    reset_provider(conn, provider_name)
    upsert_provider(conn, provider_name, provider_config)

    started_at = datetime.now(timezone.utc).isoformat()
    package_count = 0
    resource_count = 0

    print(f"Building catalog for provider: {provider_name}")
    print(f"Writing to: {database}")

    start = 0

    while True:
        result = await provider._get(
            "package_search",
            {
                "q": "*:*",
                "rows": rows_per_page,
                "start": start,
            },
        )

        results = result.get("results") or []

        if not results:
            break

        for package in results:
            if limit is not None and package_count >= limit:
                break

            full_package = await provider._get(
                "package_show",
                {"id": package["id"]},
            )

            resource_count += upsert_package(conn, provider_name, full_package)
            package_count += 1

            if package_count % 25 == 0:
                conn.commit()
                print(f"Loaded {package_count} packages...")

        conn.commit()

        if limit is not None and package_count >= limit:
            break

        start += rows_per_page

        if start >= result.get("count", 0):
            break

    rebuild_provider_fts(conn, provider_name)

    finished_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO catalog_runs (
            provider, started_at, finished_at, package_count, resource_count
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            provider_name,
            started_at,
            finished_at,
            package_count,
            resource_count,
        ],
    )

    conn.commit()
    conn.close()

    print(f"Done. Packages: {package_count:,}. Resources: {resource_count:,}.")


async def build_all(
    database: Path,
    rows_per_page: int = 100,
    limit: int | None = None,
) -> None:
    config = plugin_config(None)

    for provider_name in config.get("providers", {}):
        await build_catalog(
            provider_name=provider_name,
            database=database,
            rows_per_page=rows_per_page,
            limit=limit,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="centralbank")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--database", default="catalog.db")
    parser.add_argument("--rows-per-page", type=int, default=100)
    parser.add_argument("--limit", type=int)

    args = parser.parse_args()

    if args.all:
        asyncio.run(
            build_all(
                database=Path(args.database),
                rows_per_page=args.rows_per_page,
                limit=args.limit,
            )
        )
    else:
        asyncio.run(
            build_catalog(
                provider_name=args.provider,
                database=Path(args.database),
                rows_per_page=args.rows_per_page,
                limit=args.limit,
            )
        )


if __name__ == "__main__":
    main()