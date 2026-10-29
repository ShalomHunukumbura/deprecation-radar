"""CI check: the demo's report must credit each deprecation to the right source and line."""

import json
import sys

report = json.load(open(sys.argv[1]))
found = {
    (d["source"], site["path"], site["line"])
    for d in report["deprecations"]
    for site in d["sites"]
    if site["yours"]
}
expected = {
    ("pydantic", "app.py", 20),  # class Config
    ("pydantic", "app.py", 35),  # parse_obj
    ("pydantic", "app.py", 36),  # .dict()
    ("pydantic", "test_app.py", 12),  # .json()
    ("fastapi", "app.py", 28),  # on_event
    ("Python", "app.py", 30),  # datetime.utcnow()
    ("SQLAlchemy", "app.py", 10),  # sqlalchemy.ext.declarative.declarative_base
}
missing = expected - found
if missing:
    sys.exit(f"missing from the report: {sorted(missing)}\nfound: {sorted(found)}")
print(f"all {len(expected)} expected deprecations found")
