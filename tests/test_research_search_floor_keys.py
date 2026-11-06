"""The MCP search-only surface must carry the manager's floor record:
relevant_count and relevant_target ride through research_search instead of
being lost when the bridge rebuilds its response dict."""
import research_integration as ri


class _FakeManager:
    def __init__(self, record):
        self._record = record

    def search(self, **kwargs):
        return self._record

    def get_working_engines(self):
        return ["duckduckgo"]


def _record(with_floor_keys):
    record = {
        "ok": True,
        "results": [
            {"title": "Draw Things community port", "url": "https://github.com/drawthingsai/draw-things-community",
             "content": "upstream Linux build support"},
            {"title": "Draw Things guide", "url": "https://docs.example.com/draw-things",
             "content": "how the mac app maps to Linux"},
            {"title": "Inkforge notes", "url": "https://notes.example.com/inkforge",
             "content": "porting plan for the sampler"},
        ],
        "ladder": ["proxy mode off: direct",
                   "local: 29 results (full engine set)",
                   "direct keyless: not engaged (enough results)"],
        "engines_tried": ["duckduckgo", "brave"],
        "search_proxy": "",
        "last_error": None,
    }
    if with_floor_keys:
        record["relevant_count"] = 29
        record["relevant_target"] = 20
    return record


def _bridge(record):
    bridge = ri.ResearchScraperBridge.__new__(ri.ResearchScraperBridge)
    bridge.searxng_manager = _FakeManager(record)
    bridge.excluded_domains = set()
    bridge.sites_data = {"categories": {}}
    return bridge


def test_keys_ride_through_when_manager_provides_them():
    bridge = _bridge(_record(with_floor_keys=True))
    result = bridge.research_search("draw things mac app linux")
    assert result["relevant_count"] == 29
    assert result["relevant_target"] == 20


def test_keys_absent_when_manager_omits_them():
    bridge = _bridge(_record(with_floor_keys=False))
    result = bridge.research_search("draw things mac app linux")
    assert "relevant_count" not in result
    assert "relevant_target" not in result


def test_ladder_and_rows_still_ride_through():
    bridge = _bridge(_record(with_floor_keys=True))
    result = bridge.research_search("draw things mac app linux")
    assert "local: 29 results (full engine set)" in result["ladder"]
    assert result["results"][0]["url"].startswith("https://")
    assert result["ok"] is True
