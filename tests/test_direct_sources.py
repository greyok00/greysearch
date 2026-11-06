#!/usr/bin/env python3
"""Direct-source tests: the launcher's solver-era removal gate and the
direct-source tier.

Everything here is OFFLINE: no network sockets are opened and no browser is
launched. The launcher gate asserts the audit finding is retired (no seed
symbol, no extension loader, no retired-solver string anywhere in the
launcher's source text); the tier readers parse fixture XML/JSON/HTML
through an injected fetch; the politeness dict runs on an injected clock
and sleeper; the direct-transport guard is tested against the opener
directly.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import direct_sources as ds  # noqa: E402
import patchright_headless as ph  # noqa: E402
import research_integration as ri  # noqa: E402

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Greysearch Notes</title>
<link>https://feed.example.com</link>
<item>
  <title>Direct catalog tier readers</title>
  <link>https://feed.example.com/notes/direct-catalog</link>
  <description><![CDATA[<p>Readers for the tier.</p>]]></description>
</item>
<item>
  <title>Sitemap walk notes</title>
  <link>https://feed.example.com/notes/sitemap-walk</link>
  <description>plain summary</description>
  <content:encoded>fallback encoded body</content:encoded>
</item>
</channel>
</rss>"""

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Notes</title>
<entry>
  <title>Atom entry one</title>
  <link rel="self" href="https://atom.example.com/feed/one/self"/>
  <link rel="alternate" href="https://atom.example.com/notes/one"/>
  <summary>Atom summary one</summary>
</entry>
<entry>
  <title>Atom entry two</title>
  <link href="https://atom.example.com/notes/two"/>
  <content>Bare content text for two.</content>
</entry>
</feed>"""

SITEMAP_LOC = "<url><loc>{u}</loc></url>"


def sitemap_body(urls):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(SITEMAP_LOC.format(u=u) for u in urls) + "</urlset>")


SITEMAP_INDEX_FIXTURE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>https://sm.example.net/sitemap-posts.xml</loc></sitemap>"
    "<sitemap><loc>https://sm.example.net/sitemap-pages.xml</loc></sitemap>"
    "</sitemapindex>")

LATEST_JSON = {"topic_list": {"topics": [
    {"id": 11, "slug": "gray-ports", "title": "Catalog tier ports",
     "excerpt": "<i>hello</i> world topics"}]}}

TOPIC_JSON = {"title": "Catalog tier ports",
              "topic": {"id": 11, "slug": "gray-ports",
                        "title": "Catalog tier ports"},
              "post_stream": {"posts": [
                  {"post_number": 2, "blurb": "second post text"},
                  {"post_number": 3, "blurb": "<b>third</b> post text"}]}}

PAGE_HTML = ("<html><head><title>Named page evidence</title></head>"
             "<body><h1>Named page evidence</h1><p>Listing text here with "
             "greysearch direct catalog tier notes.</p>"
             "<nav>menu</nav></body></html>")


@pytest.fixture(autouse=True)
def _tier_env(monkeypatch):
    ds.politeness_reset()
    for name in ("GREYSEARCH_DIRECT_POLITENESS_S",
                 "GREYSEARCH_DIRECT_TIMEOUT_S", "GREYSEARCH_DIRECT_MAX_BYTES",
                 "GREYSEARCH_DIRECT_ROWS_MAX", "GREYSEARCH_SITEMAP_MAX",
                 "GREYSEARCH_CATALOG_MAX", "GREYSEARCH_SITE_CATALOG",
                 "GREYSEARCH_CAPTCHA_SOLVER",
                 "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                 "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    yield
    ds.politeness_reset()


class _Resp:
    """A urllib-shaped response double: .status plus bounded .read."""

    def __init__(self, body=b"", status=200):
        self.status = status
        self._body = body
        self.closed = False

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def close(self):
        self.closed = True


def _rf(routes, statuses=None):
    """An injected site_fetch-shaped reader: routes maps url -> body;
    statuses maps url -> status (default 200). Never touches the network."""
    calls = []

    def _f(url, timeout=None):
        url = str(url)
        calls.append(url)
        st = int((statuses or {}).get(url, 200))
        body = routes.get(url, "not found")
        return {"ok": st == 200, "status": st, "body": body, "url": url,
                "domain": "", "error": "" if st == 200 else f"http {st}",
                "waited_s": 0.0, "capped": False}

    _f.calls = calls
    return _f


def test_nopecha_fully_removed():
    """The audit finding: every seed symbol and the extension loader are
    gone from the module the launcher ships, and from its source text.
    The assets gate - the tracked assets/nopecha tree itself - lands with
    Task 9's repo-clean scan, which covers the tracked asset paths."""
    root = Path(ph.__file__).resolve().parent
    assert not hasattr(ph, "seed_nopecha_keys")
    assert not hasattr(ph, "captcha_solver_extension")
    assert not hasattr(ph, "read_nopecha_key")
    src = (root / "patchright_headless.py").read_text()
    assert "nopecha" not in src.lower()


