#!/usr/bin/env python3
import sys
path = sys.argv[1]
src = open(path, encoding='utf-8').read()
applied = []

def replace(name, old, new, already_marker=None):
    global src
    marker = already_marker if already_marker is not None else new.split('\n')[0]
    if marker in src and old not in src:
        applied.append(f'{name}: SKIP (already applied)')
        return
    assert src.count(old) == 1, f'{name}: found {src.count(old)} matches'
    src = src.replace(old, new)
    applied.append(f'{name}: OK')
replace('prefilter-floor', '    if tokens and coverage < 0.34:\n        return {"decision": "reject",\n                "reason": f"low topic-token coverage ({coverage:.0%})",\n                "coverage": coverage, "procedural": False, "tier": tier}', '    _n_tok = len(tokens) if tokens else 0\n    _floor = (0.34 if _n_tok <= 8\n              else min(0.34, max(0.06, 2.5 / _n_tok)))\n    if tokens and coverage < _floor:\n        return {"decision": "reject",\n                "reason": (f"low topic-token coverage ({coverage:.0%}; "\n                           f"floor {_floor:.0%} for {_n_tok}-token question)"),\n                "coverage": coverage, "procedural": False, "tier": tier}')
if '_ord = ordered_question_tokens(question)' not in src:
    old = '    toks = question_tokens(question)\n    scope_tok = question_tokens(scope) if (scope or "").strip() else set()'
    new = '    toks = question_tokens(question)\n    _ord = ordered_question_tokens(question)\n    if len(_ord) > 20:\n        toks = set(_ord[:20])\n    scope_tok = question_tokens(scope) if (scope or "").strip() else set()'
    assert src.count(old) == 1
    src = src.replace(old, new)
    applied.append('judge-token-cap: OK')
else:
    applied.append('judge-token-cap: present')
if '_drift_short_only' not in src:
    old = '    for p in pages:\n        obj = _deterministic_judge_obj(question, p, guide_type)'
    new = '    _ord_all = ordered_question_tokens(question)\n    _drift_short_only = len(_ord_all) <= 8\n    for p in pages:\n        obj = _deterministic_judge_obj(question, p, guide_type)'
    assert src.count(old) == 1
    src = src.replace(old, new)
    old2 = '        drift = focal_tail_drift(question, p)\n        noise = noise_shape_warnings(p)'
    new2 = '        drift = focal_tail_drift(question, p) if _drift_short_only else None\n        noise = noise_shape_warnings(p)'
    assert src.count(old2) == 1
    src = src.replace(old2, new2)
    applied.append('drift-short-only: OK')
else:
    applied.append('drift-short-only: present')
open(path, 'w', encoding='utf-8').write(src)
print('\n'.join(applied))
print('PATCHED:', path)
