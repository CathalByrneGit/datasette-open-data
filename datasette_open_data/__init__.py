from __future__ import annotations

from datasette import hookimpl

from .agent_tools import register_open_data_agent_tools
from .views import (
    dataset_view,
    groups_view,
    index_view,
    load_resource_view,
    organizations_view,
    resource_preview_view,
    search_view,
    tags_view,
)


@hookimpl
def register_routes():
    return [
        (r"^/-/open-data$", index_view),
        (r"^/-/open-data/search$", search_view),
        (r"^/-/open-data/dataset/(?P<dataset_id>[^/]+)$", dataset_view),
        (r"^/-/open-data/resource/(?P<resource_id>[^/]+)/preview$", resource_preview_view),
        (r"^/-/open-data/resource/(?P<resource_id>[^/]+)/load$", load_resource_view),
        (r"^/-/open-data/groups$", groups_view),
        (r"^/-/open-data/organizations$", organizations_view),
        (r"^/-/open-data/tags$", tags_view),
    ]


@hookimpl
def menu_links(datasette, actor):
    return [
        {
            "href": "/-/open-data",
            "label": "Open Data",
        }
    ]


@hookimpl
def extra_css_urls():
    """Apply the Open Data skin across the instance.

    custom.css restyles Datasette's own chrome (body, .hd, .ft, .metadata), so
    it is deliberately global. It is served from datasette_open_data/static/,
    which Datasette mounts automatically -- no --static flag needed.

    To limit the skin to this plugin's own pages instead, take the `template`
    argument and return the URL only when it starts with "open_data_".
    """
    return ["/-/static-plugins/datasette-open-data/custom.css"]


@hookimpl
def register_agent_tools(datasette):
    return register_open_data_agent_tools(datasette)