def test_rss_fixture_parses():
    f = _rf({"https://feed.example.com/feed": RSS_FIXTURE})
    res = ds.rss_atom("https://feed.example.com/feed", fetch=f)
    assert res["ok"] is True and res["kind"] == "rss"
    assert [r["url"] for r in res["rows"]] == [
        "https://feed.example.com/notes/direct-catalog",
        "https://feed.example.com/notes/sitemap-walk"]
    assert res["rows"][0]["title"] == "Direct catalog tier readers"
    assert res["rows"][0]["summary"] == "Readers for the tier."
    assert res["rows"][1]["summary"] == "plain summary"
    assert f.calls == ["https://feed.example.com/feed"]


def test_rss_rows_max_knob_caps_entries(monkeypatch):
    monkeypatch.setenv("GREYSEARCH_DIRECT_ROWS_MAX", "1")
    f = _rf({"https://feed.example.com/feed": RSS_FIXTURE})
    res = ds.rss_atom("https://feed.example.com/feed", fetch=f)
    assert res["rows"] == [
        {"title": "Direct catalog tier readers",
         "url": "https://feed.example.com/notes/direct-catalog",
         "summary": "Readers for the tier."}]


def test_atom_fixture_parses():
    f = _rf({"https://atom.example.com/atom.xml": ATOM_FIXTURE})
    res = ds.rss_atom("https://atom.example.com/atom.xml", fetch=f)
    assert res["ok"] is True and res["kind"] == "atom"
    assert [r["url"] for r in res["rows"]] == [
        "https://atom.example.com/notes/one",
        "https://atom.example.com/notes/two"]
    assert res["rows"][0]["summary"] == "Atom summary one"
    assert res["rows"][1]["summary"] == "Bare content text for two."


def test_rss_bodies_that_are_not_feeds_are_honest_records():
    f = _rf({"https://nope.example.com/feed": "<html><body>nope</body></html>",
             "https://broken.example.com/rss": "<rss><broken"})
    res = ds.rss_atom("https://nope.example.com/feed", fetch=f)
    assert res["ok"] is False and res["rows"] == []
    assert res["error"] == "not a feed (html)"
    res2 = ds.rss_atom("https://broken.example.com/rss", fetch=f)
    assert res2["ok"] is False
    assert res2["error"].startswith("parse failed:")
    f404 = _rf({}, statuses={"https://gone.example.com/rss": 404})
    assert ds.rss_atom("https://gone.example.com/rss", fetch=f404)["error"] \
        == "http 404"


def test_sitemap_fixture_parses_with_caps():
    urls = [f"https://sm.example.net/post-{i}" for i in range(5)]
    f = _rf({"https://sm.example.net/sitemap.xml": sitemap_body(urls)})
    res = ds.sitemap_walk("https://sm.example.net/sitemap.xml", fetch=f,
                          cap=2)
    assert res["ok"] is True and res["kind"] == "sitemap"
    assert res["urls"] == urls[:2]
    assert res["capped"] is True
    assert [(r["title"], r["summary"]) for r in res["rows"]] == [("", "")] * 2
    res2 = ds.sitemap_walk("https://sm.example.net/sitemap.xml", fetch=f)
    assert res2["urls"] == urls
    assert res2["capped"] is False


def test_sitemap_index_one_level_only():
    posts = [f"https://sm.example.net/posts/{i}" for i in range(3)]
    f = _rf({
        "https://sm.example.net/sitemap.xml": SITEMAP_INDEX_FIXTURE,
        "https://sm.example.net/sitemap-posts.xml": sitemap_body(posts),
        "https://sm.example.net/sitemap-pages.xml": SITEMAP_INDEX_FIXTURE})
    res = ds.sitemap_walk("https://sm.example.net/sitemap.xml", fetch=f)
    assert res["kind"] == "sitemap-index"
    assert res["urls"] == posts
    assert res["skipped"] == ["https://sm.example.net/sitemap-pages.xml"]
    assert res["capped"] is False


def test_sitemap_index_child_failures_recorded():
    f = _rf({"https://sm.example.net/sitemap.xml": SITEMAP_INDEX_FIXTURE,
             "https://sm.example.net/sitemap-posts.xml":
                 sitemap_body(["https://sm.example.net/posts/one"])})
    res = ds.sitemap_walk("https://sm.example.net/sitemap.xml", fetch=f)
    assert res["urls"] == ["https://sm.example.net/posts/one"]
    assert "https://sm.example.net/sitemap-pages.xml" in res["skipped"]
    assert res["ok"] is True


def test_discourse_latest_json_rows():
    f = _rf({"https://forum.example.org/latest.json": json.dumps(LATEST_JSON)})
    res = ds.forum_json("https://forum.example.org", fetch=f)
    assert res["ok"] is True and res["kind"] == "forum"
    assert res["rows"] == [{"title": "Catalog tier ports",
                            "url": "https://forum.example.org/t/gray-ports/11",
                            "summary": "hello world topics"}]
    assert f.calls == ["https://forum.example.org/latest.json"]


