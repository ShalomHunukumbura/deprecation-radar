"""End-to-end tests: run pytest on a small throwaway project with the plugin loaded."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

# A stand-in for an installed library. It lives outside the test project's rootdir,
# so the plugin treats it as someone else's code, the way it treats site-packages.
FAKELIB = '''
import warnings


class FakelibDeprecationWarning(DeprecationWarning):
    pass


def old():
    warnings.warn(
        "old() is deprecated and will be removed in fakelib 2.0, use new()",
        FakelibDeprecationWarning,
        stacklevel=2,
    )


def internal():
    old()  # fakelib calling its own deprecated function
'''


@pytest.fixture
def fakelib(pytester: pytest.Pytester) -> Path:
    lib = pytester.path.parent / f"{pytester.path.name}-site"
    (lib / "fakelib").mkdir(parents=True)
    (lib / "fakelib" / "__init__.py").write_text(FAKELIB)
    pytester.syspathinsert(lib)
    return lib


def test_library_deprecation_points_at_your_code(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        app="""
        import fakelib

        def use_it():
            for _ in range(3):
                fakelib.old()

        def once_more():
            fakelib.old()
        """,
        test_app="""
        import app

        def test_app():
            app.use_it()
            app.once_more()
        """,
    )
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(
        [
            "*= deprecation-radar =*",
            "fakelib: 1 deprecation, removal in fakelib 2.0",
            "  FakelibDeprecationWarning: old() is deprecated and will be removed in fakelib 2.0, use new()",
            "    app.py:5 in use_it (3 times)",
            "    app.py:8 in once_more",
            "deprecation-radar: 1 deprecation from 1 source, used at 2 places in your code.",
        ]
    )


def test_deprecation_inside_another_package(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        """
        import fakelib

        def test_it():
            fakelib.internal()
        """
    )
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(
        [
            "fakelib: 1 deprecation, removal in fakelib 2.0, used only inside other packages",
            "    inside fakelib: */fakelib/__init__.py:18",
            "*used at 0 places in your code.",
        ]
    )


def test_standard_library_deprecation(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import warnings

        def test_it():
            warnings.warn("datetime.datetime.utcnow() is deprecated", DeprecationWarning)
        """
    )
    result = pytester.runpytest()
    version = "{}.{}".format(*sys.version_info[:2])
    result.stdout.fnmatch_lines(
        [
            f"Python {version} standard library: 1 deprecation",
            "  DeprecationWarning: datetime.datetime.utcnow() is deprecated",
            "    test_standard_library_deprecation.py:4 in test_it",
        ]
    )


def test_deprecation_that_subclasses_user_warning(pytester: pytest.Pytester) -> None:
    # Starlette and FastAPI do this so their warnings show by default.
    pytester.makepyfile(
        """
        import warnings

        class StarletteDeprecationWarning(UserWarning):
            pass

        def test_it():
            warnings.warn("Using httpx with the test client", StarletteDeprecationWarning)
            warnings.warn("config file not found")  # not a deprecation
        """
    )
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["  StarletteDeprecationWarning: Using httpx with the test client"])
    assert "config file not found" not in result.stdout.str().split("= deprecation-radar =")[1]


def test_warning_while_importing(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        """
        import fakelib

        fakelib.old()

        def test_it():
            pass
        """
    )
    result = pytester.runpytest("--deprecation-radar-json=report.json")
    result.stdout.fnmatch_lines(["    test_warning_while_importing.py:3 in <module>"])
    report = json.loads((pytester.path / "report.json").read_text())
    assert report["deprecations"][0]["sites"][0]["tests"] == ["(while collecting)"]


def test_json_report(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        """
        import fakelib

        def test_it():
            fakelib.old()
        """
    )
    pytester.runpytest("--deprecation-radar-json=radar.json")
    report = json.loads((pytester.path / "radar.json").read_text())
    assert report == {
        "version": 1,
        "deprecations": [
            {
                "source": "fakelib",
                "source_version": None,
                "category": "FakelibDeprecationWarning",
                "message": "old() is deprecated and will be removed in fakelib 2.0, use new()",
                "removal": "fakelib 2.0",
                "count": 1,
                "sites": [
                    {
                        "path": "test_json_report.py",
                        "line": 4,
                        "function": "test_it",
                        "yours": True,
                        "package": None,
                        "count": 1,
                        "tests": ["test_json_report.py::test_it"],
                    }
                ],
            }
        ],
    }


def test_sources_with_a_removal_date_come_first(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        """
        import warnings
        import fakelib

        def test_it():
            warnings.warn("old_thing() is deprecated", DeprecationWarning)
            warnings.warn("old_thing() is deprecated", DeprecationWarning)
            fakelib.old()
        """
    )
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["fakelib: *", "*", "*", "Unknown source: 1 deprecation"])


def test_many_sites_are_cut_short(pytester: pytest.Pytester, fakelib: Path) -> None:
    calls = "\n".join(f"    fakelib.old()  # {i}" for i in range(8))
    pytester.makepyfile(f"import fakelib\n\ndef test_it():\n{calls}\n")
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["    ... and 3 more"])


def test_ignored_warnings_stay_ignored(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        """
        import fakelib

        def test_it():
            fakelib.old()
        """
    )
    pytester.makeini("[pytest]\nfilterwarnings = ignore::DeprecationWarning\n")
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["deprecation-radar: no deprecation warnings in this run."])


