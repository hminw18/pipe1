from __future__ import annotations

import secrets
import time
import uuid

_RAND_A_BITS = 12
_RAND_B_BITS = 62


def uuid7() -> uuid.UUID:
    """Return an RFC 9562 version 7 UUID (48-bit Unix ms timestamp + 74 random bits).

    Time-ordered, so primary-key inserts stay index-friendly, and not guessable
    from neighbouring ids.
    """
    unix_ms = time.time_ns() // 1_000_000
    rand = secrets.randbits(_RAND_A_BITS + _RAND_B_BITS)
    rand_a = rand >> _RAND_B_BITS
    rand_b = rand & ((1 << _RAND_B_BITS) - 1)
    value = (unix_ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)
