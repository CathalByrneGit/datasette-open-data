from __future__ import annotations

import sqlite3

import pytest


class FakeResults:
    """Stands in for datasette's Results object."""

    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FakeDatabase:
    """Minimal async wrapper over a real sqlite3 file.

    Uses a real database rather than mocking execute() so that SQL built by
    the code under test (identifier quoting, PRAGMA, FTS) is actually parsed.
    """

    def __init__(self, path: str | None):
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    async def execute(self, sql, params=None):
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params or [])
            return FakeResults(cursor.fetchall())
        finally:
            conn.close()

    async def table_names(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            return [row["name"] for row in rows]
        finally:
            conn.close()


class FakeDatasette:
    """Stands in for the Datasette instance passed to views and agent tools."""

    def __init__(self, databases=None, plugin_config=None):
        self.databases = databases or {}
        self._plugin_config = plugin_config
        self.rendered = []

    def get_database(self, name):
        return self.databases[name]

    def plugin_config(self, name):
        return self._plugin_config

    async def render_template(self, template, context=None, request=None):
        self.rendered.append((template, context))
        return f"<html>{template}</html>"


class FakeRequest:
    def __init__(self, args=None, headers=None, url_vars=None):
        self.args = args or {}
        self.headers = headers or {}
        self.url_vars = url_vars or {}


@pytest.fixture
def data_db(tmp_path):
    """A file-backed 'data' database with two joinable tables."""
    path = str(tmp_path / "data.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE population (county TEXT, year TEXT, value INTEGER);
        INSERT INTO population VALUES
            ('Dublin', '2020', 100),
            ('Cork',   '2020', 50),
            ('Galway', '2020', 25);

        CREATE TABLE prices (county TEXT, year TEXT, price REAL);
        INSERT INTO prices VALUES
            ('Dublin', '2020', 1.5),
            ('Cork',   '2020', 1.2),
            ('Kerry',  '2020', 1.1);
        """
    )
    conn.commit()
    conn.close()
    return FakeDatabase(path)


@pytest.fixture
def datasette_with_data(data_db):
    return FakeDatasette(databases={"data": data_db})
