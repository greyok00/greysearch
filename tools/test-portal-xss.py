#!/usr/bin/env python3
"""Regression fixture for the portal frontend (findings GS-01 + GS-03).

Serves portal/index.html and its static assets from this checkout, mocks
the backend API with deliberately hostile payloads — script-bearing source
URLs and titles, image metadata, guide-preview text — and asserts in a real
headless Chromium that:

  1. no payload executes as markup (a fired payload rewrites document.title,
     which is visible across script worlds; the title must never change),
  2. payloads survive as TEXT where they belong (textContent sinks),
  3. non-http(s) source URLs render linkless instead of clickable,
  4. refine/build requests that fail (HTTP 500) re-enable their controls,
  5. the sanctioned liteMd() renderer still neutralizes raw tags.

The page is driven like a real user — form submit, mocked SSE stream,
button clicks — because patchright evaluates in an isolated script world
where page globals are invisible; every assertion reads the shared DOM.

Run from a checkout with dependencies installed:

    .venv/bin/python tools/test-portal-xss.py

Needs the patchright Chromium build (python -m patchright install chromium).
Exits 0 on pass, 1 on failure, 77 if the browser is unavailable.
"""
import functools
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---- hostile payloads (each would execute script if it reached markup) ----
P_TAG = "<img src=x onerror=\"document.title='PWNED-TAG'\">"
P_QUOTE = '"><svg onload="document.title=\'PWNED-QUOTE\'">'
P_JSURL = "javascript:document.title='PWNED-URL';1"
P_MOUSE = 'https://x.test/path"onmouseover="document.title=\'PWNED-HOVER\'"'

RUN_RESULT = {
    "status": "done",
    "result": {
        "master_guide": {
            "preview": "**Executive Brief:** the brief\nspans two lines. "
                       + P_TAG + " and more prose.\n\nRest of the guide.",
            "path": "/somewhere/guides/2026-01-01/master-guide.md",
        },
        "sources": [
            P_JSURL,            # refused scheme -> rendered linkless
            P_MOUSE,            # quote-breaker -> href stays inert
            "https://safe.test/page",
        ],
        "image_candidates": [
            {"file": 'guides/t/bad"file.png', "title": P_TAG, "license": P_QUOTE},
            {"file": "guides/t/ok.png", "title": "clean title", "license": "CC0"},
        ],
        "pages_fetched": [
            {"url": P_MOUSE, "title": P_TAG,
             "text": "A long paragraph that survives the content gate "
                     + P_TAG + " because it does not start with a tag."},
            {"url": P_JSURL, "title": P_QUOTE,
             "text": "Second paragraph also longer than the gate requires "
                     + P_QUOTE + " so it appears in the live text view."},
        ],
    },
}


def fulfill_json(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json",
                  body=json.dumps(payload))


