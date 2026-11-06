#!/usr/bin/env python3
import json
import re
import sys
import time
import urllib.request
BASE = 'http://127.0.0.1:8791'
SET_NAME = 'wiring acceptance set'
Q1_TAG = 'walkthrough run 1'

def api(path, method='GET', payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or '{}')

def _norm(s):
    s = re.sub('(?m)^\\s*>\\s?', '', str(s))
    return ' '.join(s.split())

def run():
    set_name = 'wiring acceptance set %d' % int(time.time())
    from patchright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1700, 'height': 1100})
        page.goto(BASE + '/')
        page.wait_for_function("document.body.dataset.screen === 'search'", timeout=30000)
        page.wait_for_function("!document.getElementById('gsreopen').hidden && document.getElementById('reopens-q').textContent.trim() !== ''", timeout=15000)
        latest = api('/api/latest')
        assert latest.get('status') == 'done', 'no done run to reopen: %r' % latest
        open_q = page.inner_text('#reopens-q')
        latest_q = str((api('/api/run/' + latest['run_id']).get('result') or {}).get('question') or '')
        assert open_q.strip() == latest_q.strip(), "reopen label is not the run's question: %r vs %r" % (open_q, latest_q)
        page.click('#reopens', timeout=5000)
        page.wait_for_function("!document.getElementById('curate').hidden", timeout=30000)
        meta = page.inner_text('#cur-meta')
        assert 'read' in meta and 'pages' in meta, 'curation meta: %r' % meta
        print('PASS  open: lands on search, reopen loads %s by name; %r' % (latest['run_id'][:10], meta[:70]))
        page.click("button[data-curtab='paras']")
        rows = page.locator('#cur-paras .gs-currow')
        assert rows.count() >= 80, 'paragraph rows too few: %d' % rows.count()
        assert page.locator('#cur-paras input').count() == 0, 'checkbox swarm must be gone from the curation pane'
        cur_rec = api('/api/curation/%s' % api('/api/latest')['run_id'])
        api_para = {}
        for pgr in cur_rec.get('curation', {}).get('pages') or []:
            for p in pgr.get('paragraphs') or []:
                api_para[str(p.get('id') or '')] = str(p.get('text') or '')
        assert api_para, 'curation record had no paragraphs'
        full = page.evaluate("(apiPara) => {\n                 let mism = 0, shown = 0, clamped = 0;\n                 document.querySelectorAll('#cur-paras .gs-currow')\n                   .forEach(r => {\n                     const pid = r.getAttribute('data-pid');\n                     const txt = r.querySelector('.gs-curtext') || {};\n                     if (!(pid in apiPara)) return;\n                     shown++;\n                     if (txt.textContent !== apiPara[pid]) mism++;\n                     if (r.scrollHeight > r.clientHeight + 1) clamped++;\n                   });\n                 return {mism: mism, shown: shown, clamped: clamped};\n               }", api_para)
        assert full['shown'] == len(api_para), 'rows painted %d of %d record paragraphs' % (full['shown'], len(api_para))
        assert full['mism'] == 0, '%d paragraph rows displayed clipped text' % full['mism']
        assert full['clamped'] == 0, '%d paragraph rows visually clamped' % full['clamped']
        row_text = rows.nth(0).locator('.gs-curtext').inner_text()
        rows.nth(0).click()
        page.wait_for_function("document.getElementById('outrail').classList.contains('open') && document.getElementById('drawbtn').getAttribute('aria-expanded') === 'true'", timeout=5000)
        _hint = page.inner_text('#savemsg')
        assert 'Select material' in _hint and 'Save research' in _hint, 'auto-opened drawer without its purpose line: %r' % _hint
        print('PASS  first pick slides the draft drawer open with its purpose line')
        rows.nth(1).click()
        page.wait_for_function("document.getElementById('cur-count').textContent.indexOf('2 paragraphs') === 0", timeout=5000)
        assert rows.nth(0).get_attribute('aria-checked') == 'true', 'clicked row did not set aria-checked'
        para_texts = [row_text[:60], rows.nth(1).locator('.gs-curtext').inner_text()[:60]]
        print('PASS  click-select 2 paragraphs, full text shown; count: %r' % page.inner_text('#cur-count'))
        page.click("button[data-curtab='kws']")
        chips = page.locator('#cur-kws .gs-kw')
        assert chips.count() >= 4, 'keyword chips: %d' % chips.count()
        chips.nth(0).click()
        chips.nth(1).click()
        page.wait_for_function("document.getElementById('cur-count').textContent.indexOf('2 keywords') !== -1", timeout=5000)
        assert chips.nth(0).get_attribute('aria-pressed') == 'true', 'clicked chip did not set aria-pressed'
        print('PASS  click-select 2 keywords')
        page.click("button[data-curtab='imgs']")
        cards = page.locator('#cur-imgs .gs-imgck')
        assert cards.count() > 3, 'image cards: %d' % cards.count()
        first_txt = cards.nth(0).inner_text()
        assert 'gate flagged' not in first_txt, 'cleared-first ordering broken; first card: %r' % first_txt[:80]
        flagged = sum((1 for i in range(cards.count()) if 'gate flagged' in cards.nth(i).inner_text()))
        assert flagged < cards.count(), 'no cleared images at all'
        assert page.locator('#cur-imgs input').count() == 0, 'checkbox swarm must be gone from the image pane'
        cards.nth(0).click()
        page.wait_for_function("document.getElementById('cur-count').textContent.indexOf('1 images') !== -1", timeout=5000)
        assert cards.nth(0).get_attribute('aria-checked') == 'true', 'clicked card did not set aria-checked'
        print('PASS  images: %d cards (%d flagged), clicked first cleared; %r' % (cards.count(), flagged, page.inner_text('#cur-count')))
        cw = page.evaluate("() => {\n            const d = document.querySelector('.gs-doc');\n            const c = document.querySelector('.gs-curate');\n            return {doc: d ? d.getBoundingClientRect().width : 0,\n                    cur: c ? c.getBoundingClientRect().width : 0};\n        }")
        assert cw['cur'] >= cw['doc'] * 0.9, 'curation list is not full width: %r' % cw
        assert page.locator('#cur-paras input').count() == 0, 'checkbox swarm returned'
        assert page.locator('#outrail').is_visible(), 'draft drawer was not open after the picks'
        page.keyboard.press('Escape')
        page.wait_for_function("!document.getElementById('outrail').classList.contains('open') && !document.getElementById('outrail').classList.contains('preopen')", timeout=5000)
        page.click('#drawbtn', timeout=5000)
        page.wait_for_function("document.getElementById('outrail').classList.contains('open')", timeout=5000)
        assert page.locator('#outrail').is_visible(), 'draft drawer did not slide back out from the Draft button'
        print('PASS  full-width list (%.0fpx of %.0fpx); drawer Escape-closes and reopens from the Draft menu' % (cw['cur'], cw['doc']))
        page.fill('#setname', set_name)
        page.click('#savebtn')
        page.wait_for_function("!document.getElementById('outdoc').hidden && document.getElementById('outdoc').textContent.trim() !== ''", timeout=20000)
        out = page.inner_text('#outdoc')
        _p0 = _norm(para_texts[0])
        assert _p0[:40] in _norm(out) or _p0[:30] in _norm(out), 'selected paragraph missing from composed draft'
        assert 'export' in page.inner_text('#outactions').lower(), 'no export affordance in output actions'
        sets = api('/api/sets')
        mine = [s for s in sets.get('sets') or [] if s.get('name') == set_name]
        assert len(mine) == 1, 'set not on list: %r' % sets
        set_id = mine[0]['set_id']
        assert (mine[0].get('runs') or 0) == 1, 'first save runs: %r' % mine[0]
        print('PASS  save set %s; composed draft holds the selection' % set_id)
        page.reload()
        page.wait_for_function("document.body.dataset.screen === 'search'", timeout=30000)
        page.wait_for_function("!document.getElementById('gsreopen').hidden", timeout=15000)
        page.click('#reopens', timeout=5000)
        page.wait_for_function("!document.getElementById('curate').hidden", timeout=30000)
        page.click('#drawbtn', timeout=5000)
        page.wait_for_function("document.getElementById('outrail').classList.contains('open')", timeout=5000)
        page.wait_for_function('document.getElementById(\'setsel\').querySelector(\'option[value="%s"]\') !== null' % set_id, timeout=10000)
        page.select_option('#setsel', set_id)
        page.wait_for_function("!document.getElementById('outdoc').hidden && document.getElementById('outdoc').textContent.trim() !== ''", timeout=20000)
        out2 = page.inner_text('#outdoc')
        _p0 = _norm(para_texts[0])
        assert _p0[:40] in _norm(out2) or _p0[:30] in _norm(out2), 'recomposed draft lost the selection'
        print('PASS  reopen: picker recomposes the saved research')
        rid1 = api('/api/latest').get('run_id') or ''
        page.click('#gr-pill')
        page.locator('#gr-pillmenu [role="menuitem"]', has_text='Search').first.click()
        page.fill('#q', 'web research agents structured data extraction methods')
        page.click('#go')
        print('  … second run submitted (baseline %s), waiting for a newer run to finish' % rid1)
        rid2 = None
        for _ in range(45):
            time.sleep(15)
            latest = api('/api/latest')
            if latest.get('status') == 'done':
                cand = latest.get('run_id') or ''
                if cand and cand != rid1:
                    rid2 = cand
                    break
        assert rid2, 'second run never finished'
        page.wait_for_function("window.__curLoad && !document.getElementById('curate').hidden", timeout=60000)
        rows2 = page.locator('#cur-paras .gs-currow')
        page.click("button[data-curtab='paras']")
        rows2 = page.locator('#cur-paras .gs-currow')
        assert rows2.count() >= 80, 'second-run rows: %d' % rows2.count()
        rows2.nth(0).click()
        rows2.nth(1).click()
        page.wait_for_function("document.getElementById('cur-count').textContent.indexOf('2 paragraphs') === 0", timeout=5000)
        new_texts = [rows2.nth(0).locator('.gs-curtext').inner_text()[:60], rows2.nth(1).locator('.gs-curtext').inner_text()[:60]]
        print('PASS  second run %s curates; 2 paragraphs clicked' % rid2)
        page.wait_for_function("document.getElementById('setsel').value === '%s'" % set_id, timeout=10000)
        page.click('#savebtn')
        page.wait_for_function("(document.getElementById('savemsg').textContent || '').indexOf('Saved') === 0", timeout=25000)
        sets = api('/api/sets')
        mine2 = [s for s in sets.get('sets') or [] if s['set_id'] == set_id]
        assert (mine2[0].get('runs') or 0) == 2, 'runs after add-run: %r' % mine2[0]
        out3 = page.inner_text('#outdoc')
        assert new_texts[0][:40] in out3 or new_texts[0][:30] in out3, "second run's paragraph missing after add-run"
        print('PASS  add-run: set %s now carries both runs; draft grew' % set_id)
        with urllib.request.urlopen(BASE + '/api/sets/%s/export.md' % set_id, timeout=30) as r:
            md = r.read().decode()
        _p1 = _norm(para_texts[0])
        _p2 = _norm(new_texts[0])
        assert _p1[:40] in _norm(md) or _p1[:30] in _norm(md), 'run-1 paragraph missing from export'
        assert _p2[:40] in _norm(md) or _p2[:30] in _norm(md), 'run-2 paragraph missing from export'
        print("PASS  export.md: %d bytes, both runs' material present" % len(md))
        page.click("button[data-curtab='paras']")
        page.locator('#outactions button', has_text='edit markdown').first.click()
        page.wait_for_function("!document.getElementById('outedit').hidden", timeout=10000)
        EDITMARK = 'GATE-EDIT-MARKER-%d' % int(time.time())
        page.fill('#outedit', 'hand-edited draft\n' + EDITMARK)
        page.locator('#outactions button', has_text='apply edits').first.click()
        page.wait_for_function("(document.getElementById('savemsg').textContent || '').indexOf('Edit saved') === 0", timeout=25000)
        with urllib.request.urlopen(BASE + '/api/sets/%s/export.md' % set_id, timeout=30) as r:
            md_edit = r.read().decode()
        assert EDITMARK in md_edit, 'applied edit missing from export.md'
        assert md_edit.startswith('hand-edited draft'), 'override not served verbatim: %r' % md_edit[:60]
        odh = page.inner_text('#outdoc')
        assert EDITMARK in odh, 'applied edit missing from the rendered draft'
        print('PASS  draft editor: hand edit held on compose + export')
        page.locator('#outactions button', has_text='reset to selections').first.click()
        page.wait_for_function("(document.getElementById('savemsg').textContent || '').indexOf('Draft reset') === 0", timeout=25000)
        with urllib.request.urlopen(BASE + '/api/sets/%s/export.md' % set_id, timeout=30) as r:
            md_reset = r.read().decode()
        assert EDITMARK not in md_reset, 'override survived the reset'
        _p1r = _norm(para_texts[0])
        assert _p1r[:40] in _norm(md_reset) or _p1r[:30] in _norm(md_reset), 'reset did not return to the selections compose'
        print('PASS  draft reset: export returns to the selection compose')
        browser.close()
        print('ALL PASS: curation workflow walkthrough (select → save → compose → reopen → second run → add-run → export → edit → reset)')
        return 0

def main():
    try:
        return run()
    except AssertionError as e:
        print('FAIL: %s' % e)
        return 1
    except Exception as e:
        print('FAIL: %r' % e)
        return 1
if __name__ == '__main__':
    sys.exit(main())
