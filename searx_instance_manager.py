#!/usr/bin/env python3
"""searx_instance_manager.py - the self-updating SearXNG instance pool.

Owner order 2026-11-23: no joke/unreliable defaults, the pool searches searx.space for the MOST USEFUL instances,
downloads them into a list it keeps current: dead instances are pruned and
better ones are promoted in their place.

One pool file feeds the ladder: research/searx-fallback-roster.json (the
same schema searx_fallback._harvest_instances already reads, so no ladder
change is needed). Everything here is a SEARCH transport and never rides
the page-scrape path; the pool refresh itself goes DIRECT (a ProxyModeError
is honest, never silently direct).

Pipeline:
  harvest   - searx.space/data/instances.json (fixed source, never bypassed)
              filtered to normal-network instances whose status is 200 and
              whose search timing reports working engines; name-blocklist
              (joke/adult instances never enter the pool).
  rank      - static score from the searx.space record itself: search-grade
              (all/average), TLS grade, uptime 90-day average, working
              engines, latency. Higher is better.
  probe     - live test of the ranked candidates: one direct GET of
              /search?q=<probe>&format=json per instance, scored by
              (answered, latency). JSON-bot-walled instances probe-fail and
              stay out of the head of the pool (the browser rung still
              reaches them).
  write     - the surviving instances, best first, into the roster file with
              a per-instance record (probe ts, ms, verdict, consecutive
              failures). update mode: re-probe the CURRENT pool, prune dead
              ones, backfill from the candidate reserve.

Env knobs: GREYSEARCH_POOL_SIZE (default 10), GREYSEARCH_POOL_PROBES (how
many candidates get a live probe, default 25), GREYSEARCH_POOL_MAXFAIL
(consecutive probe failures before a pool entry is pruned, default 3).
"""
from __future__ import annotations

import json
import base64
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional

SEARX_SPACE_DATA = "https://searx.space/data/instances.json"
ROOT = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(ROOT, "research", "searx-fallback-roster.json")

SIZE_DEFAULT = 10
PROBES_DEFAULT = 25
MAXFAIL_DEFAULT = 3
PROBE_QUERY = "test"
PROBE_TIMEOUT_S = 12.0

# Name-level filter: unreliable/parody instances never enter the pool.
# Kept encoded so the shipped source carries no theme words; decode with
# base64 -d. Owner directive 2026-11-23: keep this list out of public diffs.
NAME_BLOCKLIST_RAW = (
    "ZmVtYm95CnBvcm4KbnNmdwpzZXgKbG9saQpoZW50YWkKeHh4CndvamFrCnNoaXRwb3N0Cm1hc3RvZG9uLnh5egpzbmVlZApncnVtcHkKdGxtdHJhbnNmZXI=",
)
NAME_BLOCKLIST = tuple(
    w for w in base64.b64decode(NAME_BLOCKLIST_RAW[0]).decode().split("\n") if w
)

GRADE_ORDER = {"A+": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1, "G": 1}


def _env_int(name: str, default: int) -> int:
    try:
        v = int(str(os.environ.get(name, "") or default).strip() or default)
    except (TypeError, ValueError):
        v = default
    return v if v > 0 else default


def pool_size() -> int:
    return _env_int("GREYSEARCH_POOL_SIZE", SIZE_DEFAULT)


def probes_budget() -> int:
    return _env_int("GREYSEARCH_POOL_PROBES", PROBES_DEFAULT)


def max_fail() -> int:
    return _env_int("GREYSEARCH_POOL_MAXFAIL", MAXFAIL_DEFAULT)


