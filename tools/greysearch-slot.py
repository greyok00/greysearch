#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path
MAP = Path.home() / 'greysearch' / 'research' / 'browser-ports.json'
BASE_PORT = 9350
TASK_RE = re.compile('^task-(\\d+)$')

def load() -> dict:
    with MAP.open() as fh:
        return json.load(fh)

def save(data: dict) -> None:
    tmp = MAP.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    os.replace(tmp, MAP)

def indexes(data: dict) -> list[int]:
    out = []
    for key in data.get('tasks', {}):
        m = TASK_RE.match(key)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)

def add(data: dict, n: int) -> dict:
    key = f'task-{n}'
    if key in data.setdefault('tasks', {}):
        return data['tasks'][key]
    entry = {'port': BASE_PORT + n, 'aliases': [key, str(n)], 'profile': f'~/.cache/greysearch/{key}-profile'}
    data['tasks'][key] = entry
    return entry

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--new', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--ensure', type=int, default=0)
    args = ap.parse_args()
    data = load()
    existing = indexes(data)
    if args.list or not (args.new or args.ensure):
        for n in existing:
            e = data['tasks'][f'task-{n}']
            print(f"task-{n}\tport {e['port']}\t{e['profile']}")
        print(f'total: {len(existing)} slots')
        return 0
    if args.ensure:
        for n in range(1, args.ensure + 1):
            add(data, n)
        save(data)
        print(f'ensure: {max(indexes(data))} slots defined')
        return 0
    n = max(existing) + 1 if existing else 1
    entry = add(data, n)
    save(data)
    print(json.dumps({'task': f'task-{n}', **entry}))
    return 0
if __name__ == '__main__':
    sys.exit(main())
