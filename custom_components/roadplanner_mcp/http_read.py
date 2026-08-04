"""Read a bounded HTTP body COMPLETELY, or reject it for being too large.

``await response.content.read(limit)`` looks like "read up to limit bytes",
and that is exactly the trap: aiohttp's ``StreamReader.read(n)`` returns
whatever is *currently buffered*, up to n - it does not wait for n bytes.
On a streaming response that is the first chunk, so the body arrives
truncated with no error anywhere.

The consequences were everywhere and looked unrelated (live reports):

- photos whose JPEG header was intact but which no renderer could open
  ("8 Fotos ... keines davon ließ sich als Bild öffnen"),
- "0 Kartenbilder", because a truncated PNG tile fails to decode,
- a shared place page that "contained no GPS" because the coordinates sat
  past the first chunk.

This helper loops until EOF, and returns ``None`` as soon as the limit is
exceeded so an oversized body is still rejected rather than buffered.
"""

from __future__ import annotations

_CHUNK_SIZE = 64 * 1024


async def async_read_bounded(response, limit: int) -> bytes | None:
    """Return the full body, or None if it exceeds ``limit`` bytes."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def async_read_capped(response, limit: int) -> bytes:
    """Return the full body, or the first ``limit`` bytes of a longer one.

    For text that only needs to be scanned (an HTML page), a bounded
    prefix is a legitimate answer - unlike an image, where a truncated
    body is useless.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return b"".join(chunks)[:limit]
