"""Reading a bounded HTTP body must actually read all of it.

THE bug behind a long chain of live reports: ``response.content.read(n)``
reads *at most* n bytes - it returns whatever is currently buffered and
does not wait for the rest. Every download in Roadplanner used that call,
so bodies arriving in more than one chunk were silently truncated:

- photos with an intact JPEG header that no renderer could open
  ("8 Fotos ... keines davon ließ sich als Bild öffnen"),
- "0 Kartenbilder", because a truncated PNG tile fails to decode,
- a shared place page that "contained no GPS" because the coordinates sat
  past the first chunk.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

MODULE_PATH = Path("custom_components/roadplanner_mcp/http_read.py")
spec = spec_from_file_location("roadplanner_http_read_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

BODY = bytes(range(256)) * 400  # 102_400 bytes, many chunks


class FakeContent:
    """aiohttp semantics: read() gives one buffered chunk, not the body."""

    def __init__(self, body: bytes, chunk_size: int = 1024) -> None:
        self._body = body
        self._chunk_size = chunk_size
        self.chunks_served = 0

    async def iter_chunked(self, _size: int):
        for start in range(0, len(self._body), self._chunk_size):
            self.chunks_served += 1
            yield self._body[start:start + self._chunk_size]

    async def read(self, max_bytes: int = -1) -> bytes:
        return self._body[: self._chunk_size]


class FakeResponse:
    def __init__(self, body: bytes, chunk_size: int = 1024) -> None:
        self.content = FakeContent(body, chunk_size)


def verify_the_whole_body_arrives_not_just_the_first_chunk() -> None:
    response = FakeResponse(BODY)
    body = asyncio.run(module.async_read_bounded(response, len(BODY)))
    assert body == BODY, f"got {len(body or b'')} of {len(BODY)} bytes"
    assert response.content.chunks_served > 1, "the fixture must span chunks"
    # The trap this replaces, demonstrated on the same fixture.
    assert len(asyncio.run(response.content.read(len(BODY)))) < len(BODY)


def verify_an_oversized_body_is_rejected_rather_than_buffered() -> None:
    response = FakeResponse(BODY)
    assert asyncio.run(module.async_read_bounded(response, len(BODY) - 1)) is None
    # It stops early instead of reading the whole thing into memory first.
    assert response.content.chunks_served < 101


def verify_exactly_at_the_limit_still_passes() -> None:
    response = FakeResponse(BODY)
    assert asyncio.run(module.async_read_bounded(response, len(BODY))) == BODY


def verify_capped_read_returns_a_bounded_prefix_of_a_long_body() -> None:
    # For a page that is only scanned, a prefix is a legitimate answer -
    # unlike an image, where a truncated body is useless.
    response = FakeResponse(BODY)
    prefix = asyncio.run(module.async_read_capped(response, 4096))
    assert len(prefix) == 4096
    assert prefix == BODY[:4096]
    # A short body comes back complete.
    short = FakeResponse(b"kurz")
    assert asyncio.run(module.async_read_capped(short, 4096)) == b"kurz"


def verify_an_empty_body_is_empty_bytes_not_none() -> None:
    assert asyncio.run(module.async_read_bounded(FakeResponse(b""), 10)) == b""
    assert asyncio.run(module.async_read_capped(FakeResponse(b""), 10)) == b""


def verify_every_download_uses_it() -> None:
    package_root = Path("custom_components/roadplanner_mcp")
    offenders = []
    for path in package_root.glob("*.py"):
        if path.name == "http_read.py":
            continue
        if "content.read(" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, (
        "content.read(n) truncates a chunked body - use async_read_bounded/"
        f"async_read_capped instead: {offenders}"
    )


if __name__ == "__main__":
    verify_the_whole_body_arrives_not_just_the_first_chunk()
    verify_an_oversized_body_is_rejected_rather_than_buffered()
    verify_exactly_at_the_limit_still_passes()
    verify_capped_read_returns_a_bounded_prefix_of_a_long_body()
    verify_an_empty_body_is_empty_bytes_not_none()
    verify_every_download_uses_it()
    print("Bounded HTTP read tests passed.")
