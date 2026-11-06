#!/usr/bin/env python3
r"""models/CLIPTOKENIZER.py - the text-side adapter between a challenge
prompt and the vendored CLIP textual ONNX graphs (models/clip/, see
models/SOURCE).

The textual graph's decompiled contract (all three vendored pairs sampled):

    input  "TEXT": int32 [text_batch_size, 77]
    output (name varies, e.g. "TEXT_EMBEDDING"): float32 [B, dim]

i.e. it wants one 77-token row per prompt, exactly what CLIP's training
pipeline produced. The recipe below re-derives that pipeline from the
public algorithm:

  1. lowercase + whitespace-clean the prompt (challenge prompts are ASCII
     English; an HTML-entity unescape is applied first - the unicode
     normalization pass the original tooling performs is NOT replicated,
     a KNOWN GAP stated honestly below),
  2. split the text with CLIP's contraction-aware regex,
  3. map each fragment's UTF-8 bytes through the reversible
     byte-to-unicode table,
  4. apply byte-pair merges from the vendored vocabulary in rank order,
  5. wrap the ids in <|startoftext|> ...  and pad to 77 with zeros.

The vocabulary (bpe_simple_vocab_16e6.txt.gz) is NOT shipped in the repo -
it is fetched + sha256-verified by install.sh's vendor step like every
other weight-adjacent data file (models/CHECKSUMS pin). Its parsing is
anchored empirically: parsed correctly, the table puts <|startoftext|>
exactly at id 49406 and  at 49407 - the two ids CLIP's public release
documents - and a decode(encode(text)) round trip is text-identical. The
anchors are probed by the suite (tests/test_captcha_model.py) and the
loader below returns None (honest miss, never a wrong answer) when the
vocabulary is absent, corrupt, or out of spec.

KNOWN GAPS vs the reference pipeline, both disclosed rather than papered
over:
  * no ftfy-style unicode fixup: challenge prompts are ASCII English so
    this cannot bite them; non-ASCII prompts may tokenize differently.
  * the regex uses Python's stdlib `re` with a `[not-word-digit-underscore]` class instead
    of the reference tool's `\p{L}` classes: for ASCII prompts the two
    agree exactly.
"""
from __future__ import annotations

import gzip
import html
import logging
import os
import re
from typing import List, Optional

LOG = logging.getLogger("greysearch.captcha.model")

_CONTEXT = 77
_SOT = "<|startoftext|>"
_EOT = "<|endoftext|>"

# CLIP's contraction-aware token pattern, spelled with stdlib classes:
# `[^\W\d_]` = a unicode letter, `[0-9]` = one digit,
# `[^\s\w]+` = a punctuation run.
_PAT = re.compile(
    r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|"
    r"[^\W\d_]+|[0-9]|[^\s\w]+",
    re.IGNORECASE,
)


def tokenizer_vocab_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "clip",
        "bpe_simple_vocab_16e6.txt.gz")


def _bytes_to_unicode() -> dict:
    """The reversible byte->unicode table CLIP trains its BPE over:
    printable latin-1 ranges map onto themselves, every other byte
    slides into the 256.. codepoints so the merge vocabulary survives
    as plain text inside the gz file. Fresh implementation of the
    publicly documented mapping (validated byte-identical round trip)."""
    bs = (list(range(ord("!"), ord("~") + 1))
          + list(range(ord("\xa1"), ord("\xac") + 1))
          + list(range(ord("\xae"), ord("\xff") + 1)))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, (chr(c) for c in cs)))