def run():
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):        # silence request noise
            pass

        def translate_path(self, path):
            for prefix, sub in (("/kit/", "assets/kit-source/"),
                                ("/assets/", "assets/")):
                if path.startswith(prefix):
                    return str(ROOT / sub / path[len(prefix):].split("?")[0])
            return super().translate_path(path)

    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 functools.partial(Handler, directory=str(ROOT)))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/portal/index.html" % port

    state = {"research_status": 500, "refine_status": 500, "build_status": 500}

    from patchright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                print("SKIP: cannot launch Chromium (%s)" % e)
                sys.exit(77)
            page = browser.new_page()

            def refine_route(route):
                if state["refine_status"] == 200:
                    fulfill_json(route, {
                        "ok": True,
                        "text": "Refined brief with " + P_TAG + " inside.",
                        "image_suggestions": [
                            {"file": "guides/t/sug.png", "title": "clean sugg",
                             "license": "", "para": 0},
                        ],
                    })
                else:
                    route.fulfill(status=500, body="boom")

            def build_route(route):
                if state["build_status"] == 200:
                    fulfill_json(route, {
                        "ok": True,
                        "site": "/x/guides/d/sites/site-x",
                        "pdf": "/x/guides/d/report-x.pdf",
                        "guardrails": {"checks": {"a": {"pass": True},
                                                  "b": {"pass": False}}},
                    })
                else:
                    route.fulfill(status=500, body="boom")

            page.route("**/api/latest",
                       lambda r: fulfill_json(r, {"exists": False}))

            def research_route(route):
                if state["research_status"] == 200:
                    fulfill_json(route, {"run_id": "t1"})
                else:
                    route.fulfill(status=500, body="boom")

            page.route("**/api/research", research_route)
            page.route("**/api/progress/**", lambda r: r.fulfill(
                status=200, content_type="text/event-stream",
                body='data: {"stage":"__end__","data":{}}\n\n'))
            page.route("**/api/run/**",
                       lambda r: fulfill_json(r, RUN_RESULT))
            page.route("**/api/refine/**", refine_route)
            page.route("**/api/build/**", build_route)
            page.goto(base)
            assert page.title() == "Greysearch", "unexpected start title"

            # GS-03: a refused submit must re-enable the button and say why
            page.fill("#q", "hostile metadata drill")
            page.click("#go")
            page.wait_for_function(
                "!document.getElementById('go').disabled", timeout=10000)
            assert "Couldn't start the run" in page.inner_text("#err")
            print("PASS  GS-03: submit control restored after server error")

            # drive a run like a user: submit -> mocked SSE end -> poll
            # picks up the done run and paints the results (within 5s)
            state["research_status"] = 200
            page.fill("#q", "hostile metadata drill")
            page.click("#go")
            page.wait_for_function(
                "document.getElementById('res').style.display === 'block'",
                timeout=15000)

            assert page.title() == "Greysearch", \
                "GS-01: a payload executed during result painting"
            assert P_TAG in page.inner_text("#brief"), \
                "guide preview payload lost from #brief text"
            assert page.locator("#brief img").count() == 0, \
                "preview rendered as markup"

            srcs = page.locator("#srcs li")
            assert srcs.count() == 3, "expected 3 source rows, got %d" % srcs.count()
            assert srcs.nth(0).locator("a").get_attribute("href") is None, \
                "javascript: URL rendered with a clickable href"
            assert srcs.nth(0).inner_text() == P_JSURL, "refused URL text lost"
            assert srcs.nth(2).locator("a").get_attribute("href") == \
                "https://safe.test/page", "clean URL lost its link"
            assert srcs.nth(1).locator("a").inner_text() == P_MOUSE, \
                "quote-bearing URL text lost"

            imgs = page.locator("#imgs .imgc")
            assert imgs.count() == 2, "expected 2 image cards"
            bad_img_src = imgs.nth(0).locator("img").get_attribute("src") or ""
            assert '"' not in bad_img_src, \
                "image path broke out of src: %r" % bad_img_src
            assert imgs.nth(0).inner_text() == P_TAG + "\n" + P_QUOTE, \
                "image metadata lost as text"
            assert imgs.nth(1).inner_text().startswith("clean title")

            boxes = page.locator("#researchtext .rtsrc")
            assert boxes.count() == 2, "expected 2 live-text boxes"
            assert boxes.nth(0).locator("h4").inner_text() == P_TAG, \
                "page title lost as text"
            assert boxes.nth(0).locator("p").count() == 1, "paragraph dropped by gate"
            src_line = boxes.nth(1).locator(".src")
            assert src_line.locator("a").get_attribute("href") is None, \
                "javascript: page URL rendered clickable in live text"
            assert src_line.locator("a").inner_text() == P_JSURL, \
                "refused page URL text lost"
            assert page.title() == "Greysearch", \
                "GS-01: a payload executed after result painting"
            print("PASS  GS-01 sinks: no execution; text + link allowlist hold")

            # GS-03: control restore on failed refine
            page.fill("#refineq", "narrow it")
            page.click("#refinego")
            page.wait_for_function(
                "!document.getElementById('refinego').disabled", timeout=10000)
            assert "Refine failed" in page.inner_text("#refineout")
            print("PASS  GS-03: refine control restored after server error")

            # refine success through liteMd + suggestion grid
            state["refine_status"] = 200
            page.click("#refinego")
            page.wait_for_function(
                "!document.getElementById('refinego').disabled", timeout=10000)
            out_html = page.inner_html("#refineout")
            assert "<img" not in out_html and "<svg" not in out_html, \
                "liteMd let raw markup through"
            assert P_TAG in page.inner_text("#refineout"), "refine text lost as text"
            sugg = page.locator("#imgs .imgc")
            assert sugg.count() == 3, "suggestion card missing"
            assert sugg.nth(2).locator("img").get_attribute("src") == \
                "/imgcand/t/sug.png", "suggestion path mangled"
            assert "para 1" in sugg.nth(2).inner_text(), "suggestion label lost"
            assert page.title() == "Greysearch", \
                "GS-01: a payload executed through the refine path"
            print("PASS  GS-01 refine: liteMd neutralizes tags; cards text-safe")

            # GS-03: control restore on failed build, then success anchors
            page.click("#build")
            page.wait_for_function(
                "!document.getElementById('build').disabled", timeout=10000)
            assert "Build failed" in page.inner_text("#built")
            state["build_status"] = 200
            page.click("#build")
            page.wait_for_function(
                'document.getElementById("built")'
                '.textContent.includes("Guardrails 1/2")', timeout=10000)
            links = page.locator("#built a")
            assert links.count() == 3, "expected Open it / zip / PDF links"
            assert links.nth(0).get_attribute("href") == \
                "/site/d/sites/site-x/index.html"
            assert links.nth(1).get_attribute("download") is not None
            assert page.title() == "Greysearch", \
                "GS-01: a payload executed through the build path"
            print("PASS  GS-03 build: restore on error; success anchors DOM-built")

            browser.close()
            print("ALL PASS: portal XSS + control-restore fixtures "
                  "(findings GS-01, GS-03)")
            return 0
    finally:
        server.shutdown()


def main():
    try:
        return run()
    except AssertionError as e:
        print("FAIL: %s" % e)
        return 1
    except Exception as e:
        print("FAIL: %r" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())