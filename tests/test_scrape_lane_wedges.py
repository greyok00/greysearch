#!/usr/bin/env python3
"""The wedge-proof scrape lane, hermetic: the wedge counter is per-RUN
(the bridge is a process singleton, so a cumulative counter bricks the
browser scrape lane for every later run in the same portal/MCP process),
each scrape's single-use worker sheds its own CDP client when the job
ends, and the wedge handler drops the WORKER thread's client by ident -
not the runner's. All browser lanes are stubbed; nothing real connects."""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import browser_session as bs
import research_integration as ri
import test_anti_flag_trio as af


class _FakePw:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


def _wedge_scrape(*args, **kwargs):
    raise TimeoutError("scrape wall timed out (testsuite)")


def test_wedge_counter_resets_per_run(monkeypatch):
    """The wedge counter is zeroed at the start of every research_deep run:
    two identical wedge runs each record their OWN wedges (a cumulative
    counter would double the second run's), so a later working run scrapes
    for real instead of skipping every candidate with 'browser scrape lane
    abandoned'."""
    bridge, _seen = af._deep_bridge(monkeypatch, "0")
    monkeypatch.setattr(bridge, "research_scrape", _wedge_scrape)
    res1 = bridge.research_deep(
        af._DEEP_QUESTION, max_pages=1, max_total_seconds=40,
        enable_semantic_judge=False, search_proxy="off")
    wedges1 = bridge._scrape_wedges
    assert wedges1 > 0

    res2 = bridge.research_deep(
        af._DEEP_QUESTION, max_pages=1, max_total_seconds=40,
        enable_semantic_judge=False, search_proxy="off")
    assert bridge._scrape_wedges == wedges1

    cls_scrape = ri.ResearchScraperBridge.research_scrape
    monkeypatch.setattr(bridge, "research_scrape",
                        lambda *a, **k: cls_scrape(bridge, *a, **k))
    res3 = bridge.research_deep(
        af._DEEP_QUESTION, max_pages=1, max_total_seconds=40,
        enable_semantic_judge=False, search_proxy="off")
    assert res3.get("all_scraped_pages"), res3.get("skipped")
    assert not any("browser scrape lane abandoned" in (s.get("reason") or "")
                   for s in res3.get("skipped", []))
    assert bridge._scrape_wedges == 0


def test_scrape_worker_sheds_its_own_connection(monkeypatch):
    """Each scrape's single-use worker thread owns the CDP client the fetch
    caches under its ident; when the job ends the worker itself drops it,
    so no client survives the run in the process cache (one leaked node
    driver per scrape was the old shape). Direct caller threads (the
    second-pass refill runs on the runner thread) keep their thread-keyed
    client by design and are out of scope here."""
    bridge, _seen = af._deep_bridge(monkeypatch, "0")
    cls_scrape = ri.ResearchScraperBridge.research_scrape
    runner_ident = threading.get_ident()
    scrape_idents = []

    def fake_scrape(*args, **kwargs):
        ident = threading.get_ident()
        scrape_idents.append(ident)
        with bs._CONN_LOCK:
            bs._CONNECTIONS[ident] = {"pw": _FakePw(), "key": ("t", "s")}
        try:
            return cls_scrape(bridge, *args, **kwargs)
        finally:
            # a direct caller keeps its own thread-keyed client; only the
            # runner's registration is cleaned here, the workers' by the
            # scrape lane itself.
            if ident == runner_ident:
                with bs._CONN_LOCK:
                    bs._CONNECTIONS.pop(ident, None)

    monkeypatch.setattr(bridge, "research_scrape", fake_scrape)
    try:
        bridge.research_deep(
            af._DEEP_QUESTION, max_pages=1, max_total_seconds=40,
            enable_semantic_judge=False, search_proxy="off")
        worker_idents = [i for i in scrape_idents if i != runner_ident]
        assert worker_idents
        with bs._CONN_LOCK:
            surviving = [i for i in worker_idents if i in bs._CONNECTIONS]
        assert not surviving
    finally:
        with bs._CONN_LOCK:
            bs._CONNECTIONS.pop(runner_ident, None)


def test_wedge_handler_drops_the_worker_client_by_ident():
    """drop_connection_ident pops exactly one cached client from any
    thread, stops its driver, returns False once it is gone, and leaves
    every other thread's client alone (the runner-side drop the wedge
    handler used to make popped nothing at all)."""
    victim = {"pw": _FakePw()}
    bystander = {"pw": _FakePw()}
    with bs._CONN_LOCK:
        bs._CONNECTIONS[987654] = victim
        bs._CONNECTIONS[987655] = bystander
    try:
        assert bs.drop_connection_ident(987654) is True
        assert victim["pw"].stopped == 1
        assert bs.drop_connection_ident(987654) is False
        with bs._CONN_LOCK:
            assert bs._CONNECTIONS.get(987655) is bystander
    finally:
        with bs._CONN_LOCK:
            bs._CONNECTIONS.pop(987654, None)
            bs._CONNECTIONS.pop(987655, None)
        bystander["pw"].stop()