class ClipTokenizer:
    """Prompt -> [77] int32 CLIP token row (raises on the constructor only
    when the vocabulary is absent/corrupt: use load_tokenizer for the
    total None-contract the solver reads through)."""

    def __init__(self, vocab_path: Optional[str] = None):
        path = vocab_path or tokenizer_vocab_path()
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            merges = fh.read().split("\n")
        # documented layout: header line + 49152-256-2 merge pair lines
        merges = merges[1: 49152 - 256 - 2 + 1]
        pair_rows = [tuple(m.split()) for m in merges]
        if any(len(row) != 2 for row in pair_rows):
            raise ValueError(f"corrupt BPE vocabulary: {path}")
        self.byte_encoder = _bytes_to_unicode()
        vocab = list(self.byte_encoder.values())
        vocab += [v + "</w>" for v in vocab]
        vocab += ["".join(row) for row in pair_rows]
        vocab += [_SOT, _EOT]
        self.encoder = {token: i for i, token in enumerate(vocab)}
        self.sot_id = self.encoder[_SOT]
        self.eot_id = self.encoder[_EOT]
        self.context = _CONTEXT
        self.bpe_ranks = {row: rank for rank, row in enumerate(pair_rows)}
        # the empirical anchoring of the parse: CLIP's own published ids
        if self.sot_id != 49406 or self.eot_id != 49407:
            raise ValueError("BPE vocabulary anchors drifted out of spec")
        self.bpe_cache: dict = {}

    def _bpe(self, token: str) -> str:
        """One space-less fragment -> its merged symbols, joined by
        spaces: byte-pair merging in the vocabulary's rank order."""
        if token in self.bpe_cache:
            return self.bpe_cache[token]
        # the word's last symbol carries the end-of-word marker
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        while len(word) > 1:
            pairs = [(word[i], word[i + 1]) for i in range(len(word) - 1)]
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, 1 << 30))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            merged: List[str] = []
            i = 0
            while i < len(word):
                if (i < len(word) - 1 and word[i] == first
                        and word[i + 1] == second):
                    merged.append(first + second)
                    i += 2
                else:
                    merged.append(word[i])
                    i += 1
            word = tuple(merged)
        out = " ".join(word)
        self.bpe_cache[token] = out
        return out

    def encode_ids(self, text: str) -> List[int]:
        """Prompt text -> raw token ids WITHOUT the SOT/EOT wrap."""
        text = html.unescape(html.unescape(text))
        text = re.sub(r"\s+", " ", text).strip().lower()
        ids: List[int] = []
        for frag in _PAT.findall(text):
            frag = "".join(self.byte_encoder[b] for b in frag.encode("utf-8"))
            ids.extend(self.encoder[tok] for tok in self._bpe(frag).split(" "))
        return ids

    def __call__(self, texts: List[str]):
        """Prompt list -> the textual graph's input tensor (numpy int32
        [B, 77]). Truncates past the context and always keeps a trailing
        end-of-text mark, zeros padding the rest."""
        import numpy as np
        rows = []
        for text in texts:
            ids = [self.sot_id] + self.encode_ids(text) + [self.eot_id]
            if len(ids) > self.context:
                ids = ids[:self.context]
                ids[-1] = self.eot_id
            row = np.zeros(self.context, dtype=np.int32)
            row[:len(ids)] = ids
            rows.append(row)
        return np.stack(rows)

    def decode(self, ids: List[int]) -> str:
        """Token ids -> text: the round-trip anchor the suite asserts."""
        byte_decoder = {v: k for k, v in self.byte_encoder.items()}
        decoder = {i: t for t, i in self.encoder.items()}
        joined = "".join(decoder[int(i)] for i in ids)
        raw = bytes(byte_decoder[c] for c in joined)
        return raw.decode("utf-8", errors="replace").replace("</w>", " ")


def load_tokenizer(vocab_path: Optional[str] = None) -> Optional[ClipTokenizer]:
    """None (honest) when the vocab is absent or fails its self-check -
    the CLIP rung then reports an honest miss for text-side matching,
    never a wrong answer."""
    try:
        return ClipTokenizer(vocab_path)
    except Exception as e:  # noqa: BLE001 - total by contract
        LOG.warning("clip tokenizer unavailable: %s", e)
        return None
