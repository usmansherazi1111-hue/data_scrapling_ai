"""One TLS context for every httpx client: loading the CA bundle costs ~0.4 s and blocks the event loop, so do it once."""
from __future__ import annotations
import ssl

_CTX: ssl.SSLContext | None = None


def shared_ssl() -> ssl.SSLContext:
    global _CTX
    if _CTX is None:
        import certifi
        _CTX = ssl.create_default_context(cafile=certifi.where())
    return _CTX
