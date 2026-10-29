"""The baseline: deprecations your code already uses and has agreed to fix later.

With a baseline file, only new uses fail the run. Entries leave out line numbers, so
editing code above a call doesn't make an old use look new, and leave out URLs, because
messages like Pydantic's link to a page with the installed version in it
(errors.pydantic.dev/2.14/...), which would make every entry look new after a minor upgrade.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

VERSION = 1

_URL = re.compile(r"\s*\(?https?://\S+")


class Entry(NamedTuple):
    path: str  # relative to the pytest rootdir, with forward slashes
    function: str
    category: str
    message: str  # normalised, without URLs


def message_key(message: str) -> str:
    return " ".join(_URL.sub("", message).split())


class BaselineError(Exception):
    pass


def load(path: Path) -> set[Entry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            raise BaselineError(f"unsupported version {data.get('version')!r}")
        return {Entry(**item) for item in data["deprecations"]}
    except BaselineError as exc:
        raise BaselineError(f"{path}: {exc}") from None
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise BaselineError(f"{path}: not a deprecation-radar baseline ({exc})") from None


def save(path: Path, entries: Iterable[Entry]) -> int:
    items = sorted(set(entries))
    data = {"version": VERSION, "deprecations": [e._asdict() for e in items]}
    # Sorted, one field per line, so a change shows up as a small, readable diff.
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return len(items)
