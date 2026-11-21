"""Reading a Garage16 setting of the desktop client from the environment — the same as server/core/env.py.

Every setting is called GARAGE16_<NAME> since V1.0.2. Up to V1.0.1 it was LMU_GARAGE_<NAME> — the
project's first name — and a .env from then keeps working: when GARAGE16_<NAME> is not set at all,
LMU_GARAGE_<NAME> is read. (docker-compose.yml does the same when it fills in the containers.)
"""

from __future__ import annotations

import os
from typing import Optional, overload

PREFIX = "GARAGE16_"
LEGACY_PREFIX = "LMU_GARAGE_"


@overload
def env(name: str) -> Optional[str]: ...
@overload
def env(name: str, default: str) -> str: ...


def env(name, default=None):
    """GARAGE16_<name>, else LMU_GARAGE_<name>, else `default`. An empty value counts as set (as
    os.environ.get always did here) — it is how a few settings are switched off on purpose."""
    for key in (PREFIX + name, LEGACY_PREFIX + name):
        value = os.environ.get(key)
        if value is not None:
            return value
    return default
