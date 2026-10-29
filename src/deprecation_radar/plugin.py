"""The pytest plugin: collect deprecation warnings during the run and report them by package.

pytest loads this module through the `pytest11` entry point as soon as the package is
installed. Turn it off for one run with `-p no:deprecation_radar`.
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest

from . import baseline
from .collect import Collector, Deprecation, Site
from .origin import Origins, Source

_MAX_MESSAGE = 160
_MAX_SITES = 5
_PHASES = {"config": "(while configuring)", "collect": "(while collecting)"}
_FAIL_MODES = ("new", "any", "never")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("deprecation-radar", "report deprecations grouped by package")
    group.addoption(
        "--deprecation-radar-json",
        metavar="PATH",
        help="Also write the full report as JSON to PATH.",
    )
    group.addoption(
        "--deprecation-radar-update-baseline",
        action="store_true",
        help="Write every deprecation your code uses to the baseline file, and don't fail.",
    )
    parser.addini(
        "deprecation_radar_fail",
        "When deprecations in your code fail the run: new (default; only those not in the "
        "baseline file, and only when one exists), any, or never.",
        default="new",
    )
    parser.addini(
        "deprecation_radar_ignore",
        "Sources to leave out of the report and the gate, e.g. pytest or Python.",
        type="args",
        default=[],
    )
    parser.addini(
        "deprecation_radar_baseline",
        "Baseline file of known deprecations, relative to the rootdir.",
        default=".deprecation-radar-baseline.json",
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
        self.ignore = {name.lower() for name in config.getini("deprecation_radar_ignore")}
        self.github = os.environ.get("GITHUB_ACTIONS") == "true"

        self.fail_mode = str(config.getini("deprecation_radar_fail")).lower()
        if self.fail_mode not in _FAIL_MODES:
            raise pytest.UsageError(
                f"deprecation_radar_fail: {self.fail_mode!r} is not one of {', '.join(_FAIL_MODES)}"
            )
        self.update_baseline = bool(config.getoption("deprecation_radar_update_baseline"))
        self.baseline_path = config.rootpath / str(config.getini("deprecation_radar_baseline"))
        self.known: set[baseline.Entry] | None = None  # None: no baseline file
        if self.baseline_path.is_file() and not self.update_baseline:
            try:
                self.known = baseline.load(self.baseline_path)
            except baseline.BaselineError as exc:
                raise pytest.UsageError(f"deprecation-radar: {exc}") from None
        self.baseline_written: int | None = None
        self.failing: set[tuple[int, int]] = set()

    def pytest_warning_recorded(
        self, warning_message: warnings.WarningMessage, when: str, nodeid: str
    ) -> None:
        test = nodeid or _PHASES.get(when, "(outside any test)")
        self.collector.add(warning_message, test)

    # --- deciding what fails -------------------------------------------------

    def _groups(self) -> list[tuple[Source, list[Deprecation]]]:
        return [(s, deps) for s, deps in self.collector.by_source() if s.name.lower() not in self.ignore]

    @staticmethod
    def _entry(d: Deprecation, site: Site) -> baseline.Entry:
        return baseline.Entry(site.path, site.function, d.category, baseline.message_key(d.message))

    def _your_uses(self) -> list[tuple[Deprecation, Site]]:
        return [(d, site) for _, deps in self._groups() for d in deps for site in d.your_sites()]

    def _failing(self) -> set[tuple[int, int]]:
        """Uses of deprecations in your code that fail the run, as (id(d), id(site))."""
        if self.update_baseline or self.fail_mode == "never":
            return set()
        uses = self._your_uses()
        if self.fail_mode == "new":
            if self.known is None:
                return set()
            uses = [(d, s) for d, s in uses if self._entry(d, s) not in self.known]
        return {(id(d), id(s)) for d, s in uses}

    # --- hooks ---------------------------------------------------------------

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.json_path is not None:
            self.json_path.write_text(json.dumps(self._as_json(), indent=2) + "\n", encoding="utf-8")
        if self.update_baseline:
            entries = (self._entry(d, s) for d, s in self._your_uses())
            self.baseline_written = baseline.save(self.baseline_path, entries)
        self.failing = self._failing()
        if self.failing and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter) -> None:
        tr = terminalreporter
        groups = self._groups()
        if not groups:
            tr.write_line("deprecation-radar: no deprecation warnings in this run.", green=True)
        else:
            self._write_report(tr, groups)
        if self.baseline_written is not None:
            n = self.baseline_written
            tr.write_line(
                f"deprecation-radar: wrote {n} known use{_s(n)} to "
                f"{self.origins.relative(str(self.baseline_path))}.",
                green=True,
            )
        self._write_baseline_notes(tr)
        if self.github:
            self._write_annotations(tr)

    def _write_report(self, tr, groups: list[tuple[Source, list[Deprecation]]]) -> None:
        failing = bool(self.failing)
        tr.write_sep("=", "deprecation-radar", red=failing, yellow=not failing)
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

        deprecations = [d for _, deps in groups for d in deps]
        yours = sum(len(d.your_sites()) for d in deprecations)
        summary = (
            f"deprecation-radar: {len(deprecations)} deprecation{_s(len(deprecations))} from "
            f"{len(groups)} source{_s(len(groups))}, used at {yours} place{_s(yours)} in your code"
        )
        if failing:
            n = len(self.failing)
            what = "new " if self.fail_mode == "new" else ""
            summary += f"; {n} {what}use{_s(n)} fail{'s' if n == 1 else ''} the run"
        elif self.known is not None:
            summary += ", all in the baseline"
        tr.write_line(summary + ".", red=failing, yellow=not failing, bold=True)

    def _write_deprecation(self, tr, d: Deprecation) -> None:
        tr.line(f"  {d.category}: {_shorten(d.message)}")
        # Failing uses first, then the rest of your code, then sites inside packages.
        sites = sorted(
            d.sites.values(),
            key=lambda s: ((id(d), id(s)) not in self.failing, not s.yours, -s.count, s.path, s.lineno),
        )
        for site in sites[:_MAX_SITES]:
            new = (id(d), id(site)) in self.failing
            tr.line(f"    {'NEW ' if new and self.fail_mode == 'new' else ''}{_describe(site)}", red=new)
        if len(sites) > _MAX_SITES:
            tr.line(f"    ... and {len(sites) - _MAX_SITES} more")

    def _write_baseline_notes(self, tr) -> None:
        if self.known is None:
            return
        seen = {self._entry(d, s) for d, s in self._your_uses()}
        stale = len(self.known - seen)
        if stale:
            tr.write_line(
                f"deprecation-radar: {stale} baseline entr{'y was' if stale == 1 else 'ies were'} "
                "not seen in this run: fixed, or not run. After a full run, refresh the file "
                "with --deprecation-radar-update-baseline."
            )

    def _write_annotations(self, tr) -> None:
        """Workflow commands GitHub shows as notes on the pull request diff.

        Failing uses are errors. Without a gate, every use in your code is a warning. Uses
        already in the baseline are known debt and get no note.
        """
        for d, site in self._your_uses():
            if (id(d), id(site)) in self.failing:
                level = "error"
            elif self.known is None:
                level = "warning"
            else:
                continue
            title = f"deprecation-radar: {d.category}"
            body = f"{d.message} (from {d.source.label()})"
            tr.write_line(
                f"::{level} file={_escape_prop(site.path)},line={site.lineno},"
                f"title={_escape_prop(title)}::{_escape_data(body)}"
            )

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
                for source, deprecations in self._groups()
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


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")
