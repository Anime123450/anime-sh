"""AniList's own explanation reaches the user instead of a URL and a status.

When AniList switched its API off on 07/09/2026, every request got a 403 whose
body said exactly why, and users saw ``POST https://graphql.anilist.co -> 403``.
"""

from __future__ import annotations

import pytest

from anime_sh.domain.errors import MetadataError
from anime_sh.infra.http.client import HttpClient, HttpError
from anime_sh.infra.metadata.anilist import AniListMetadata, graphql_error_message

# Verbatim shape of what AniList returned during the outage.
DISABLED = (
    '{"errors": [{"message": "The AniList API has been temporarily disabled due '
    'to severe stability issues.", "status": 403, '
    '"locations": [{"line": 1, "column": 1}]}], "data": null}'
)


def test_reads_the_first_graphql_error_message():
    assert graphql_error_message(DISABLED) == (
        "The AniList API has been temporarily disabled due to severe stability issues."
    )


@pytest.mark.parametrize(
    "body",
    [None, "", "<html>502 Bad Gateway</html>", "[]", '{"errors": []}',
     '{"errors": [{}]}', '{"errors": [{"message": "   "}]}', '{"errors": "nope"}'],
)
def test_anything_else_means_no_better_wording(body):
    """This only ever improves a message already being raised, so it must not
    raise itself on a body it does not recognise."""
    assert graphql_error_message(body) is None


async def test_the_client_keeps_the_status_and_body_on_a_4xx(monkeypatch):
    async def forbidden(method, url, params, json, headers):
        return 403, DISABLED, None

    client = HttpClient(retries=0)
    monkeypatch.setattr(client, "_send", forbidden)
    with pytest.raises(HttpError) as ei:
        await client.post_json("https://graphql.anilist.co", json={})
    assert ei.value.status == 403
    assert ei.value.body == DISABLED


async def test_anilist_surfaces_its_reason_not_the_url(monkeypatch):
    async def forbidden(method, url, params, json, headers):
        return 403, DISABLED, None

    http = HttpClient(retries=0)
    monkeypatch.setattr(http, "_send", forbidden)
    md = AniListMetadata(http=http)
    with pytest.raises(MetadataError) as ei:
        await md.trending(limit=5)
    message = str(ei.value)
    assert "temporarily disabled" in message
    assert "graphql.anilist.co" not in message


async def test_anilist_keeps_the_old_wording_when_the_body_says_nothing(monkeypatch):
    async def bad_gateway_page(method, url, params, json, headers):
        return 404, "<html>not found</html>", None

    http = HttpClient(retries=0)
    monkeypatch.setattr(http, "_send", bad_gateway_page)
    md = AniListMetadata(http=http)
    with pytest.raises(MetadataError, match="request failed"):
        await md.trending(limit=5)
