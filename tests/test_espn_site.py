"""Tests for core/utils/espn_site.py."""

import asyncio

import pytest

from core.errors import APIFetchError
from core.utils.espn_site import ESPN_RETRY_DELAY_SECONDS, fetch_json

URL = "https://site.api.espn.com/test"


def fake_espn(monkeypatch, answers):
    """Replaces aiohttp with a fake ESPN that gives `answers` in order: a dict
    is returned as the JSON, an exception is raised instead. Returns the list
    of URLs asked for and the list of sleeps, for the test to check."""
    urls: list[str] = []
    sleeps: list[float] = []

    class FakeResponse:
        def __init__(self, answer):
            self.answer = answer

        async def __aenter__(self):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def json(self):
            return self.answer

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        def get(self, url, *args, **kwargs):
            urls.append(url)
            return FakeResponse(answers.pop(0))

    async def fake_sleep(seconds):
        sleeps.append(seconds)  # don't actually wait

    monkeypatch.setattr(
        "core.utils.espn_site.aiohttp.ClientSession", lambda *a, **kw: FakeSession()
    )
    monkeypatch.setattr("core.utils.espn_site.asyncio.sleep", fake_sleep)
    return urls, sleeps


@pytest.mark.asyncio
async def test_returns_the_json(monkeypatch):
    urls, sleeps = fake_espn(monkeypatch, [{"injuries": []}])

    assert await fetch_json(URL) == {"injuries": []}
    assert urls == [URL]
    assert sleeps == []


@pytest.mark.asyncio
async def test_a_timeout_is_retried_once(monkeypatch):
    """ESPN is slow now and then: one timeout shouldn't fail the fetch."""
    urls, sleeps = fake_espn(monkeypatch, [asyncio.TimeoutError(), {"ok": True}])

    assert await fetch_json(URL) == {"ok": True}
    assert urls == [URL, URL]
    assert sleeps == [ESPN_RETRY_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_two_timeouts_raise_api_fetch_error(monkeypatch):
    """The caller must hear about it, not get nothing back."""
    urls, _ = fake_espn(monkeypatch, [asyncio.TimeoutError(), asyncio.TimeoutError()])

    with pytest.raises(APIFetchError):
        await fetch_json(URL)
    assert len(urls) == 2
