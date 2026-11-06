import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import proxy_mode as _proxy_mode  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_home: test intentionally resolves runtime copies in the real "
        "HOME (deployment checks such as the runtime tree's live layout); "
        "it opts out of "
        "the default per-test HOME isolation")


@pytest.fixture(autouse=True)
def _isolated_home(request, monkeypatch, tmp_path):
    """Every test runs against an isolated HOME by default: the suite must
    never read or write the operator's live ~/.cache/greysearch (task
    registry, slot claims, probe caches). With live state present,
    test_catalog_lane_budget_reached_recorded failed in a full-file
    run while passing under an isolated HOME, so nothing in the suite is
    allowed to depend on live runtime state. Tests marked real_home opt out."""
    if request.node.get_closest_marker("real_home"):
        sentinel = Path.home() / "greysearch"
        if not sentinel.is_dir():
            pytest.skip("deployment layout (~/greysearch) not present "
                        "on this machine", allow_module_level=False)
        return
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _browser_pace_off(monkeypatch):
    """The suite runs the browser-lane search pace guard INERT via the
    documented GREYSEARCH_BROWSER_PACE_S=0 bypass, so no test ever sleeps
    on the 30 s human pace. The pace tests set the env back to a pace and
    verify the guard itself; every other test is unaffected."""
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "0")


def write_fixture_env(path, value=None):
    """A synthetic env file with NO real credentials: 127.0.0.1:9 proxy
    values that cannot route anywhere. Returns the parsed value so the
    test can assert on the exact string it seeded."""
    val = value or "http://testsuite:examplepass@127.0.0.1:12345"
    path.write_text(
        "# synthetic fixture; not a real endpoint\n"
        f"HTTP_PROXY={val}\n"
        f"HTTPS_PROXY={val}\n",
        encoding="utf-8")
    return val


def set_env_file(monkeypatch, path):
    monkeypatch.setattr(_proxy_mode, "ENV_FILE", str(path))