def test_forum_pattern_endpoints():
    f = _rf({"https://forum.sub.example.net/latest.json":
             json.dumps(LATEST_JSON),
             "https://forum.example.org/custom.json":
             json.dumps(LATEST_JSON),
             "https://forum.example.org/t/gray-ports/11.json":
             json.dumps(TOPIC_JSON)})
    res = ds.forum_json("forum.sub.example.net", fetch=f)
    assert res["ok"] is True
    assert res["url"] == "https://forum.sub.example.net/latest.json"
    res2 = ds.forum_json("https://forum.example.org/custom.json", fetch=f)
    assert res2["ok"] is True
    assert res2["url"] == "https://forum.example.org/custom.json"
    res3 = ds.forum_json("https://forum.example.org/t/gray-ports/11", fetch=f)
    assert res3["ok"] is False and res3["error"] == "no topics"
    assert res3["url"] == "https://forum.example.org/t/gray-ports/11.json"


def test_discourse_topic_json_rows():
    f = _rf({"https://forum.example.org/t/gray-ports/11.json":
             json.dumps(TOPIC_JSON)})
    res = ds.forum_topic_json("https://forum.example.org", "gray-ports/11",
                              fetch=f)
    assert res["ok"] is True and res["kind"] == "forum"
    assert [r["url"] for r in res["rows"]] == [
        "https://forum.example.org/t/gray-ports/11/2",
        "https://forum.example.org/t/gray-ports/11/3"]
    assert res["rows"][0]["summary"] == "second post text"
    assert res["rows"][1]["summary"] == "third post text"
    assert res["rows"][0]["title"] == "Catalog tier ports"


def test_page_fetch_rows():
    f = _rf({"https://page.example.com/list": PAGE_HTML})
    res = ds.page_fetch("https://page.example.com/list", fetch=f)
    assert res["ok"] is True and res["kind"] == "page"
    assert res["rows"][0]["title"] == "Named page evidence"
    assert "greysearch direct catalog tier notes" in \
        res["rows"][0]["summary"]
    assert res["rows"][0]["summary"].find("<nav>") < 0
    f2 = _rf({"https://page.example.com/list2": ""})
    assert ds.page_fetch("https://page.example.com/list2", fetch=f2)["ok"] \
        is False


def test_site_fetch_politeness_spacing():
    clock_state = {"t": 1000.0}
    sleeps = []

    def clock():
        return clock_state["t"]

    def sleeper(s):
        sleeps.append(round(s, 2))
        clock_state["t"] += s

    def open_call(url, timeout):
        return _Resp(b"x")

    r1 = ds.site_fetch("https://pol.example.com/a", open_call=open_call,
                       sleeper=sleeper, clock=clock)
    assert r1["ok"] is True and r1["waited_s"] == 0.0 and sleeps == []
    clock_state["t"] = 1001.0
    r2 = ds.site_fetch("https://pol.example.com/b", open_call=open_call,
                       sleeper=sleeper, clock=clock)
    assert r2["waited_s"] == 2.0 and sleeps == [2.0]
    clock_state["t"] = 1020.0
    r3 = ds.site_fetch("https://pol.example.com/c", open_call=open_call,
                       sleeper=sleeper, clock=clock)
    assert r3["waited_s"] == 0.0 and r3["capped"] is False
    r4 = ds.site_fetch("https://other.example.com/a", open_call=open_call,
                       sleeper=sleeper, clock=clock)
    assert r4["waited_s"] == 0.0
    assert set(ds.politeness_at()) == {"pol.example.com",
                                       "other.example.com"}
    ds.politeness_reset()
    assert ds.politeness_at() == {}


def test_site_fetch_politeness_knob_read_at_call_time(monkeypatch):
    monkeypatch.setenv("GREYSEARCH_DIRECT_POLITENESS_S", "5.0")
    assert ds.politeness_s() == 5.0
    clock_state = {"t": 100.0}

    def clock():
        return clock_state["t"]

    def sleeper(s):
        clock_state["t"] += s

    def open_call(url, timeout):
        return _Resp(b"x")

    ds.site_fetch("https://knob.example.com/a", open_call=open_call,
                  sleeper=sleeper, clock=clock)
    clock_state["t"] = 102.0
    r = ds.site_fetch("https://knob.example.com/b", open_call=open_call,
                      sleeper=sleeper, clock=clock)
    assert r["waited_s"] == 3.0


