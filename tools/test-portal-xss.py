#!/usr/bin/env python3
import functools
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
P_TAG = '<img src=x onerror="document.title=\'PWNED-TAG\'">'
P_QUOTE = '"><svg onload="document.title=\'PWNED-QUOTE\'">'
P_JSURL = "javascript:document.title='PWNED-URL';1"
P_MOUSE = 'https://x.test/path"onmouseover="document.title=\'PWNED-HOVER\'"'
TITLE = 'Greysearch - Research station'
CURATION = {'query': P_TAG, 'counts': {'pages': 2, 'accepted': 1, 'quarantined': 0, 'rejected': 1, 'unscraped_candidates': 3}, 'unscraped_candidates': [{'url': P_JSURL, 'decision': 'deprioritized', 'reason': P_QUOTE}, {'url': 'https://good.test/openme', 'decision': 'deprioritized', 'reason': 'page cap reached'}, P_TAG], 'query_keywords': [P_QUOTE, 'clean kw'], 'pages': [{'id': 'pg0', 'url': P_MOUSE, 'title': P_TAG, 'decision': 'accepted', 'reason': P_QUOTE, 'relevance_score': 0.9, 'source_type': 'web', 'chars': 120, 'paragraphs': [{'id': 'pg0#p0', 'text': 'A long paragraph that survives the content gate ' + P_TAG + ' because it is prose.'}], 'paragraphs_truncated': False, 'keywords': [P_TAG, 'page kw'], 'headings': [], 'images': [{'src': 'https://good.test/i.jpg', 'title': P_TAG, 'gated': False}, {'src': P_JSURL, 'title': 'no scheme gate', 'gated': False}]}, {'id': 'pg1', 'url': P_JSURL, 'title': P_QUOTE, 'decision': 'rejected', 'reason': 'hostile', 'chars': 0, 'paragraphs': [], 'paragraphs_truncated': False, 'keywords': [], 'headings': []}], 'image_candidates': [{'file': 'guides/t/ok.png', 'title': P_TAG, 'license': P_QUOTE}]}
CURATION_AFTER = {'query': P_TAG, 'counts': {'pages': 3, 'accepted': 2, 'quarantined': 0, 'rejected': 1, 'unscraped_candidates': 2}, 'unscraped_candidates': [{'url': P_JSURL, 'decision': 'deprioritized', 'reason': P_QUOTE}, P_TAG], 'query_keywords': [P_QUOTE, 'clean kw'], 'pages': [{'id': 'pg0', 'url': P_MOUSE, 'title': P_TAG, 'decision': 'accepted', 'reason': P_QUOTE, 'relevance_score': 0.9, 'source_type': 'web', 'chars': 120, 'paragraphs': [{'id': 'pg0#p0', 'text': 'A long paragraph that survives the content gate ' + P_TAG + ' because it is prose.'}], 'paragraphs_truncated': False, 'keywords': [P_TAG, 'page kw'], 'headings': [], 'images': [{'src': 'https://good.test/i.jpg', 'title': P_TAG, 'gated': False}, {'src': P_JSURL, 'title': 'no scheme gate', 'gated': False}]}, {'id': 'pg1', 'url': P_JSURL, 'title': P_QUOTE, 'decision': 'rejected', 'reason': 'hostile', 'chars': 0, 'paragraphs': [], 'paragraphs_truncated': False, 'keywords': [], 'headings': []}, {'id': 'pg2', 'url': 'https://good.test/openme', 'title': P_QUOTE, 'decision': 'manual', 'reason': P_TAG, 'relevance_score': 0.0, 'source_type': 'web', 'chars': 90, 'tier': 'second-wave', 'paragraphs': [{'id': 'pg2#p0', 'text': "Fetched on the user's " + 'command with a hostile payload ' + P_TAG + ' riding in the prose.'}], 'paragraphs_truncated': False, 'keywords': [], 'headings': [], 'images': []}], 'image_candidates': [{'file': 'guides/t/ok.png', 'title': P_TAG, 'license': P_QUOTE}]}
ENGINE_STATE = {'browser': {'task': P_TAG, 'port': 9363, 'up': True}, 'slots': [{'task': P_TAG, 'port': 9363, 'up': True}], 'listening': 1, 'engines_enabled': 2, 'search_note': P_QUOTE, 'newest': {'written': 0, 'query': P_TAG, 'accepted': 3}, 'engine_catalog': [{'name': P_TAG, 'categories': ['general', 'web']}, {'name': P_TAG + ' images', 'categories': ['images']}, {'name': 'mojeek', 'categories': ['general', 'web']}, {'name': 'wikipedia', 'categories': ['general']}, {'name': 'arxiv', 'categories': ['science']}]}
RUNS_LEDGER = {'runs': [{'query': P_TAG, 'origin': 'webui', 'run_id': 'r1' + P_TAG[:2], 'scraped': 2, 'candidates': 3, 'accepted': 2, 'quarantined': 0, 'modified': 0, 'bytes': 42, 'accepted_sources': [P_MOUSE, P_JSURL, 'https://safe.test/x']}, {'query': 'second row stays text ' + P_QUOTE, 'origin': 'webui', 'run_id': '', 'modified': 0, 'bytes': 0, 'accepted': 0, 'candidates': 0, 'scraped': 0, 'quarantined': 0}], 'root': 'x'}
RUN_RESULT = {'status': 'done', 'result': {'question': P_TAG, 'master_guide': {'preview': '**Executive Brief:** the brief\nspans two lines. ' + P_TAG + ' and more prose.\n\nRest of the guide.', 'path': '/somewhere/guides/2026-01-01/master-guide.md'}, 'sources': [P_JSURL, P_MOUSE, 'https://safe.test/page'], 'image_candidates': [{'file': 'guides/t/bad"file.png', 'title': P_TAG, 'license': P_QUOTE}, {'file': 'guides/t/ok.png', 'title': 'clean title', 'license': 'CC0'}], 'pages_fetched': [{'url': P_MOUSE, 'title': P_TAG, 'text': 'A long paragraph that survives the content gate ' + P_TAG + ' because it does not start with a tag.'}, {'url': P_JSURL, 'title': P_QUOTE, 'text': 'Second paragraph also longer than the gate requires ' + P_QUOTE + ' so it appears in the live text view.'}]}}
HOSTILE_EVENTS = [{'type': 'kw', 'value': 'page kw', 'on': True}, 'this line is not an object', None, {'type': 'para', 'on': True, 'pid': 'pg0#p<script>doc', 'u': P_MOUSE}, {'type': 'uurl', 'on': True, 'u': P_JSURL}, {'type': 'uurl', 'on': True, 'u': 'https://ghost.test/no-row'}, {'type': 'img', 'on': True, 'key': 'u:' + P_TAG}, {'type': 'save', 'set_id': 's1'}, {'type': 'kw', 'value': 'clean kw', 'on': True}, {'type': 'para', 'on': True, 'pid': 'pg0#p0', 'u': P_MOUSE, 'ts': 4.5}, {'type': 'para', 'on': True, 'pid': P_TAG + '#p0', 'u': 'https://x.test/none'}]