def test_no_deprecations(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_ok():\n    pass\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["deprecation-radar: no deprecation warnings in this run."])


def test_can_be_disabled(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(
        textwrap.dedent(
            """
            import fakelib

            def test_it():
                fakelib.old()
            """
        )
    )
    result = pytester.runpytest("-p", "no:deprecation_radar")
    assert "deprecation-radar" not in result.stdout.str()


# --- milestone 4: baseline, fail modes, ignoring, annotations ------------------------

APP = """
import fakelib

def use_it():
    fakelib.old()
"""

TEST_APP = """
import app

def test_app():
    app.use_it()
"""


def test_without_a_baseline_nothing_fails(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    assert pytester.runpytest().ret == pytest.ExitCode.OK


def test_baseline_lets_known_uses_pass_and_new_ones_fail(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    result = pytester.runpytest("--deprecation-radar-update-baseline")
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["deprecation-radar: wrote 1 known use to .deprecation-radar-baseline.json."])
    saved = json.loads((pytester.path / ".deprecation-radar-baseline.json").read_text())
    assert saved == {
        "version": 1,
        "deprecations": [
            {
                "path": "app.py",
                "function": "use_it",
                "category": "FakelibDeprecationWarning",
                "message": "old() is deprecated and will be removed in fakelib 2.0, use new()",
            }
        ],
    }

    # Lines added above the call don't make it new.
    pytester.makepyfile(app="\n\n" + APP)
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*used at 1 place in your code, all in the baseline."])

    pytester.makepyfile(app=APP + "\ndef also():\n    fakelib.old()\n")
    pytester.makepyfile(test_app=TEST_APP + "    app.also()\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [
            "    NEW app.py:7 in also",
            "    app.py:4 in use_it",
            "*used at 2 places in your code; 1 new use fails the run.",
        ]
    )


def test_baseline_survives_a_version_in_a_message_url(pytester: pytest.Pytester) -> None:
    # Pydantic's messages link to errors.pydantic.dev/<installed version>/..., which
    # changes with every minor upgrade.
    lib = pytester.path.parent / f"{pytester.path.name}-site"
    (lib / "urllib_lib").mkdir(parents=True)
    pytester.syspathinsert(lib)

    def install(version: str) -> None:
        (lib / "urllib_lib" / "__init__.py").write_text(
            "import warnings\n"
            "def old():\n"
            f"    warnings.warn('old() is deprecated. See https://errors.example.dev/{version}/old', "
            "DeprecationWarning, stacklevel=2)\n"
        )

    pytester.makepyfile("import urllib_lib\n\ndef test_it():\n    urllib_lib.old()\n")
    install("2.14")
    pytester.runpytest("--deprecation-radar-update-baseline")
    install("2.15")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*all in the baseline."])


def test_fail_on_any(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    pytester.makeini("[pytest]\ndeprecation_radar_fail = any\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*; 1 use fails the run."])


def test_fail_never_with_a_baseline(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    (pytester.path / ".deprecation-radar-baseline.json").write_text('{"version": 1, "deprecations": []}')
    pytester.makeini("[pytest]\ndeprecation_radar_fail = never\n")
    assert pytester.runpytest().ret == pytest.ExitCode.OK


def test_uses_inside_packages_never_fail(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile("import fakelib\n\ndef test_it():\n    fakelib.internal()\n")
    pytester.makeini("[pytest]\ndeprecation_radar_fail = any\n")
    assert pytester.runpytest().ret == pytest.ExitCode.OK


def test_ignore_a_source(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    pytester.makeini("[pytest]\ndeprecation_radar_fail = any\ndeprecation_radar_ignore = FakeLib\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["deprecation-radar: no deprecation warnings in this run."])


def test_bad_fail_mode_is_a_usage_error(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_ok():\n    pass\n")
    pytester.makeini("[pytest]\ndeprecation_radar_fail = sometimes\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*'sometimes' is not one of new, any, never*"])


def test_broken_baseline_is_a_usage_error(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_ok():\n    pass\n")
    (pytester.path / ".deprecation-radar-baseline.json").write_text("[1, 2]")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*not a deprecation-radar baseline*"])


def test_baseline_reports_entries_no_longer_seen(pytester: pytest.Pytester, fakelib: Path) -> None:
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    pytester.runpytest("--deprecation-radar-update-baseline")
    pytester.makepyfile(app="def use_it():\n    pass\n")
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["deprecation-radar: 1 baseline entry was not seen in this run*"])


def test_github_annotations_without_a_baseline(
    pytester: pytest.Pytester, fakelib: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(
        [
            "::warning file=app.py,line=4,title=deprecation-radar%3A FakelibDeprecationWarning::"
            "old() is deprecated and will be removed in fakelib 2.0, use new() (from fakelib)"
        ]
    )


def test_github_annotations_mark_only_new_uses(
    pytester: pytest.Pytester, fakelib: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    pytester.makepyfile(app=APP, test_app=TEST_APP)
    pytester.runpytest("--deprecation-radar-update-baseline")
    pytester.makepyfile(app=APP + "\ndef also():\n    fakelib.old()\n")
    pytester.makepyfile(test_app=TEST_APP + "    app.also()\n")
    output = pytester.runpytest().stdout.str()
    assert "::error file=app.py,line=7," in output
    assert "line=4" not in output
