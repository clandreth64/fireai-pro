"""Placeholder identities (Milestone 2.2C).

Draft files may be populated before real people are named. Any identity beginning with
``PLACEHOLDER_`` marks such a slot. It can AUTHOR draft content, but it can never satisfy a human
control:

  rule review · rule approval · rule-set approval · known-answer review (or count as a case author)
  · completeness-manifest review / approval · source-authorization changes

Every such attempt is refused with ``PLACEHOLDER_IDENTITY_NOT_PERMITTED``. This is in addition to (never
instead of) the existing independence rules (reviewer != author, etc.).
"""

from __future__ import annotations

from typing import Optional

PLACEHOLDER_PREFIX = "PLACEHOLDER_"
PLACEHOLDER_CODE = "PLACEHOLDER_IDENTITY_NOT_PERMITTED"


def is_placeholder(name: Optional[str]) -> bool:
    return bool(name) and name.strip().upper().startswith(PLACEHOLDER_PREFIX)


def placeholder_message(name: str, control: str) -> str:
    return (f"{PLACEHOLDER_CODE}: {name!r} is a placeholder identity and cannot satisfy {control}; a real, named "
            "person must replace it")
