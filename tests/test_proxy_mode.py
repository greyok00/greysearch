"""proxy_mode contract tests: mode parsing, env-file loading from a
synthetic fixture (no real credentials), precedence, structured errors,
per-call opener construction (urllib mocked), ladder notes, and the
scrape guard. No network access happens in this file."""
import io

import pytest
import urllib.request

import proxy_mode as pm
from conftest import write_fixture_env, set_env_file


# ---- mode parsing ---------------------------------------------------------

def test_normalized_mode_accepts_valid_values():
    assert pm.normalized_mode("off") == "off"
    assert pm.normalized_mode("  OFF ") == "off"
    assert pm.normalized_mode("on") == "on"
    assert pm.normalized_mode("on") == "on"


def test_normalized_mode_none_or_empty_means_no_preference():
    assert pm.normalized_mode(None) is None
    assert pm.normalized_mode("") is None
    assert pm.normalized_mode("   ") is None


def test_normalized_mode_rejects_unknown_values():
    for bad in ("proxy9", "always", "direct", "auto"):
        with pytest.raises(ValueError):
            pm.normalized_mode(bad)


def test_default_mode_is_off(tmp_path):
    monkey = pytest.MonkeyPatch()
    monkey.setenv("GREYSEARCH_SEARCH_PROXY", "")
    set_env_file(monkey, tmp_path / "no-file.env")
    res = pm.resolve_proxy_mode(None)
    assert res == {"ok": True, "mode": "off", "proxy": None, "error": "",
                   "env_file": str(tmp_path / "no-file.env")}
    monkey.undo()


# ---- env-file loading -----------------------------------------------------

def test_load_env_file_fixture_no_real_credentials(tmp_path):
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    assert ":12345" in val  # synthetic endpoint only
    creds = pm._load_env_file(str(path))
    assert creds == {"http": val, "https": val}


def test_load_env_file_mixed_case_and_quotes(tmp_path):
    path = tmp_path / "proxy.env"
    path.write_text('https_proxy="http://u:p@10.0.0.9:9"\n'
                    'HTTP_PROXY=\'http://u2:p2@10.0.0.9:10\'\n'
                    '\n'
                    '# comment\n'
                    'OTHER_KEY=ignore-me\n', encoding="utf-8")
    assert pm._load_env_file(str(path)) == {
        "http": "http://u2:p2@10.0.0.9:10",
        "https": "http://u:p@10.0.0.9:9"}


def test_load_env_file_missing_returns_none(tmp_path):
    assert pm._load_env_file(str(tmp_path / "absent.env")) is None


def test_load_env_file_without_proxy_values_returns_none(tmp_path):
    path = tmp_path / "empty.env"
    path.write_text("# only comments\nOTHER=1\n", encoding="utf-8")
    assert pm._load_env_file(str(path)) is None


# ---- resolution + structured errors --------------------------------------

def test_on_alias_resolves_from_fixture_file(tmp_path):
    monkey = pytest.MonkeyPatch()
    monkey.setenv("GREYSEARCH_SEARCH_PROXY", "")
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    set_env_file(monkey, path)
    res = pm.resolve_proxy_mode("on")
    assert res["ok"] is True
    assert res["mode"] == "on"
    assert res["proxy"] == {"http": val, "https": val}
    assert res["error"] == ""
    monkey.undo()


def test_on_missing_file_structured_error(tmp_path, monkeypatch):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    res = pm.resolve_proxy_mode("on")
    assert res["ok"] is False
    assert res["mode"] == "on"
    assert res["proxy"] is None
    assert res["error"] == "MISSING_ENVFILE"


def test_on_empty_file_no_proxy_values_error(tmp_path, monkeypatch):
    path = tmp_path / "empty.env"
    path.write_text("# no proxy keys here\n", encoding="utf-8")
    set_env_file(monkeypatch, path)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    res = pm.resolve_proxy_mode("on")
    assert res["ok"] is False
    assert res["error"] == "NO_PROXY_VALUES"


def test_env_override_wins_over_explicit_arg(tmp_path, monkeypatch):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "on")
    res = pm.resolve_proxy_mode("off")
    assert res["mode"] == "on"
    assert res["error"] == "MISSING_ENVFILE"
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "off")
    res = pm.resolve_proxy_mode("on")
    assert res["mode"] == "off"


def test_prepared_turns_bad_mode_into_error_record(tmp_path, monkeypatch):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    res = pm.prepared("proxy9")
    assert res["ok"] is False
    assert res["error"] == pm.BAD_MODE


def test_prepared_dict_passthrough(tmp_path):
    rec = {"ok": True, "mode": "on",
           "proxy": {"http": "x", "https": "y"}, "error": ""}
    assert pm.prepared(dict(rec)) == rec


# ---- opener construction (urllib mocked: never touches the network) -------

class _FakeHTTPResponse(io.BytesIO):
    pass


def _install_openers(monkeypatch, calls):
    class FakeProxyHandler:
        def __init__(self, cfg=None):
            self.cfg = cfg

    class FakeOpener:
        def __init__(self, handler):
            self.handler = handler
            self.opened = []

        def open(self, req, timeout=None):
            self.opened.append((req.full_url, timeout))
            calls.append(("opener.open", req.full_url, timeout,
                          self.handler.cfg))
            return _FakeHTTPResponse(b"fetched")

    monkeypatch.setattr(urllib.request, "ProxyHandler", FakeProxyHandler)
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *handlers: FakeOpener(handlers[0]))
    return FakeOpener


