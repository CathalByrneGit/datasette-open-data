# Central Bank test case notes

The Central Bank of Ireland website says the portal makes Central Bank statistical data easier
to access, reuse and redistribute using international open data standards, and that the data can
be accessed via search/filter or directly through an API.

Dateno's catalog registry identifies the portal as CKAN and lists these endpoints:

- https://opendata.centralbank.ie/api/3
- https://opendata.centralbank.ie/api/3/action/package_search
- https://opendata.centralbank.ie/api/3/action/package_list
- https://opendata.centralbank.ie/catalog.jsonld

The portal's CKAN Data API snippet shows DataStore endpoints under:

- https://opendata.centralbank.ie/en_GB/api/3/action/datastore_search
- https://opendata.centralbank.ie/en_GB/api/3/action/datastore_search_sql

That is why provider config separates `api_base_url` and `datastore_api_base_url`.