def test_site_fetch_body_cap_and_errors():
    def open_call(url, timeout):
        return _Resp(b"x" * 40)

    r = ds.site_fetch("https://cap.example.com/a", max_bytes=5,
                      open_call=open_call, clock=lambda: 0.0,
                      sleeper=lambda s: None)
    assert r["capped"] is True and r["body"] == "xxxxx" and r["ok"] is True

    def bad_open(url, timeout):
        raise urllib.error.URLError("refused")

    r2 = ds.site_fetch("https://bad.example.com/a", open_call=bad_open,
                       clock=lambda: 0.0, sleeper=lambda s: None)
    assert r2["ok"] is False and r2["status"] == 0
    assert r2["error"].startswith("URLError")


def test_discovery_bounded_first_hit_wins():
    f = _rf({"https://disc.example.com/feed": RSS_FIXTURE})
    res = ds.discover("disc.example.com", fetch=f)
    assert res["ok"] is True
    assert res["url"] == "https://disc.example.com/feed"
    assert res["kind"] == "rss" and len(res["rows"]) == 2
    assert [t["url"] for t in res["tried"]] == [
        "https://disc.example.com/feed"]
    assert res["tried"][0]["ok"] is True
    assert f.calls == ["https://disc.example.com/feed"]


def test_discovery_tries_all_paths_when_everything_misses():
    f = _rf({})
    res = ds.discover("quiet.example.com", fetch=f)
    assert res["ok"] is False and res["rows"] == []
    assert res["error"] == "no feed path returned entries"
    assert [t["url"] for t in res["tried"]] == [
        "https://quiet.example.com" + p for p in ds.FEED_PATHS]
    assert all(t["ok"] is False and t["n"] == 0 for t in res["tried"])
    assert len(f.calls) == len(ds.FEED_PATHS)


def test_discovery_sitemap_path_reader():
    f = _rf({"https://sm.discover.example.com/sitemap.xml":
             sitemap_body(["https://sm.discover.example.com/a"])})
    res = ds.discover("sm.discover.example.com", fetch=f)
    assert res["ok"] is True and res["kind"] == "sitemap"
    assert res["rows"][0]["url"] == "https://sm.discover.example.com/a"
    assert [t["kind"] for t in res["tried"]] == ["", "", "", "sitemap"]
    assert [t["ok"] for t in res["tried"]] == [False, False, False, True]