def test_off_builds_empty_proxy_handler(tmp_path, monkeypatch):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    calls = []
    _install_openers(monkeypatch, calls)
    opener = pm.build_search_opener("off")
    assert opener.handler.cfg == {}


def test_on_builds_credentialed_handler_from_fixture(
        tmp_path, monkeypatch):
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    set_env_file(monkeypatch, path)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    calls = []
    _install_openers(monkeypatch, calls)
    opener = pm.build_search_opener("on")
    assert opener.handler.cfg == {"http": val, "https": val}


def test_openers_are_fresh_per_call(tmp_path, monkeypatch):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    build_calls = []

    class FakeProxyHandler:
        def __init__(self, cfg=None):
            self.cfg = cfg

    counter = {"n": 0}

    class FakeOpener:
        pass

    def fake_build(*handlers):
        counter["n"] += 1
        build_calls.append(handlers[0].cfg)
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "ProxyHandler", FakeProxyHandler)
    monkeypatch.setattr(urllib.request, "build_opener", fake_build)
    first = pm.build_search_opener("off")
    second = pm.build_search_opener("off")
    assert first is not second
    assert build_calls == [{}, {}]
    assert counter["n"] == 2


def test_missing_envfile_raises_proxy_mode_error_in_transport(
        tmp_path, monkeypatch):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    with pytest.raises(pm.ProxyModeError) as err:
        pm.build_search_opener("on")
    assert err.value.code == "MISSING_ENVFILE"


def test_open_search_loopback_never_builds_a_proxy_opener(
        tmp_path, monkeypatch):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    hits = []

    def fake_urlopen(req, timeout=None):
        hits.append((req.full_url, timeout))
        return _FakeHTTPResponse(b"local")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def must_not_run(*args, **kwargs):
        raise AssertionError("loopback lane must not build a proxy opener")

    monkeypatch.setattr(pm, "build_search_opener", must_not_run)
    resp = pm.open_search("http://127.0.0.1:8888/search?q=x&q=off",
                          timeout=3, mode="on")
    assert resp.read() == b"local"
    assert hits == [("http://127.0.0.1:8888/search?q=x&q=off", 3)]


def test_open_search_external_uses_the_mode_opener(tmp_path, monkeypatch):
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    set_env_file(monkeypatch, path)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    calls = []
    _install_openers(monkeypatch, calls)
    resp = pm.open_search("https://html.example.org/x", timeout=7,
                          mode="on",
                          headers={"User-Agent": "testsuite"})
    assert resp.read() == b"fetched"
    assert calls == [("opener.open", "https://html.example.org/x", 7,
                      {"http": val, "https": val})]


# ---- ladder notes + guard -------------------------------------------------

def test_ladder_notes_off_on_and_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    assert pm.ladder_note(pm.prepared("off")) == "proxy mode off: direct"
    path = tmp_path / "proxy.env"
    write_fixture_env(path)
    set_env_file(monkeypatch, path)
    assert pm.ladder_note(pm.prepared("on")) == (
        "search proxy: on (external search lanes ride the proxy)")
    set_env_file(monkeypatch, tmp_path / "absent.env")
    note = pm.ladder_note(pm.prepared("on"))
    assert note == ("search proxy: on requested but the env file is "
                    "missing (MISSING_ENVFILE, "
                    "~/.config/greysearch/proxy.env): external search "
                    "lanes held back")
    path = tmp_path / "empty.env"
    path.write_text("", encoding="utf-8")
    set_env_file(monkeypatch, path)
    note = pm.ladder_note(pm.prepared("on"))
    assert "(NO_PROXY_VALUES)" in note and "held back" in note
    assert pm.ladder_note(None) == ("search proxy: mode not resolved "
                                    "(searches run direct)")


def test_ladder_notes_and_errors_never_carry_credential_values(
        tmp_path, monkeypatch):
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path, "http://testsuite:SECRETWORD@127.0.0.6:1")
    set_env_file(monkeypatch, path)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    surfaces = [
        pm.ladder_note(pm.prepared("on")),
        pm.ladder_note(pm.prepared("off")),
    ]
    bad = pm.prepared("on", )
    assert all("SECRETWORD" not in s for s in surfaces)
    assert "SECRETWORD" not in str(pm.ladder_note(bad))
    assert val.startswith("http://testsuite:")
    with pytest.raises(pm.ProxyModeError):
        pm.build_search_opener({"ok": False, "mode": "on",
                                "error": "MYSTERY_CODE", "proxy": None})
    assert "SECRETWORD" not in str(pm.ladder_note({"ok": False, "mode": "",
                                                  "error": "MISSING"}))


def test_scrape_guard_exact_string():
    assert pm.scrape_guard_note() == "scrape direct (no proxy)"


def test_is_loopback_url():
    assert pm.is_loopback_url("http://127.0.0.1:8888/search?q=x") is True
    assert pm.is_loopback_url("http://localhost:8888/x") is True
    assert pm.is_loopback_url("http://user:pw@127.0.0.1:8888/x") is True
    assert pm.is_loopback_url("http://box.localhost:1/x") is True
    assert pm.is_loopback_url("https://searx.space/data") is False
    assert pm.is_loopback_url("https://html.duckduckgo.com/x") is False


def test_off_mode_explicitly_bypasses_ambient_proxy(tmp_path, monkeypatch):
    calls = []
    _install_openers(monkeypatch, calls)
    res = {"ok": True, "mode": "off", "proxy": None, "error": ""}
    opener = pm.build_search_opener(res)
    assert opener.handler.cfg == {}
    assert calls == []