def _direct_open(url: str, timeout: float):
    """Pool refresh is a DIRECT search transport, always (empty ProxyHandler,
    so process-env proxies never ride along)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": _ua(),
                                               "Accept": "*/*"})
    return opener.open(req, timeout=timeout)


def _ua() -> str:
    try:
        import fp_shield as _fp
        return _fp.WINDOWS_UA
    except Exception:
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/156.0.0.0 Safari/537.36")


def blocked(name: str) -> bool:
    low = (name or "").lower()
    return any(bad in low for bad in NAME_BLOCKLIST)


def harvest() -> List[Dict[str, Any]]:
    """Pull + filter + score the searx.space candidate list (no probing)."""
    data = json.loads(_direct_open(SEARX_SPACE_DATA, 20).read()
                      .decode("utf-8"))
    out: List[Dict[str, Any]] = []
    for url, info in (data.get("instances") or {}).items():
        low = (url or "").lower()
        if blocked(low):
            continue
        if info.get("network_type") != "normal":
            continue
        if (info.get("http") or {}).get("status_code") != 200:
            continue
        timing = (info.get("timing") or {}).get("search") or {}
        if timing.get("error") is not None:
            continue
        working = int(timing.get("working_engines", 0) or 0)
        if working < 1:
            continue
        score = 0
        for key in ("all", "average"):
            g = (timing.get(key) or {}).get("grade")
            score += GRADE_ORDER.get(str(g).upper(), 0) * 10
        tls_g = ((info.get("tls") or {}).get("grade") or "")
        score += GRADE_ORDER.get(str(tls_g).upper(), 0) * 3
        up = ((info.get("uptime") or {}).get("uptimeDay") or 0)
        try:
            score += float(up) * 10
        except (TypeError, ValueError):
            pass
        lat = timing.get("all", {}).get("median") if isinstance(
            timing.get("all"), dict) else None
        try:
            if lat:
                score += max(0.0, 2000.0 - float(lat)) / 1000.0
        except (TypeError, ValueError):
            pass
        out.append({"url": url, "score": round(score, 2),
                    "working_engines": working, "latency_ms": lat})
    out.sort(key=lambda x: -x["score"])
    return out


def probe(base_url: str) -> Dict[str, Any]:
    """One live JSON search against an instance. Never raises.

    verdict classes: "json" (answers the JSON API), "429" (rate-limited —
    still a working instance for the browser rung), "botwall" (challenge
    page instead of JSON), "dead" (unreachable/other). 429 is SOFT: it
    never prunes an instance, it only orders it after JSON-answerers."""
    url = base_url.rstrip("/") + "/search?q=" + PROBE_QUERY + "&format=json"
    started = time.time()
    try:
        body = _direct_open(url, PROBE_TIMEOUT_S).read().decode("utf-8")
        ms = int((time.time() - started) * 1000)
        parsed = json.loads(body)
        n = len(parsed.get("results", [])) if isinstance(parsed, dict) else 0
        if n > 0:
            return {"ok": True, "verdict": "json", "ms": ms, "results": n}
        return {"ok": False, "verdict": "botwall", "ms": ms, "results": n,
                "error": "0 results"}
    except Exception as e:
        ms = int((time.time() - started) * 1000)
        msg = re.sub(r"\s+", " ", str(e))[:120]
        if "429" in msg:
            return {"ok": False, "verdict": "429", "ms": ms, "error": msg}
        if "403" in msg or "418" in msg or "401" in msg:
            return {"ok": False, "verdict": "botwall", "ms": ms,
                    "error": msg}
        return {"ok": False, "verdict": "dead", "ms": ms, "error": msg}


def _ts(stamp: Optional[str]) -> float:
    """Parse a probed timestamp back to epoch (0 when absent/junk)."""
    try:
        return time.mktime(time.strptime(str(stamp or ""),
                                         "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def load_pool() -> Dict[str, Any]:
    try:
        return json.load(open(POOL_FILE))
    except Exception:
        return {"harvested": "", "instances": [], "records": {}}


def _records_of(pool: Dict[str, Any]) -> Dict[str, Any]:
    return pool.setdefault("records", {})


def refresh(*, do_probe: bool = True) -> Dict[str, Any]:
    """Full rebuild: harvest, rank, probe candidates, write the pool."""
    candidates = harvest()
    records = _records_of(load_pool())

    def _record(url: str, r: Dict[str, Any]) -> None:
        rec = records.setdefault(url, {"fails": 0})
        rec.update({"probed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ms": r.get("ms"),
                    "results": r.get("results"),
                    "error": r.get("error"),
                    "verdict": r.get("verdict"),
                    "json_api": bool(r.get("ok"))})
        soft = r.get("verdict") in ("429", "botwall")
        if r.get("ok"):
            rec["fails"] = 0
        elif soft:
            rec["fails"] = rec.get("fails", 0)  # unchanged: soft failures
        else:
            rec["fails"] = rec.get("fails", 0) + 1

    if do_probe:
        budget = probes_budget()
        for c in candidates[:budget]:
            _record(c["url"], probe(c["url"]))
    live = [c for c in candidates
            if (records.get(c["url"], {}).get("verdict") or "fresh") != "dead"
            or records.get(c["url"], {}).get("fails", 0) < max_fail()]
    # the final pool head must always carry a live probe verdict: static
    # rank alone says nothing about the JSON API (the bot-wall 429 crowd
    # ranks high and dies in the probe)
    for c in live[:pool_size()]:
        url = c["url"]
        if records.get(url, {}).get("json_api") is not None \
                and records[url].get("fails", 0) == 0 \
                and time.time() - _ts(records[url].get("probed")) < 86400:
            continue
        _record(url, probe(url))
    # JSON-answerers head the pool; soft-failed (429/botwall) instances
    # follow as browser-rung material; dead ones never enter.
    live.sort(key=lambda c: {"json": 0, "botwall": 1, "429": 2,
                             None: 3}.get(
        records.get(c["url"], {}).get("verdict"), 3))
    pool = {
        "harvested": time.strftime("%Y-%m-%d"),
        "instances": [c["url"] for c in live[:pool_size()]],
        "records": {u: records[u] for u in records
                    if any(c["url"] == u for c in candidates)},
        "reserve": [c["url"] for c in live[pool_size():pool_size() * 3]],
        "blocklist": list(NAME_BLOCKLIST),
    }
    json.dump(pool, open(POOL_FILE, "w"), indent=1)
    return pool


def update() -> Dict[str, Any]:
    """Keep the pool current: re-probe current members + reserve, prune the
    dead (consecutive fails >= max_fail), promote fresh reserve candidates
    into the freed slots. Cheaper than refresh; safe to run on a schedule."""
    pool = load_pool()
    records = _records_of(pool)
    current = list(pool.get("instances") or [])
    reserve = list(pool.get("reserve") or [])
    mf = max_fail()
    # probe current members
    survivors: List[str] = []
    for url in current:
        r = probe(url)
        rec = records.setdefault(url, {"fails": 0})
        rec.update({"probed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ms": r.get("ms"), "results": r.get("results"),
                    "error": r.get("error"),
                    "verdict": r.get("verdict"),
                    "json_api": bool(r.get("ok"))})
        if r.get("ok"):
            rec["fails"] = 0
        elif r.get("verdict") not in ("dead",):
            pass  # soft: no fail increment
        else:
            rec["fails"] = rec.get("fails", 0) + 1
        if r.get("ok") or (r.get("verdict") != "dead") or rec["fails"] < mf:
            survivors.append(url)
        else:
            rec["pruned"] = time.strftime("%Y-%m-%d")
    # freshen the reserve with a ranked harvest
    try:
        candidates = harvest()
    except Exception:
        candidates = []
    fresh = [c["url"] for c in candidates
             if c["url"] not in survivors][:pool_size() * 3]
    # promote probed reserve members that answer
    promoted: List[str] = []
    for url in (reserve + fresh):
        if url in survivors or len(promoted) >= (pool_size() - len(survivors)):
            continue
        r = probe(url)
        rec = records.setdefault(url, {"fails": 0})
        rec.update({"probed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ms": r.get("ms"), "results": r.get("results"),
                    "error": r.get("error"),
                    "json_api": bool(r.get("ok"))})
        if r.get("ok"):
            survivors.append(url)
            promoted.append(url)
        else:
            rec["fails"] = rec.get("fails", 0) + 1
    new_reserve = [u for u in (reserve + fresh)
                   if u not in survivors][:pool_size() * 3]
    survivors.sort(key=lambda u: {"json": 0, "botwall": 1, "429": 2}.get(
        (records.get(u) or {}).get("verdict"), 3))
    pool.update({"harvested": time.strftime("%Y-%m-%d"),
                 "instances": survivors[:pool_size()],
                 "reserve": new_reserve,
                 "records": records})
    json.dump(pool, open(POOL_FILE, "w"), indent=1)
    return {"pruned": [u for u in current if u not in survivors],
            "promoted": promoted, "pool": survivors[:pool_size()]}


def main() -> int:
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "refresh":
        pool = refresh()
        print(f"pool rebuilt: {len(pool['instances'])} instances")
        for u in pool["instances"]:
            print(" ", u)
        return 0
    if cmd == "update":
        res = update()
        print(f"pruned: {res['pruned'] or 'none'}")
        print(f"promoted: {res['promoted'] or 'none'}")
        print("pool:")
        for u in res["pool"]:
            print(" ", u)
        return 0
    if cmd == "show":
        pool = load_pool()
        for u in pool.get("instances", []):
            rec = (pool.get("records") or {}).get(u, {})
            print(f"  {u}  fails={rec.get('fails', 0)} "
                  f"json={rec.get('json_api')} ms={rec.get('ms')}")
        return 0
    print("usage: searx_instance_manager.py [refresh|update|show]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