def test_no_proxy_guard_on_every_direct_reader(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://testsuite:pw@127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://testsuite:pw@127.0.0.1:9")
    monkeypatch.setenv("GREYSEARCH_DIRECT_POLITENESS_S", "0")
    seen = []
    routes = {
        "https://proxytest.feed.example.com/feed": RSS_FIXTURE,
        "https://proxytest.sm.example.com/sitemap.xml":
            sitemap_body(["https://sm.example.net/a"]),
        "https://proxytest.forum.example.com/latest.json":
            json.dumps(LATEST_JSON),
        "https://proxytest.page.example.com/p": PAGE_HTML,
    }

    def fake_open(url, timeout=0.0):
        u = str(url)
        seen.append(u)
        return _Resp(routes.get(u, "not found").encode("utf-8"))

    monkeypatch.setattr(ds, "_direct_open", fake_open)
    ds.rss_atom("https://proxytest.feed.example.com/feed")
    ds.sitemap_walk("https://proxytest.sm.example.com/sitemap.xml")
    ds.forum_json("https://proxytest.forum.example.com")
    ds.page_fetch("https://proxytest.page.example.com/p")
    disc = ds.discover("proxytest.disc.example.com")
    assert disc["ok"] is False
    assert len(seen) == 4 + len(ds.FEED_PATHS)
    assert all(u.startswith("https://proxytest.") for u in seen)
    assert "https://proxytest.disc.example.com" + ds.FEED_PATHS[0] in seen


def test_direct_opener_never_rides_env_proxy(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://testsuite:pw@127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://testsuite:pw@127.0.0.1:9")
    monkeypatch.setenv("all_proxy", "http://testsuite:pw@127.0.0.1:9")
    built = []

    real_build = urllib.request.build_opener

    def spy_build(*handlers):
        built.append(list(handlers))
        return real_build(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", spy_build)
    opener = ds._direct_opener()
    assert len(built) == 1
    passed = built[0]
    phandlers = [h for h in passed
                 if isinstance(h, urllib.request.ProxyHandler)]
    assert len(phandlers) == 1 and phandlers[0].proxies == {}
    in_chain = [h for h in opener.handlers
                if isinstance(h, urllib.request.ProxyHandler)]
    assert in_chain == []
    assert ds.domain_of("HTTPS://Feed.Example.COM/feed") == \
        "feed.example.com"
    assert ds.domain_of("not a url") == ""


def test_read_catalog_rows_attribution_and_lines():
    f = _rf({
        "https://feed.example.com/feed": RSS_FIXTURE,
        "https://sm.example.net/sitemap.xml": sitemap_body(
            ["https://sm.example.net/greysearch-notes-a",
             "https://sm.example.net/greysearch-notes-b"]),
        "https://forum.example.org/latest.json": json.dumps(LATEST_JSON)})
    catalog = [
        {"site": "feed.example.com", "kind": "rss",
         "url": "https://feed.example.com/feed"},
        {"site": "https://sm.example.net", "kind": "sitemap",
         "url": "https://sm.example.net/sitemap.xml"},
        {"site": "forum.example.org", "kind": "forum",
         "url": "https://forum.example.org/latest.json"}]
    res = ds.read_catalog(catalog, fetch=f)
    assert res["ok"] is True
    assert res["entries_tried"] == 3 and res["entries_ok"] == 3
    assert res["lines"] == [
        "direct catalog: feed.example.com rss +2",
        "direct catalog: sm.example.net sitemap +2",
        "direct catalog: forum.example.org forum +1"]
    rows = res["rows"]
    assert len(rows) == 2 + 2 + 1
    assert all(r["tier"] == "direct-catalog" for r in rows)
    assert {r["engine"] for r in rows} == {
        "feed.example.com", "sm.example.net", "forum.example.org"}
    rss_rows = [r for r in rows if r["engine"] == "feed.example.com"]
    assert rss_rows[0]["url"] == \
        "https://feed.example.com/notes/direct-catalog"
    assert rss_rows[0]["domain"] == "feed.example.com"
    assert rss_rows[0]["snippet"] == "Readers for the tier."
    sm_rows = [r for r in rows if r["engine"] == "sm.example.net"]
    assert sm_rows[0]["url"] == \
        "https://sm.example.net/greysearch-notes-a"


def test_read_catalog_dedupes_within_the_lane():
    f = _rf({"https://feed.example.com/feed": RSS_FIXTURE})
    catalog = [
        {"site": "feed.example.com", "kind": "rss",
         "url": "https://feed.example.com/feed"},
        {"site": "mirror.example.com", "kind": "rss",
         "url": "https://feed.example.com/feed"}]
    res = ds.read_catalog(catalog, fetch=f)
    assert len(res["rows"]) == 2
    assert res["entries_ok"] == 1
    assert res["lines"][1] == \
        "direct catalog: mirror.example.com rss +0 (no new urls)"
    assert res["skipped"][-1]["reason"] == "no new urls"


def test_read_catalog_failures_honest_never_raises():
    f = _rf({"https://garbage.example.com/feed": "<html>hi</html>"})
    catalog = [
        {"site": "wiki.example.com", "kind": "wiki"},
        {"site": "", "kind": "rss"},
        {"site": "dead.example.com", "kind": "rss"},
        {"site": "garbage.example.com", "kind": "rss",
         "url": "https://garbage.example.com/feed"}]
    res = ds.read_catalog(catalog, fetch=f)
    assert res["ok"] is False and res["rows"] == []
    assert res["lines"][0] == \
        "direct catalog: wiki.example.com wiki +0 (unknown kind)"
    assert res["lines"][1] == "direct catalog: ? rss +0 (no site)"
    assert res["lines"][2] == \
        ("direct catalog: dead.example.com rss +0 "
         "(no feed path returned entries)")
    assert res["lines"][3] == \
        "direct catalog: garbage.example.com rss +0 (not a feed (html))"
    assert res["entries_tried"] == 2 and res["entries_ok"] == 0

    class _Boom(Exception):
        pass

    def _boom_fetch(url, timeout=None):
        raise _Boom("broken wire")

    res2 = ds.read_catalog([{"site": "boom.example.com", "kind": "rss"}],
                           fetch=_boom_fetch)
    assert res2["ok"] is False and res2["rows"] == []
    assert res2["lines"] == [
        "direct catalog: boom.example.com rss +0 (_Boom: broken wire)"]


def test_read_catalog_budget_reached_stops_the_walk():
    f = _rf({"https://feed.example.com/feed": RSS_FIXTURE})
    catalog = [{"site": "feed.example.com", "kind": "rss",
                "url": "https://feed.example.com/feed"},
               {"site": "late.example.com", "kind": "rss"}]
    res = ds.read_catalog(catalog, fetch=f, deadline_ts=100.0,
                          clock=lambda: 10.0)
    assert res["entries_tried"] == 2 and res["entries_ok"] == 1
    res2 = ds.read_catalog(catalog, fetch=f, deadline_ts=9.0,
                           clock=lambda: 10.0)
    assert res2["entries_tried"] == 0
    assert res2["rows"] == []
    assert res2["lines"] == [
        "direct catalog: feed.example.com rss +0 (budget reached)"]
    assert res2["skipped"][0]["reason"] == "budget reached"


def test_read_catalog_budget_reached_mid_walk():
    f = _rf({"https://feed.example.com/feed": RSS_FIXTURE})
    catalog = [{"site": "feed.example.com", "kind": "rss",
                "url": "https://feed.example.com/feed"},
               {"site": "late.example.com", "kind": "rss"}]
    now = {"t": 0.0}

    def clock():
        old = now["t"]
        now["t"] += 1.0
        return old

    res = ds.read_catalog(catalog, fetch=f, deadline_ts=1.0, clock=clock)
    assert res["entries_tried"] == 1
    assert res["lines"] == [
        "direct catalog: feed.example.com rss +2",
        "direct catalog: late.example.com rss +0 (budget reached)"]
    assert any(s.get("reason") == "budget reached" for s in res["skipped"])


def test_read_catalog_entry_cap_records_unattempted():
    f = _rf({})
    catalog = [{"site": f"c{i}.example.com", "kind": "rss"}
               for i in range(5)]
    res = ds.read_catalog(catalog, fetch=f, entry_cap=2)
    assert res["entries_tried"] == 2
    assert res["lines"][-1].startswith(
        "direct catalog: lane cap 2 reached (3 entries unattempted)")
    assert any(s.get("unattempted") == 3 for s in res["skipped"])


def test_load_catalog_env(tmp_path, monkeypatch):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps([{"site": "a.example.com", "kind": "rss"}]))
    monkeypatch.setenv("GREYSEARCH_SITE_CATALOG", str(p))
    assert ds.load_catalog_env() == [{"site": "a.example.com", "kind": "rss"}]
    p2 = tmp_path / "wrapped.json"
    p2.write_text(json.dumps({"catalog": [
        {"site": "b.example.com", "kind": "forum"}]}))
    monkeypatch.setenv("GREYSEARCH_SITE_CATALOG", str(p2))
    assert ds.load_catalog_env() == [
        {"site": "b.example.com", "kind": "forum"}]
    p3 = tmp_path / "junk.json"
    p3.write_text("{not json")
    monkeypatch.setenv("GREYSEARCH_SITE_CATALOG", str(p3))
    assert ds.load_catalog_env() == []
    monkeypatch.setenv("GREYSEARCH_SITE_CATALOG",
                       str(tmp_path / "absent.json"))
    assert ds.load_catalog_env() == []
    monkeypatch.delenv("GREYSEARCH_SITE_CATALOG")
    assert ds.load_catalog_env() == []


def test_coverage_manifest_shape():
    import searxng_integrated as si
    m = ds.coverage_manifest()
    assert m["engine"] == "greysearch"
    assert sorted(m["tiers"]) == ["A", "B", "C", "D"]
    assert m["tiers"]["C"]["module"] == "direct_sources"
    assert m["tiers"]["C"]["never_proxied"] is True
    assert "rss" in m["tiers"]["C"]["reach"]
    assert "wayback machine rescue" in m["tiers"]["D"]["reach"]
    assert m["instance_pool_source"]["primary"] == si.SEARX_SPACE_DATA_URL
    assert m["instance_pool_source"]["primary"] == \
        "https://searx.space/data/instances.json"
    assert any("hbubli" in str(u) for u in
               m["instance_pool_source"]["fallbacks"])
    contract = m["catalog_contract"]
    assert contract["env"] == "GREYSEARCH_SITE_CATALOG"
    assert contract["tier"] == "direct-catalog"
    assert contract["digest_line"] == "direct catalog: <site> <kind> +N"
    assert contract["failure_line"] == \
        "direct catalog: <site> <kind> +0 (<reason>)"
    assert contract["reader"] == "direct_sources.read_catalog"
    assert contract["param"] == "site_catalog (research_deep run kwarg)"
    assert ds.CATALOG_KINDS == ("rss", "sitemap", "forum", "page")
    assert ds.FEED_PATHS == ("/feed", "/rss", "/atom.xml", "/sitemap.xml",
                             "/feed.xml")


def test_coverage_manifest_reflects_knobs(monkeypatch):
    monkeypatch.setenv("GREYSEARCH_DIRECT_ROWS_MAX", "7")
    monkeypatch.setenv("GREYSEARCH_SITEMAP_MAX", "21")
    monkeypatch.setenv("GREYSEARCH_CATALOG_MAX", "3")
    m = ds.coverage_manifest()
    assert m["direct_knobs"]["GREYSEARCH_DIRECT_ROWS_MAX"] == 7
    assert m["direct_knobs"]["GREYSEARCH_SITEMAP_MAX"] == 21
    assert m["catalog_contract"]["entries_per_run"] == 3


def test_module_defaults_are_the_documented_ones():
    assert ds.CATALOG_TIER == "direct-catalog"
    assert ds.POLITENESS_DEFAULT_S == 3.0
    assert ds.TIMEOUT_DEFAULT_S == 12.0
    assert ds.MAX_BYTES_DEFAULT == 262144
    assert ds.ROWS_MAX_DEFAULT == 40
    assert ds.SITEMAP_MAX_DEFAULT == 500
    assert ds.CATALOG_MAX_DEFAULT == 12
    assert ds.USER_AGENT.find("polite") > 0
    assert ds.CATALOG_ENV == "GREYSEARCH_SITE_CATALOG"


DIGEST_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Direct Tier Digest</title>
<item>
  <title>greysearch direct catalog tier readers digest</title>
  <link>https://digest.example.com/posts/direct-catalog-readers</link>
  <description>the direct tier reads named sites</description>
</item>
<item>
  <title>catalog readers digest again</title>
  <link>https://digest.example.com/posts/catalog-readers-2</link>
  <description>second digest note</description>
</item>
</channel>
</rss>"""

UNRELATED_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Unrelated</title>
<item>
  <title>Completely different pottery content</title>
  <link>https://unrelated.example.com/entries/pottery-garden</link>
  <description>clay and wheel notes only</description>
</item>
</channel>
</rss>"""

B2_ROUTES = {
    "https://feed.example.com/feed": RSS_FIXTURE,
    "https://digest.example.com/rss": DIGEST_FIXTURE,
    "https://unrelated.example.com/feed": UNRELATED_FIXTURE,
}

B2_CATALOG = [
    {"site": "feed.example.com", "kind": "rss",
     "url": "https://feed.example.com/feed"},
    {"site": "digest.example.com", "kind": "rss",
     "url": "https://digest.example.com/rss"},
    {"site": "unrelated.example.com", "kind": "rss",
     "url": "https://unrelated.example.com/feed"},
]

B2_QUESTION = "greysearch direct catalog tier readers"


def _catalog_open(url, timeout=0.0):
    u = str(url)
    return _Resp(B2_ROUTES.get(u, "not found").encode("utf-8"))


def _deep_bridge():
    """A research_deep bridge cut off from every real network lane: the
    search rows come from a fake manager lane, every scrape fails
    honestly, the instance preflight is silent."""
    bridge = ri.ResearchScraperBridge.__new__(ri.ResearchScraperBridge)
    bridge._scrape_slot = None
    bridge._active_scrapes = set()
    bridge.searxng_manager = type(
        "_M", (), {"get_working_engines": (lambda self: ["duckduckgo"])})()
    rows = [
        {"title": "greysearch direct tier docs",
         "url": "https://docs.greysearch.dev/tier/direct",
         "content": "how the direct tier reads named sites",
         "domain": "docs.greysearch.dev", "tier": "secondary",
         "engine": "duckduckgo"},
        {"title": "greysearch catalog lane notes",
         "url": "https://blog.greysearch.dev/catalog-lane",
         "content": "the catalog lane drives readers",
         "domain": "blog.greysearch.dev", "tier": "secondary",
         "engine": "duckduckgo"},
        {"title": "Dinner recipes for autumn nights",
         "url": "https://cooking.example.com/recipes/autumn",
         "content": "stews and roasts",
         "domain": "cooking.example.com", "tier": "secondary",
         "engine": "duckduckgo"}]

    def _search(q, **kw):
        return {"ok": True, "results": [dict(r) for r in rows],
                "ladder": ["proxy mode off: direct",
                           "local: 3 results (full engine set)"],
                "engines_tried": ["duckduckgo"], "search_proxy": "off",
                "last_error": None}

    def _scrape(url, max_chars=0, slot="", mode=None):
        return {"ok": False, "url": url, "error": "stub offline"}

    bridge.research_search = _search
    bridge.research_scrape = _scrape
    bridge._instance_preflight = lambda proxy_res: {"alive": [],
                                                    "line": "", "tried": 0}
    bridge.excluded_domains = set()
    bridge.sites_data = {"categories": {}}
    return bridge


def _lane(run):
    return [s for s in run["searches"]
            if s.get("category") == "site-catalog"]


def test_catalog_lane_wires_rows_and_records(monkeypatch):
    monkeypatch.setattr(ds, "_direct_open", _catalog_open)
    monkeypatch.setenv("GREYSEARCH_DIRECT_POLITENESS_S", "0")
    bridge = _deep_bridge()
    seen = []
    run = bridge.research_deep(
        B2_QUESTION, categories=["reference"], max_pages=2,
        enable_semantic_judge=False,
        progress_cb=lambda stage, data: seen.append((stage, data)),
        site_catalog=B2_CATALOG)
    lanes = _lane(run)
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["ok"] is True and lane["n"] == 5
    assert lane["ladder"] == [
        "direct catalog: feed.example.com rss +2",
        "direct catalog: digest.example.com rss +2",
        "direct catalog: unrelated.example.com rss +1"]
    assert lane["query"] == "(direct-source tier: named-site catalog)"
    done = [d for s, d in seen if s == "search_done"]
    assert len(done) == 1
    cat_rows = [r for r in done[0]["results"]
                if r.get("tier") == "direct-catalog"]
    assert {r["engine"] for r in cat_rows} == {
        "feed.example.com", "digest.example.com"}
    assert any(r["url"] == "https://feed.example.com/notes/direct-catalog"
               for r in cat_rows)
    assert any(r["url"] == "https://digest.example.com/posts/catalog-readers-2"
               for r in cat_rows)
    assert "direct catalog: digest.example.com rss +2" in \
        run["search_ladder"]
    assert "direct catalog" in ri._lane_keys_from_ladder(run["search_ladder"])
    assert any(r.get("engine") == "duckduckgo"
               for r in done[0]["results"])


def test_gate_exemption_scoped_to_the_catalog_tier(monkeypatch):
    monkeypatch.setattr(ds, "_direct_open", _catalog_open)
    monkeypatch.setenv("GREYSEARCH_DIRECT_POLITENESS_S", "0")
    bridge = _deep_bridge()
    run = bridge.research_deep(
        B2_QUESTION, categories=["reference"], max_pages=2,
        enable_semantic_judge=False, site_catalog=B2_CATALOG)
    gate = [s for s in run["searches"]
            if s.get("category") == "relevance-gate"][0]
    pre = [s for s in run["searches"] if s.get("category") == "prefilter"][0]
    assert gate["filtered_out"] == 1
    assert gate["n"] == 7
    assert pre["filtered_out"] == 2
    rejects = [e for e in run["skipped_pages"]
               if e.get("decision") == "reject"]
    urls = [e["url"] for e in rejects]
    assert "https://unrelated.example.com/entries/pottery-garden" in urls
    assert "https://feed.example.com/notes/sitemap-walk" in urls


def test_no_catalog_lane_without_a_catalog(monkeypatch):
    bridge = _deep_bridge()
    seen = []
    run = bridge.research_deep(
        B2_QUESTION, categories=["reference"], max_pages=1,
        enable_semantic_judge=False,
        progress_cb=lambda stage, data: seen.append((stage, data)))
    assert _lane(run) == []
    assert "direct catalog" not in \
        ri._lane_keys_from_ladder(run["search_ladder"])
    assert any(s == "done" for s, _d in seen)


def test_catalog_lane_reads_the_env_catalog(tmp_path, monkeypatch):
    p = tmp_path / "site-catalog.json"
    p.write_text(json.dumps([
        {"site": "feed.example.com", "kind": "rss",
         "url": "https://feed.example.com/feed"}]))
    monkeypatch.setenv("GREYSEARCH_SITE_CATALOG", str(p))
    monkeypatch.setattr(ds, "_direct_open", _catalog_open)
    monkeypatch.setenv("GREYSEARCH_DIRECT_POLITENESS_S", "0")
    run = _deep_bridge().research_deep(
        B2_QUESTION, categories=["reference"], max_pages=1,
        enable_semantic_judge=False)
    lanes = _lane(run)
    assert len(lanes) == 1 and lanes[0]["ok"] is True and lanes[0]["n"] == 2
    assert "direct catalog: feed.example.com rss +2" in run["search_ladder"]


def test_catalog_lane_budget_reached_recorded(monkeypatch):
    spy = []
    real_read = ds.read_catalog

    def _spy(catalog, **kw):
        spy.append(kw.get("deadline_ts"))
        return real_read(catalog, **kw)

    monkeypatch.setattr(ds, "read_catalog", _spy)
    monkeypatch.setattr(ds, "_direct_open", _catalog_open)
    # A negative budget puts the deadline in the PAST, so the lane hits the
    # budget check deterministically. The old +0.001 s seed raced the wall
    # clock: on a fast run the first check fired before 1 ms elapsed and the
    # lane fetched anyway (flaky ok=True vs ok=False across runs).
    run = _deep_bridge().research_deep(
        B2_QUESTION, categories=["reference"], max_pages=1,
        enable_semantic_judge=False, max_total_seconds=-0.001,
        site_catalog=B2_CATALOG)
    lanes = _lane(run)
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["ok"] is False and lane["n"] == 0
    assert lane["ladder"] and all("+0 (budget reached)" in x
                                  for x in lane["ladder"])
    assert spy and all(t is not None for t in spy)


def test_catalog_lane_failure_recorded_honestly(monkeypatch):
    def _raise(catalog, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(ds, "read_catalog", _raise)
    run = _deep_bridge().research_deep(
        B2_QUESTION, categories=["reference"], max_pages=1,
        enable_semantic_judge=False, site_catalog=B2_CATALOG)
    lanes = _lane(run)
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["ok"] is False and lane["n"] == 0
    assert lane["ladder"] == ["direct catalog: lane failed "
                              "(RuntimeError: boom)"]
    assert run["run_report"]["candidate_count"] >= 1


def test_module_wrapper_threads_site_catalog(monkeypatch):
    monkeypatch.setattr(ds, "_direct_open", _catalog_open)
    monkeypatch.setenv("GREYSEARCH_DIRECT_POLITENESS_S", "0")
    bridge = _deep_bridge()
    monkeypatch.setattr(ri, "get_bridge", lambda: bridge)
    run = ri.research_deep(
        B2_QUESTION, categories=["reference"], max_pages=1,
        enable_semantic_judge=False, site_catalog=B2_CATALOG)
    assert any(s.get("category") == "site-catalog"
               and s.get("ok") is True for s in run["searches"])
    assert "direct catalog" in ri._lane_keys_from_ladder(run["search_ladder"])
