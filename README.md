# deprecation-radar

**See every deprecation your code depends on, grouped by library, before the upgrade breaks it.**

pytest already shows deprecation warnings: as a long list at the end of every run, repeated
once per test, mixed with warnings from inside your dependencies. Everyone learns to scroll
past it. Then Pydantic 3 or Python 3.16 comes out and the list turns into errors.

deprecation-radar is a pytest plugin that turns that list into a report you can act on. Here
it is on a small FastAPI app written the way many 2023 tutorials wrote it
([`examples/demo-app`](examples/demo-app)):

```console
$ pytest
..                                                                       [100%]
============================== deprecation-radar ===============================
pydantic 2.14.1: 5 deprecations, removal in 3.0
  PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. ...
    app.py:20 in User
  PydanticDeprecatedSince20: The `dict` method is deprecated; use `model_dump` instead. Deprecated in Pydantic V2.0 to be removed in V3.0. ...
    app.py:36 in get_user
  PydanticDeprecatedSince20: The `json` method is deprecated; use `model_dump_json` instead. Deprecated in Pydantic V2.0 to be removed in V3.0. ...
    test_app.py:12 in test_user_json
  PydanticDeprecatedSince20: The `parse_obj` method is deprecated; use `model_validate` instead. Deprecated in Pydantic V2.0 to be removed in V3.0. ...
    app.py:35 in get_user
  UserWarning: Valid config keys have changed in V2: * 'orm_mode' has been renamed to 'from_attributes'
    inside pydantic: pydantic/_internal/_config.py:408
fastapi 0.162.0: 1 deprecation
  DeprecationWarning: on_event is deprecated, use lifespan event handlers instead. ...
    app.py:28 in <module>
    inside fastapi: fastapi/applications.py:4723
Python 3.12 standard library: 1 deprecation
  DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. ...
    app.py:30 in startup
SQLAlchemy 2.1.6: 1 deprecation
  MovedIn20Warning: The ``declarative_base()`` function is now available as sqlalchemy.orm.declarative_base(). (deprecated since: 2.0) ...
    app.py:10 in <module>
starlette 1.10.0: 1 deprecation, used only inside other packages
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    inside fastapi: fastapi/testclient.py:1
deprecation-radar: 9 deprecations from 5 sources, used at 7 places in your code.
2 passed, 10 warnings in 2.03s
```

For each deprecation you get:

- **the library** that deprecated it and the version you have installed,
- **the version that removes it**, when the message says so, with those libraries listed first,
- **the lines of your code** that use it, with the function they're in and how often they
  fired,
- deprecations **inside your dependencies**, kept apart, because the fix there is upgrading
  the dependency, not changing your code.

## Install

```bash
pip install pytest-deprecation-radar     # or: uv add --dev pytest-deprecation-radar
```

pytest loads the plugin on its own. The next `pytest` run prints the report.

## What counts as a deprecation

- `DeprecationWarning`, `PendingDeprecationWarning` and `FutureWarning`, and their
  subclasses (Pydantic's `PydanticDeprecatedSince20`, SQLAlchemy's `MovedIn20Warning`,
  Django's `RemovedInDjango60Warning`).
- Warnings whose class is named like a deprecation even when it isn't a subclass of one.
  Starlette's and FastAPI's deprecation warnings subclass `UserWarning` on purpose, because
  Python hides `DeprecationWarning` by default.
- Any warning whose message says "deprecated", "has been renamed" or "will be removed".

Warnings you've silenced with `filterwarnings = ignore::...` stay silenced.

## JSON report

```bash
pytest --deprecation-radar-json=deprecations.json
```

Every deprecation with its source, versions, every site, and the tests that triggered it.
Useful for tracking the count over time, or for an upgrade ticket.

## Limits

- It sees what your tests run. Code with no tests isn't checked.
- A library decides which line a warning points at (`stacklevel`). When it points inside the
  library, the report says so (`inside pydantic: ...`) instead of guessing at your line.
- Which library deprecated something is worked out from the warning's class, the names in
  its message, and the file that raised it. When none of those say, the report files it
  under "Unknown source".

## Status

Early. Next: a baseline file so new deprecations fail the run while old ones get fixed over
time, settings to hide libraries you can't act on, and GitHub annotations.

## License

MIT
