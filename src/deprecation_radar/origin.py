"""Where a warning comes from: your code, the standard library, or an installed package.

A warning only carries a category, a message, a file and a line. Everything else is
worked out from those: which package owns the file, which package defined the warning
category, what the message names, and what version of that package is installed.
"""

from __future__ import annotations

import ast
import re
import sys
import sysconfig
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path

_NOT_YOURS = {"site-packages", "dist-packages", ".venv", "venv", ".tox", ".nox"}
_GENERIC_MODULES = {"builtins", "warnings", "exceptions"}


@dataclass(frozen=True)
class Source:
    """The package that deprecated something."""

    name: str  # "pydantic", "Python", "your project", "unknown"
    version: str | None = None
    kind: str = "package"  # "package", "python", "project" or "unknown"

    def label(self) -> str:
        if self.kind == "python":
            return f"Python {self.version} standard library"
        if self.kind == "unknown":
            return "Unknown source"
        return f"{self.name} {self.version}" if self.version else self.name


PYTHON = Source("Python", "{}.{}".format(*sys.version_info[:2]), "python")
UNKNOWN = Source("unknown", kind="unknown")


class Origins:
    def __init__(self, rootpath: Path) -> None:
        self.root = rootpath.resolve()
        self._scopes: dict[str, dict[int, str]] = {}

    @cache  # one Origins per run, so caching on self is fine
    def is_yours(self, filename: str) -> bool:
        try:
            relative = Path(filename).resolve().relative_to(self.root)
        except (ValueError, OSError):
            return False
        return not _NOT_YOURS.intersection(relative.parts)

    def function_at(self, filename: str, lineno: int) -> str:
        """The function or method around a line, or `<module>`. Each file is parsed once
        per run. Cached here, not for the whole process, so a file that changes between
        two runs in one process (as in this plugin's own tests) is read again."""
        if filename not in self._scopes:
            self._scopes[filename] = scopes(filename)
        return self._scopes[filename].get(lineno, "<module>")

    def relative(self, filename: str) -> str:
        try:
            return Path(filename).resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return filename

    @cache
    def module_of_file(self, filename: str) -> str | None:
        """The top-level module a file belongs to, e.g. `fastapi` for .../fastapi/testclient.py."""
        path = Path(filename).resolve()
        stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
        if path.is_relative_to(stdlib) and "site-packages" not in path.parts:
            return "<stdlib>"
        best: Path | None = None
        for entry in sys.path:
            try:
                base = Path(entry or ".").resolve()
            except OSError:
                continue
            if path.is_relative_to(base) and (best is None or len(base.parts) > len(best.parts)):
                best = base
        if best is None:
            return None
        first = path.relative_to(best).parts[0]
        return first.removesuffix(".py")

    def source_of_module(self, module: str) -> Source:
        top = module.split(".")[0]
        if top == "<stdlib>" or top in sys.stdlib_module_names:
            return PYTHON
        dists = _packages_distributions().get(top)
        if dists:
            return Source(dists[0], _version(dists[0]))
        spec_file = _module_file(top)
        if spec_file and self.is_yours(spec_file):
            return Source("your project", kind="project")
        return Source(top)

    def source_of(self, category: type[Warning], message: str, filename: str) -> Source:
        """Best guess at who deprecated the thing, most reliable evidence first."""
        # 1. Libraries often define their own category, e.g. PydanticDeprecatedSince20.
        module = category.__module__
        if module.split(".")[0] not in _GENERIC_MODULES:
            return self.source_of_module(module)
        # 2. The message often names the thing, e.g. "datetime.datetime.utcnow() is deprecated".
        for name in _names_in(message):
            top = name.split(".")[0]
            if top in sys.stdlib_module_names or top in _packages_distributions():
                return self.source_of_module(top)
        # 3. A warning raised inside a package (not at your line) is most likely about it.
        if not self.is_yours(filename):
            module = self.module_of_file(filename)
            if module is not None:
                return self.source_of_module(module)
        return UNKNOWN


_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_QUOTED = re.compile(r"""[`'"]([A-Za-z_]\w*)[`'"]""")


def _names_in(message: str) -> list[str]:
    return _DOTTED.findall(message) + _QUOTED.findall(message)


@cache
def _packages_distributions() -> dict[str, list[str]]:
    """Top-level module name -> installed distributions that provide it.

    `metadata.packages_distributions()` does this, but on Python 3.10 it only reads
    `top_level.txt`, which many modern wheels don't ship. So read each distribution's
    file list too, as Python 3.11 does.
    """
    mapping: dict[str, list[str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        top_level = dist.read_text("top_level.txt")
        if top_level:
            modules = set(top_level.split())
        else:
            modules = {_top_module(str(f)) for f in dist.files or ()} - {None}
        for module in modules:
            if name not in mapping.setdefault(module, []):
                mapping[module].append(name)
    return mapping


def _top_module(path: str) -> str | None:
    first = path.replace("\\", "/").split("/")[0]
    if first.endswith((".dist-info", ".egg-info", ".data", ".pth")) or first in ("..", "__pycache__"):
        return None
    if "/" not in path:  # a top-level file: only a module if it's Python
        return first.removesuffix(".py") if first.endswith(".py") else None
    return first


@cache
def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def _module_file(module: str) -> str | None:
    mod = sys.modules.get(module)
    return getattr(mod, "__file__", None) if mod else None


# "removed in V3.0", "will be removed in Python 3.14", "removed in SQLAlchemy 2.0",
# "removal in version 4". "removed in a future version" names no version, so no match.
_REMOVAL = re.compile(
    r"(?:removed|removal|dropped|deleted)\s+(?:in|from|with|by)\s+"
    r"(?P<name>[A-Za-z][\w-]*\s+)?v?(?P<version>\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
_NOT_A_NAME = {"version", "release", "the"}


def removal_version(message: str) -> str | None:
    m = _REMOVAL.search(message)
    if not m:
        return None
    name = (m.group("name") or "").strip()
    if name.lower() in _NOT_A_NAME:
        name = ""
    return f"{name} {m.group('version')}".strip()


def scopes(filename: str) -> dict[int, str]:
    """Line number -> the function or method around it, like `Order.total`."""
    try:
        tree = ast.parse(Path(filename).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    lines: dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                # Inner scopes are visited after, so they overwrite their lines.
                for line in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    lines[line] = name
                visit(child, f"{name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return lines
