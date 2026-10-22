"""request_context.py — the current request id, for code that has no Request object (V0.9.9).

Set by the request middleware (server/main.py); copied into the worker thread of a sync endpoint
(Starlette runs them via anyio, which carries context variables along). None outside a request
(scripts, background work)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("garage16_request_id", default=None)


def set_request_id(value: Optional[str]):
    return _request_id.set(value)


def reset(token) -> None:
    _request_id.reset(token)


def current_request_id() -> Optional[str]:
    return _request_id.get()
