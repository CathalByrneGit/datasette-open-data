import pytest
from datasette_open_data.providers.ckan import CKANProvider


def test_urls():
    p = CKANProvider(
        name="centralbank",
        title="Central Bank",
        base_url="https://opendata.centralbank.ie/",
        datastore_api_base_url="https://opendata.centralbank.ie/en_GB/api/3",
    )
    assert p.api_base_url == "https://opendata.centralbank.ie/api/3"
    assert p.datastore_api_base_url == "https://opendata.centralbank.ie/en_GB/api/3"
