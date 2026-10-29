import sys
import warnings
from pathlib import Path

import pytest

from deprecation_radar.collect import is_deprecation, normalise
from deprecation_radar.origin import PYTHON, UNKNOWN, Origins, _top_module, function_at, removal_version


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Deprecated in Pydantic V2.0 to be removed in V3.0.", "3.0"),
        ("utcnow() is deprecated and will be removed in Python 3.16", "Python 3.16"),
        ("The Query.get() method is deprecated and will be removed in SQLAlchemy 2.0", "SQLAlchemy 2.0"),
        ("This will be removed in version 4", "4"),
        ("Support will be dropped in 1.10.2", "1.10.2"),
        ("datetime.utcnow() is deprecated and scheduled for removal in a future version.", None),
        ("old() is deprecated", None),
    ],
)
def test_removal_version(message: str, expected: str | None) -> None:
    assert removal_version(message) == expected


class LibDeprecationWarning(UserWarning):
    pass


class RemovedInFoo3Warning(PendingDeprecationWarning):
    pass


@pytest.mark.parametrize(
    ("category", "message", "expected"),
    [
        (DeprecationWarning, "anything", True),
        (FutureWarning, "pandas will change", True),
        (RemovedInFoo3Warning, "anything", True),
        (LibDeprecationWarning, "Using httpx", True),  # named like one, though a UserWarning
        (UserWarning, "foo() is deprecated, use bar()", True),
        (UserWarning, "'orm_mode' has been renamed to 'from_attributes'", True),
        (UserWarning, "this option will be removed soon", True),
        (UserWarning, "config file not found", False),
        (RuntimeWarning, "coroutine was never awaited", False),
    ],
)
def test_is_deprecation(category: type[Warning], message: str, expected: bool) -> None:
    assert is_deprecation(category, message) is expected


def test_normalise_groups_repeats() -> None:
    assert normalise("<Thing at 0x7f3a2b1c9d40>  is\n deprecated") == "<Thing at 0x...> is deprecated"


def test_function_at(tmp_path: Path) -> None:
    src = tmp_path / "mod.py"
    src.write_text(
        "x = 1\n"
        "class Order:\n"
        "    def total(self):\n"
        "        def inner():\n"
        "            return 2\n"
        "        return inner()\n"
        "async def fetch():\n"
        "    pass\n"
    )
    assert function_at(str(src), 1) == "<module>"
    assert function_at(str(src), 3) == "Order.total"
    assert function_at(str(src), 5) == "Order.total.inner"
    assert function_at(str(src), 6) == "Order.total"
    assert function_at(str(src), 8) == "fetch"
    assert function_at(str(tmp_path / "missing.py"), 1) == "<module>"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("starlette/exceptions.py", "starlette"),
        ("six.py", "six"),
        ("typing_extensions.py", "typing_extensions"),
        ("starlette-1.7.0.dist-info/RECORD", None),
        ("../../bin/uvicorn", None),
        ("README.md", None),
    ],
)
def test_top_module(path: str, expected: str | None) -> None:
    assert _top_module(path) == expected


def test_source_from_a_library_category(tmp_path: Path) -> None:
    source = Origins(tmp_path).source_of(pytest.PytestDeprecationWarning, "x", __file__)
    assert source.name == "pytest"
    assert source.version == pytest.__version__


def test_source_from_the_message(tmp_path: Path) -> None:
    message = "datetime.datetime.utcnow() is deprecated"
    assert Origins(tmp_path).source_of(DeprecationWarning, message, __file__) == PYTHON


def test_source_unknown(tmp_path: Path) -> None:
    yours = str(tmp_path / "app.py")
    assert Origins(tmp_path).source_of(DeprecationWarning, "old() is deprecated", yours) == UNKNOWN


def test_source_from_the_package_that_raised_it(tmp_path: Path) -> None:
    # A generic warning raised inside pytest's own code, naming nothing: credit pytest.
    source = Origins(tmp_path).source_of(UserWarning, "x will be removed", pytest.__file__)
    assert source.name == "pytest"


def test_source_from_a_warning_raised_in_the_standard_library(tmp_path: Path) -> None:
    assert Origins(tmp_path).source_of(DeprecationWarning, "no names here", warnings.__file__) == PYTHON


def test_your_code_vs_installed_code(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    origins = Origins(tmp_path)
    assert origins.is_yours(str(tmp_path / "app.py"))
    assert not origins.is_yours(str(tmp_path / ".venv" / "lib" / "x.py"))
    assert not origins.is_yours(pytest.__file__)
    assert PYTHON.label() == "Python {}.{} standard library".format(*sys.version_info[:2])
