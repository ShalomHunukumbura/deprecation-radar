"""Turn a stream of warnings into a short list of deprecations and where they're used.

A deprecation is one category plus one message. The same deprecation can fire from many
places (sites), many times each. pytest may record the same warning hundreds of times;
the report needs each deprecation once, with its sites counted.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from .origin import Origins, Source, removal_version

CATEGORIES = (DeprecationWarning, PendingDeprecationWarning, FutureWarning)
# Some libraries subclass UserWarning on purpose, because Python hides DeprecationWarning
# by default: Starlette's and FastAPI's deprecation warnings do this. So a class named
# like a deprecation, or a message that says so (Pydantic's "'orm_mode' has been renamed"
# is a plain UserWarning), counts too.
_DEPRECATION_NAME = re.compile(r"Deprecat|Removed", re.IGNORECASE)
_DEPRECATION_MESSAGE = re.compile(
    r"\bdeprecat(?:ed|ion)\b|\bhas been renamed\b|\bwill be removed\b", re.IGNORECASE
)

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")


@dataclass
class Site:
    """One line that triggered a deprecation."""

    path: str  # relative to the rootdir for your code, absolute otherwise
    lineno: int
    function: str
    yours: bool
    package: str | None  # for a site inside an installed package, which one
    count: int = 0
    tests: list[str] = field(default_factory=list)


@dataclass
class Deprecation:
    category: str
    message: str
    source: Source
    removal: str | None
    sites: dict[tuple[str, int], Site] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return sum(s.count for s in self.sites.values())

    def your_sites(self) -> list[Site]:
        return [s for s in self.sites.values() if s.yours]


class Collector:
    def __init__(self, origins: Origins) -> None:
        self.origins = origins
        self.deprecations: dict[tuple[str, str], Deprecation] = {}
        self.warnings_seen = 0

    def add(self, warning: warnings.WarningMessage, test: str) -> None:
        category = warning.category
        message = normalise(str(warning.message))
        if not is_deprecation(category, message):
            return
        self.warnings_seen += 1
        key = (f"{category.__module__}.{category.__qualname__}", message)

        deprecation = self.deprecations.get(key)
        if deprecation is None:
            deprecation = self.deprecations[key] = Deprecation(
                category=category.__name__,
                message=message,
                source=self.origins.source_of(category, message, warning.filename),
                removal=removal_version(message),
            )

        where = (warning.filename, warning.lineno)
        site = deprecation.sites.get(where)
        if site is None:
            yours = self.origins.is_yours(warning.filename)
            site = deprecation.sites[where] = Site(
                path=self.origins.relative(warning.filename) if yours else warning.filename,
                lineno=warning.lineno,
                function=self.origins.function_at(warning.filename, warning.lineno),
                yours=yours,
                package=None if yours else self._package_of(warning.filename),
            )
        site.count += 1
        if test not in site.tests:
            site.tests.append(test)

    def _package_of(self, filename: str) -> str | None:
        module = self.origins.module_of_file(filename)
        if module is None:
            return None
        if module == "<stdlib>":
            return "Python"
        return self.origins.source_of_module(module).name

    def by_source(self) -> list[tuple[Source, list[Deprecation]]]:
        """Sources with a known removal version first, then those your code uses most."""
        groups: dict[Source, list[Deprecation]] = {}
        for d in self.deprecations.values():
            groups.setdefault(d.source, []).append(d)
        for deps in groups.values():
            deps.sort(key=lambda d: (d.removal is None, -len(d.your_sites()), -d.count, d.message))
        return sorted(
            groups.items(),
            key=lambda item: (
                all(d.removal is None for d in item[1]),
                -sum(len(d.your_sites()) for d in item[1]),
                item[0].label().lower(),
            ),
        )


def is_deprecation(category: type[Warning], message: str) -> bool:
    if issubclass(category, CATEGORIES):
        return True
    if any(_DEPRECATION_NAME.search(cls.__name__) for cls in category.__mro__):
        return True
    return bool(_DEPRECATION_MESSAGE.search(message))


def normalise(message: str) -> str:
    """One line, single spaces, and no memory addresses, so repeats group together."""
    return _ADDRESS.sub("0x...", " ".join(message.split()))
