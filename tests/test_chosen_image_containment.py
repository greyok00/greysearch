#!/usr/bin/env python3
"""Chosen-image copy containment, hermetic: a build request's chosen
"file" is only ever a candidate the run itself downloaded inside the
guide's own tree - a source anywhere else falls back to the remote url
and never lands in the web-served guides tree."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import portal_server as ps


def test_chosen_image_outside_the_guides_tree_is_not_copied(tmp_path):
    guides = tmp_path / "guides"
    guides.mkdir()
    gpath = guides / "run.md"
    gpath.write_text("# Guide\n\nBody paragraph.\n")
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret bytes")

    md = ps._insert_chosen_images(
        gpath.read_text(),
        [{"url": "https://img.example/a.jpg", "title": "A pick",
          "file": str(outside)}],
        gpath)

    assert not list(guides.glob("chosen-*"))
    assert "](https://img.example/a.jpg)" in md


def test_chosen_image_inside_the_guides_tree_is_copied(tmp_path):
    guides = tmp_path / "guides"
    cand_dir = guides / "run_imgcand"
    cand_dir.mkdir(parents=True)
    gpath = guides / "run.md"
    gpath.write_text("# Guide\n\nBody paragraph.\n")
    cand = cand_dir / "pic.jpg"
    cand.write_bytes(b"jpeg bytes")

    md = ps._insert_chosen_images(
        gpath.read_text(),
        [{"url": "https://img.example/a.jpg", "title": "A pick",
          "file": str(cand)}],
        gpath)

    copied = guides / "chosen-0-pic.jpg"
    assert copied.read_bytes() == b"jpeg bytes"
    assert "](chosen-0-pic.jpg)" in md
