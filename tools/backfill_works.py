#!/usr/bin/env python3
"""backfill_works: shelve every already-built site on disk into data/works/.

Walks the guides trees (repo guides/ plus any GREYSEARCH_OUT_DIR-style
tree passed on the command line), finds <stem>_site/index.html builds,
and ingests each once through works_library.ingest_build. Safe to rerun:
known ids are skipped. Titles come from the guide H1, the query from the
curation sidecar when one exists.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import works_library  # noqa: E402

TREES = [
    Path("/home/grey/greysearch/guides"),
    Path("/home/grey/greysearch-guides"),
]


def title_of(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.*)$", md_text, re.M)
    if m:
        t = m.group(1).strip()
        t = re.sub(r"^Master Guide\s*[—–-]\s*", "", t, flags=re.I)
        return t or fallback
    return fallback


def query_of(cpath: Path) -> str:
    try:
        return str(json.loads(cpath.read_text()).get("query") or "")
    except Exception:
        return ""


def main() -> int:
    added = skipped = 0
    for tree in TREES:
        if not tree.is_dir():
            continue
        for site in sorted(tree.glob("*/*_site")):
            if not (site / "index.html").is_file():
                continue
            md = site.with_name(site.name[:-len("_site")] + ".md")
            if not md.is_file():
                continue
            wid_root = site.name[:-len("_site")]
            # skip if an equivalent work row already exists - identity is
            # the guide slug (shelf ids stamp TODAY at ingest time, and the
            # day dir of the source tree may differ, so match on slug)
            slug = re.sub(r"[^a-z0-9]+", "-", wid_root.lower()).strip("-")
            shelf = works_library.shelf_data()
            existing = [w for w in shelf["works"]
                        if re.search(re.escape(slug[:40]), w.get("id", ""))]
            if existing:
                skipped += 1
                continue
            build = {"site": str(site), "theme": "master",
                     "refined_source": "deterministic"}
            z = Path(str(site) + ".zip")
            if z.is_file():
                build["zip"] = str(z)
            pdfs = sorted(site.parent.glob(site.name + ".pdf"))
            if pdfs:
                build["pdf"] = str(pdfs[0])
            md_text = md.read_text(errors="replace")
            row = works_library.ingest_build(
                str(md), build,
                {"title": title_of(md_text, wid_root.replace("-", " ")),
                 "query": query_of(md.with_suffix(md.suffix + ".curation.json")),
                 "run_id": ""})
            if row:
                # stamp the row with the guide's real day so dedupe keys hold
                row["day"] = day
                works_library._meta_write(works_library.WORKS_ROOT / row["id"],
                                          row)
                idx = works_library._index_read()
                for i, w in enumerate(idx.get("works") or []):
                    if w.get("id") == row["id"]:
                        idx["works"][i]["day"] = day
                works_library._index_write(idx)
                added += 1
                print("shelved:", row["id"])
            else:
                skipped += 1
    print(f"done: {added} added, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