def fulfill_json(route, payload, status=200):
    route.fulfill(status=status, content_type='application/json', body=json.dumps(payload))

def run():

    class Handler(SimpleHTTPRequestHandler):

        def log_message(self, *a):
            pass

        def translate_path(self, path):
            for prefix, sub in (('/kit/', 'assets/kit-source/'), ('/assets/', 'assets/')):
                if path.startswith(prefix):
                    return str(ROOT / sub / path[len(prefix):].split('?')[0])
            return super().translate_path(path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d/portal/index.html' % port
    state = {'research_status': 500, 'refine_status': 500, 'build_status': 500, 'curation': CURATION, 'fetched_urls': None, 'events': None, 'events_post': []}
    from patchright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                print('SKIP: cannot launch Chromium (%s)' % e)
                sys.exit(77)
            page = browser.new_page()

            def refine_route(route):
                if state['refine_status'] == 200:
                    fulfill_json(route, {'ok': True, 'text': 'Refined brief with ' + P_TAG + ' inside.', 'image_suggestions': [{'file': 'guides/t/sug.png', 'title': 'clean sugg', 'license': '', 'para': 0}]})
                else:
                    route.fulfill(status=500, body='boom')

            def build_route(route):
                if state['build_status'] == 200:
                    fulfill_json(route, {'ok': True, 'site': '/x/guides/d/sites/site-x', 'pdf': '/x/guides/d/report-x.pdf', 'guardrails': {'checks': {'a': {'pass': True}, 'b': {'pass': False}}}})
                else:
                    route.fulfill(status=500, body='boom')
            page.route('**/api/latest', lambda r: fulfill_json(r, {'exists': False}))

            def research_route(route):
                if state['research_status'] == 200:
                    fulfill_json(route, {'run_id': 't1'})
                else:
                    route.fulfill(status=500, body='boom')
            page.route('**/api/research', research_route)
            page.route('**/api/progress/**', lambda r: r.fulfill(status=200, content_type='text/event-stream', body='data: {"stage":"__end__","data":{}}\n\n'))
            page.route('**/api/run/**', lambda r: fulfill_json(r, RUN_RESULT))
            page.route('**/api/curation/**', lambda r: fulfill_json(r, {'exists': True, 'run_id': 't1', 'curation': state['curation']}))
            page.route('**/api/sets', lambda r: fulfill_json(r, {'sets': []}))

            def sets_route(route):
                req = route.request
                if req.method == 'POST' and req.url.endswith('/compose'):
                    override = state.get('draft_md') or ''
                    if override:
                        fulfill_json(route, {'set': {'set_id': 's1', 'name': 'probe set', 'selections': {}}, 'markdown': override, 'paragraphs_used': 0, 'draft_override': True})
                    else:
                        fulfill_json(route, {'set': {'set_id': 's1', 'name': 'probe set', 'selections': {}}, 'markdown': '## Composed probe\n\n' + 'Checked paragraph as text ' + P_QUOTE + '.', 'paragraphs_used': 1})
                elif req.method == 'POST':
                    fulfill_json(route, {'set_id': 's1'})
                elif req.method == 'PATCH':
                    body = json.loads(req.post_data or '{}')
                    if 'draft_md' in body:
                        state['draft_md'] = body.get('draft_md') or ''
                    fulfill_json(route, {'ok': True, 'set_id': 's1'})
                elif req.method == 'GET' and req.url.endswith('/api/sets'):
                    fulfill_json(route, {'sets': [{'set_id': 's1', 'name': 'probe set', 'runs': 1}]})
                else:
                    fulfill_json(route, {'set': {'set_id': 's1', 'name': 'probe set', 'selections': {}}, 'markdown': '## Composed probe\n\n' + 'Checked paragraph as ' + 'text ' + P_QUOTE + '.', 'paragraphs_used': 1})
            page.route('**/api/sets/**', sets_route)

            def events_route(route):
                req = route.request
                if req.method == 'GET':
                    ev = state.get('events')
                    if ev:
                        fulfill_json(route, {'exists': True, 'run_id': 't1', 'events': ev, 'capped': False})
                    else:
                        fulfill_json(route, {'exists': False, 'run_id': 't1', 'events': [], 'capped': False})
                else:
                    try:
                        body = json.loads(req.post_data or '{}')
                        sent = body.get('events') or []
                    except ValueError:
                        sent = ['<unparseable>']
                    state['events_post'].extend(sent)
                    fulfill_json(route, {'ok': True, 'kept': len(sent), 'refused': 0})
            page.route('**/api/events/**', events_route)
            page.route('**/api/engine', lambda r: fulfill_json(r, ENGINE_STATE))
            page.route('**/api/runs*', lambda r: fulfill_json(r, RUNS_LEDGER))

            def fetchurls_route(route):
                body = json.loads(route.request.post_data or '{}')
                state['fetched_urls'] = body.get('urls') or []
                state['curation'] = CURATION_AFTER
                fulfill_json(route, {'ok': True, 'fetched': 1, 'failed': [{'url': 'https://miss/x', 'error': 'boom'}], 'counts': CURATION_AFTER['counts']})
            page.route('**/api/fetch-urls/**', fetchurls_route)
            page.route('**/api/refine/**', refine_route)
            page.route('**/api/build/**', build_route)
            page.goto(base)
            assert page.title() == TITLE, 'unexpected start title'
            rows = page.locator('#latest-grid .gs-qrow')
            page.wait_for_function("document.querySelectorAll('#latest-grid .gs-qrow').length === 2", timeout=5000)
            assert page.title() == TITLE, 'GS-01: a payload executed through the grouped grid'
            assert rows.nth(0).locator('.q').inner_text() == P_TAG, 'hostile query lost as text in the grouped grid'
            assert rows.nth(0).locator('img, svg').count() == 0, 'grid row rendered a payload element'
            assert 'second row stays text ' + P_QUOTE in rows.nth(1).inner_text(), 'row 2 query text lost'
            rows.nth(0).locator('button.gs-qrow-btn').click()
            panel = rows.nth(0).locator('.gs-qpanel')
            assert panel.is_visible(), 'hostile row did not expand'
            meta = panel.locator('.gs-qmeta').inner_text()
            assert 'kept' in meta.lower() and 'written' in meta.lower(), 'counts lost as text in the expanded panel'
            gsrcs = panel.locator('.gs-qsrcs li')
            assert gsrcs.count() == 2, 'javascript: source must be dropped from grid sources'
            for k in range(gsrcs.count()):
                a = gsrcs.nth(k).locator('a')
                assert a.get_attribute('href') in (P_MOUSE, 'https://safe.test/x'), 'grid source href must round-trip the original string'
            assert page.title() == TITLE, 'GS-01: a payload executed through grid sources'
            modbtn = panel.locator('.gs-qact.ghost')
            assert modbtn.count() == 1, 'modify action missing on the row'
            modbtn.click()
            assert page.evaluate('document.body.dataset.screen') == 'search', 'modify action did not return to the search screen'
            assert page.evaluate("document.getElementById('q').value") == P_TAG, 'modify did not take the question back into the form'
            openbtn = panel.locator('.gs-qact:not(.ghost)')
            assert openbtn.count() == 1, 'open action missing on the row'
            openbtn.click()
            page.wait_for_function("document.body.dataset.screen === 'results'", timeout=10000)
            page.wait_for_function("document.getElementById('barq').value !== ''", timeout=15000)
            assert page.evaluate("document.getElementById('barq').value") == P_TAG, "open-in-station lost the run's question in the results bar"
            assert page.title() == TITLE, 'GS-01: a payload executed through reopen-by-id'
            page.click('#gr-pill')
            page.wait_for_function("document.getElementById('gr-pillmenu').classList.contains('open')", timeout=5000)
            page.click('#gr-pillmenu button')
            page.wait_for_function("document.body.dataset.screen === 'search'", timeout=5000)
            page.evaluate("document.getElementById('res').style.display = 'none';document.getElementById('brief').textContent = '';")
            print('PASS  grouped grid: hostile rows stay text; expand, modify, open all work')
            page.fill('#q', 'hostile metadata drill')
            page.click('#go')
            page.wait_for_function("!document.getElementById('go').disabled", timeout=10000)
            assert "Couldn't start the run" in page.inner_text('#formhint'), 'submit error hint missing from the form-level #formhint sink'
            assert page.inner_text('#formhint') != '', 'hint must be visible text'
            print('PASS  GS-03: submit control restored after server error')
            state['research_status'] = 200
            page.fill('#q', 'hostile metadata drill')
            page.click('#go')
            page.wait_for_function("document.getElementById('res').style.display === 'block'", timeout=15000)
            assert page.title() == TITLE, 'GS-01: a payload executed during result painting'
            assert P_TAG in page.inner_text('#brief'), 'guide preview payload lost from #brief text'
            assert page.locator('#brief img').count() == 0, 'preview rendered as markup'
            srcs = page.locator('#srcs li')
            assert srcs.count() == 3, 'expected 3 source rows, got %d' % srcs.count()
            assert srcs.nth(0).locator('a').get_attribute('href') is None, 'javascript: URL rendered with a clickable href'
            assert srcs.nth(0).inner_text() == P_JSURL, 'refused URL text lost'
            assert srcs.nth(2).locator('a').get_attribute('href') == 'https://safe.test/page', 'clean URL lost its link'
            assert srcs.nth(1).locator('a').inner_text() == P_MOUSE, 'quote-bearing URL text lost'
            imgs = page.locator('#imgs .imgc')
            assert imgs.count() == 2, 'expected 2 image cards'
            bad_img_src = imgs.nth(0).locator('img').get_attribute('src') or ''
            assert '"' not in bad_img_src, 'image path broke out of src: %r' % bad_img_src
            assert imgs.nth(0).inner_text() == P_TAG + '\n' + P_QUOTE, 'image metadata lost as text'
            assert imgs.nth(1).inner_text().startswith('clean title')
            boxes = page.locator('#researchtext .rtsrc')
            assert boxes.count() == 2, 'expected 2 live-text boxes'
            assert boxes.nth(0).locator('h4').inner_text() == P_TAG, 'page title lost as text'
            assert boxes.nth(0).locator('p').count() == 1, 'paragraph dropped by gate'
            src_line = boxes.nth(1).locator('.src')
            assert src_line.locator('a').get_attribute('href') is None, 'javascript: page URL rendered clickable in live text'
            assert src_line.locator('a').inner_text() == P_JSURL, 'refused page URL text lost'
            assert page.title() == TITLE, 'GS-01: a payload executed after result painting'
            print('PASS  GS-01 sinks: no execution; text + link allowlist hold')
            page.wait_for_function("!document.getElementById('curate').hidden", timeout=10000)
            assert 'read 2 pages' in page.inner_text('#cur-meta'), 'curation meta counts lost'
            assert P_TAG in page.inner_text('#cur-paras h4'), 'curation page title lost as text'
            assert P_TAG in page.inner_text('#cur-paras .gs-curtext'), 'curation paragraph payload lost as text'
            assert P_QUOTE in page.inner_text('#cur-paras'), 'curation verdict reason lost'
            crows = page.locator('#cur-paras .gs-currow')
            assert crows.count() == 1, 'paragraph rows wrong: %d' % crows.count()
            assert crows.nth(0).get_attribute('aria-checked') == 'false', 'paragraph row must start unselected'
            kws = page.locator('#cur-kws .gs-kw')
            assert kws.count() == 4, 'keyword chips wrong: %d' % kws.count()
            cimgs = page.locator('#cur-imgs .gs-imgck')
            assert cimgs.count() == 2, 'curation image cards wrong: %d' % cimgs.count()
            crows.nth(0).click()
            page.wait_for_function("document.getElementById('cur-count').textContent" + ".indexOf('1 paragraph ·') === 0", timeout=5000)
            assert page.title() == TITLE, 'GS-01: a payload executed through the curation panel'
            print('PASS  curation: verdict + count line; text-only sinks hold')
            page.wait_for_function("!document.getElementById('engines-pick').hidden", timeout=10000)
            assert page.title() == TITLE, 'GS-01: a payload executed in the engine picker'
            page.click('#eng-more summary')
            assert P_TAG in page.inner_text('#eng-groups'), 'hostile engine name lost from the expander as text'
            assert page.locator('#eng-groups img').count() == 0, 'hostile engine name rendered as markup'
            core_chips = page.locator('#eng-core .gs-kw input')
            assert core_chips.count() >= 3, 'core engine chips missing: %d' % core_chips.count()
            strip_txt = page.inner_text('#engine-facts')
            assert P_QUOTE in strip_txt, 'search note lost as text'
            assert P_TAG in strip_txt, 'browser lane fact lost as text'
            assert page.locator('#engine-facts dt').count() >= 3, 'engine strip lost its facts'
            print('PASS  engine picker + strip: hostile catalog stays text')
            page.click("button[data-curtab='unscraped']")
            urows = page.locator('#cur-unscraped .gs-cururow')
            assert urows.count() == 3, 'unscraped rows wrong: %d' % urows.count()
            assert urows.nth(0).locator('a').count() == 0, 'javascript: candidate rendered clickable'
            assert P_JSURL in urows.nth(0).inner_text(), 'refused candidate text lost'
            assert urows.nth(1).locator('a').get_attribute('href') == 'https://good.test/openme', 'clean candidate link lost'
            assert P_TAG in page.inner_text('#cur-unscraped'), 'hostile candidate text lost'
            assert P_QUOTE in page.inner_text('#cur-unscraped'), 'candidate reason text lost'
            fbtn = page.locator('[data-fetchu]')
            assert fbtn.is_disabled(), 'Fetch selected must start disabled when nothing is picked'
            urows.nth(1).click()
            assert fbtn.is_enabled(), 'picking a candidate should arm the Fetch button'
            state['events'] = HOSTILE_EVENTS
            page.click('[data-fetchu]')
            page.wait_for_function("document.getElementById('savemsg').textContent" + ".indexOf('Fetched 1 page') === 0", timeout=10000)
            assert state['fetched_urls'] == ['https://good.test/openme'], 'fetch-urls body lost the picked URL: %r' % state['fetched_urls']
            assert 'read 3 pages' in page.inner_text('#cur-meta'), 'second-wave page missing from the refreshed record'
            assert page.locator('#cur-unscraped .gs-cururow').count() == 2, 'fetched URL did not leave the candidate list'
            assert page.title() == TITLE, 'GS-01: a payload executed through the second-wave flow'
            print('PASS  second wave: pick, fetch, record grows, list shrinks')
            page.wait_for_function("document.getElementById('cur-count').textContent" + ".trim() === '1 paragraph · 1 keyword · 0 images checked'", timeout=10000)
            crows2 = page.locator('#cur-paras .gs-currow')
            assert crows2.nth(0).get_attribute('aria-checked') == 'true', 'the replayed paragraph pick did not reach its row'
            assert crows2.nth(1).get_attribute('aria-checked') == 'false', 'a pre-save-marker or url-missed event reached a row'
            print('PASS  events replay: save boundary + absolute states, hostile pid/uurl/img keys inert')
            page.wait_for_timeout(700)
            posted = json.dumps(state['events_post'])
            assert any((e.get('type') == 'para' and e.get('pid') == 'pg0#p0' and (e.get('on') is True) and (e.get('u') == P_MOUSE) for e in state['events_post'])), 'the paragraph pick was not journaled with pid + url: %r' % state['events_post']
            assert any((e.get('type') == 'uurl' and e.get('u') == 'https://good.test/openme' and (e.get('on') is True) for e in state['events_post'])), 'the candidate arm was not journaled: %r' % state['events_post']
            assert P_JSURL not in posted and 'onerror=' not in posted, 'hostile url/keys were re-posted by the client'
            print('PASS  events emit: picks journaled with pid+u; no hostile values re-posted')
            if page.evaluate("document.getElementById('outrail').classList.contains('open')"):
                page.keyboard.press('Escape')
                page.wait_for_function("!document.getElementById('outrail').classList.contains('open') && !document.getElementById('outrail').classList.contains('preopen')", timeout=5000)
            page.fill('#refineq', 'narrow it')
            page.click('#refinego')
            page.wait_for_function("!document.getElementById('refinego').disabled", timeout=10000)
            assert 'Refine failed' in page.inner_text('#refineout')
            print('PASS  GS-03: refine control restored after server error')
            state['refine_status'] = 200
            page.click('#refinego')
            page.wait_for_function("!document.getElementById('refinego').disabled", timeout=10000)
            out_html = page.inner_html('#refineout')
            assert '<img' not in out_html and '<svg' not in out_html, 'liteMd let raw markup through'
            assert P_TAG in page.inner_text('#refineout'), 'refine text lost as text'
            sugg = page.locator('#imgs .imgc')
            assert sugg.count() == 3, 'suggestion card missing'
            assert sugg.nth(2).locator('img').get_attribute('src') == '/imgcand/t/sug.png', 'suggestion path mangled'
            assert 'para 1' in sugg.nth(2).inner_text(), 'suggestion label lost'
            assert page.title() == TITLE, 'GS-01: a payload executed through the refine path'
            print('PASS  GS-01 refine: liteMd neutralizes tags; cards text-safe')
            page.click('#drawbtn', timeout=5000)
            page.wait_for_function("document.getElementById('outrail').classList.contains('open')", timeout=5000)
            page.click('#savebtn')
            page.wait_for_function("document.body.classList.contains('gs-saved')", timeout=10000)
            assert 'Saved' in page.inner_text('#savemsg'), 'save message lost'
            assert page.locator('#pubmenu').is_visible(), 'saving research did not reveal Publish in the top menu'
            page.locator('#outactions button', has_text='edit markdown').first.click()
            page.wait_for_function("!document.getElementById('outedit').hidden", timeout=10000)
            page.fill('#outedit', 'Hand edit carrying ' + P_TAG + ' and ' + P_QUOTE + ' inside.')
            page.locator('#outactions button', has_text='apply edits').first.click()
            page.wait_for_function("(document.getElementById('savemsg').textContent || '')" + ".indexOf('Edit saved') === 0", timeout=10000)
            assert state.get('draft_md') == 'Hand edit carrying ' + P_TAG + ' and ' + P_QUOTE + ' inside.', 'PATCH draft_md did not carry the raw text: %r' % state.get('draft_md')
            od_html = page.inner_html('#outdoc')
            assert '<img' not in od_html and '<svg' not in od_html, 'liteMd let the hand edit render as markup'
            assert P_TAG in page.inner_text('#outdoc'), 'hand-edit payload lost from the rendered draft as text'
            assert page.title() == TITLE, 'GS-01: a payload executed through the draft editor'
            print('PASS  draft editor: hostile override stays text, held verbatim')
            page.click('#pubmenu')
            page.wait_for_function("document.getElementById('pubrail').classList" + ".contains('open')", timeout=5000)
            assert page.locator('#pubrail').is_visible(), 'publish drawer did not open from the top menu'
            print('PASS  staging: Publish lives in the top menu, drawer opens')
            page.click('#build')
            page.wait_for_function("!document.getElementById('build').disabled", timeout=10000)
            assert 'Build failed' in page.inner_text('#built')
            state['build_status'] = 200
            page.click('#build')
            page.wait_for_function('document.getElementById("built").textContent.includes("Guardrails 1/2")', timeout=10000)
            links = page.locator('#built a')
            assert links.count() == 3, 'expected Open it / zip / PDF links'
            assert links.nth(0).get_attribute('href') == '/site/d/sites/site-x/index.html'
            assert links.nth(1).get_attribute('download') is not None
            assert page.title() == TITLE, 'GS-01: a payload executed through the build path'
            print('PASS  GS-03 build: restore on error; success anchors DOM-built')
            browser.close()
            print('ALL PASS: portal XSS + control-restore fixtures (findings GS-01, GS-03)')
            return 0
    finally:
        server.shutdown()

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
