"""The pytest plugin: collect deprecation warnings during the run and report them by package.

pytest loads this module through the `pytest11` entry point as soon as the package is
installed. Turn it off for one run with `-p no:deprecation_radar`.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from .collect import Collector, Deprecation, Site
from .origin import Origins

_MAX_MESSAGE = 160
_MAX_SITES = 5
_PHASES = {"config": "(while configuring)", "collect": "(while collecting)"}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("deprecation-radar", "report deprecations grouped by package")
    group.addoption(
        "--deprecation-radar-json",
        metavar="PATH",
        help="Also write the full report as JSON to PATH.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):  # a pytest-xdist worker
        return
    config.pluginmanager.register(DeprecationRadar(config), "deprecation-radar-session")


class DeprecationRadar:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.origins = Origins(config.rootpath)
        self.collector = Collector(self.origins)
        json_path = config.getoption("deprecation_radar_json")
        self.json_path = Path(json_path) if json_path else None

    def pytest_warning_recorded(
        self, warning_message: warnings.WarningMessage, when: str, nodeid: str
    ) -> None:
        test = nodeid or _PHASES.get(when, "(outside any test)")
        self.collector.add(warning_message, test)

    def pytest_sessionfinish(self) -> None:
        if self.json_path is not None:
            self.json_path.write_text(json.dumps(self._as_json(), indent=2) + "\n", encoding="utf-8")

    def pytest_terminal_summary(self, terminalreporter) -> None:
        tr = terminalreporter
        groups = self.collector.by_source()
        if not groups:
            tr.write_line("deprecation-radar: no deprecation warnings in this run.", green=True)
            return

        tr.write_sep("=", "deprecation-radar", yellow=True)
        for source, deprecations in groups:
            removals = sorted({d.removal for d in deprecations if d.removal})
            header = f"{source.label()}: {len(deprecations)} deprecation{_s(len(deprecations))}"
            if removals:
                header += f", removal in {', '.join(removals)}"
            if not any(d.your_sites() for d in deprecations):
                header += ", used only inside other packages"
            tr.write_line(header, bold=True)
            for d in deprecations:
                self._write_deprecation(tr, d)

        total = len(self.collector.deprecations)
        sources = len(groups)
        yours = sum(len(d.your_sites()) for d in self.collector.deprecations.values())
        tr.write_line(
            f"deprecation-radar: {total} deprecation{_s(total)} from {sources} "
            f"source{_s(sources)}, used at {yours} place{_s(yours)} in your code.",
            yellow=True,
            bold=True,
        )

    def _write_deprecation(self, tr, d: Deprecation) -> None:
        tr.line(f"  {d.category}: {_shorten(d.message)}")
        # Your code first: those are the lines you can change.
        sites = sorted(d.sites.values(), key=lambda s: (not s.yours, -s.count, s.path, s.lineno))
        for site in sites[:_MAX_SITES]:
            tr.line(f"    {_describe(site)}")
        if len(sites) > _MAX_SITES:
            tr.line(f"    ... and {len(sites) - _MAX_SITES} more")

    def _as_json(self) -> dict:
        return {
            "version": 1,
            "deprecations": [
                {
                    "source": source.name,
                    "source_version": source.version,
                    "category": d.category,
                    "message": d.message,
                    "removal": d.removal,
                    "count": d.count,
                    "sites": [
                        {
                            "path": s.path,
                            "line": s.lineno,
                            "function": s.function,
                            "yours": s.yours,
                            "package": s.package,
                            "count": s.count,
                            "tests": s.tests,
                        }
                        for s in d.sites.values()
                    ],
                }
                for source, deprecations in self.collector.by_source()
                for d in deprecations
            ],
        }


def _describe(site: Site) -> str:
    times = f" ({site.count} times)" if site.count > 1 else ""
    if site.yours:
        return f"{site.path}:{site.lineno} in {site.function}{times}"
    path = site.path.split("site-packages/", 1)[-1]
    inside = f"inside {site.package}: " if site.package else ""
    return f"{inside}{path}:{site.lineno}{times}"


def _shorten(text: str) -> str:
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 3] + "..."


def _s(n: int) -> str:
    return "" if n == 1 else "s"
