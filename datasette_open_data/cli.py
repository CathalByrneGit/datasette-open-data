from __future__ import annotations

import argparse
import asyncio

from .loader import load_csv_url, load_datastore_resource
from .registry import DEFAULT_CONFIG, providers_from_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Load open data resources into SQLite")
    parser.add_argument("--provider", default="centralbank")
    parser.add_argument("--resource-id")
    parser.add_argument("--csv-url")
    parser.add_argument("--database", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--limit", type=int, default=50000)
    args = parser.parse_args()

    async def run():
        if args.csv_url:
            count = await load_csv_url(args.csv_url, args.database, args.table)
        else:
            if not args.resource_id:
                parser.error("Provide --resource-id or --csv-url")
            providers = providers_from_config(DEFAULT_CONFIG)
            provider = providers[args.provider]
            count = await load_datastore_resource(provider, args.resource_id, args.database, args.table, args.limit)
        print(f"Loaded {count} rows into {args.database}:{args.table}")

    asyncio.run(run())
