#!/usr/bin/env python3
"""Drive the Stepjens Studio WebView via CDP and test every button on every page."""
import asyncio
import json
import sys
import time
import websockets

RESULTS = []
WS_URL = 'ws://localhost:9222/devtools/page/0BFC13B2CF97101E935BF8FCA270E7CE'

PAGES = ['studio', 'script', 'thumbnail', 'imagegen', 'stems', 'whisper', 'chord', 'ytdl']

_id_seq = [0]


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.pending = {}
        self.events = []
        self._loop = asyncio.get_event_loop()

    async def run(self):
        while True:
            try:
                msg = json.loads(await self.ws.recv())
            except Exception:
                return
            mid = msg.get('id')
            if mid is not None and mid in self.pending:
                fut = self.pending.pop(mid)
                if not fut.done():
                    fut.set_result(msg)
                continue
            method = msg.get('method')
            p = msg.get('params', {})
            if method == 'Runtime.exceptionThrown':
                d = p.get('exceptionDetails', {})
                self.events.append(('EXC', d.get('text', ''), d.get('url', ''), d.get('lineNumber', -1)))
            elif method == 'Log.entryAdded':
                e = p.get('entry', {})
                if e.get('level') == 'error':
                    self.events.append(('LOGERR', e.get('text', ''), e.get('url', ''), e.get('lineNumber', -1)))
            elif method == 'Runtime.consoleAPICalled':
                if p.get('type') == 'error':
                    args = [a.get('value', a.get('description', '')) for a in p.get('args', [])]
                    self.events.append(('CONSOLEERR', ' '.join(str(x) for x in args), '', -1))

    async def send(self, method, params=None, timeout=8):
        _id_seq[0] += 1
        mid = _id_seq[0]
        fut = self._loop.create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        try:
            r = await asyncio.wait_for(fut, timeout)
            return r
        except asyncio.TimeoutError:
            self.pending.pop(mid, None)
            return {'__timeout__': True}

    async def evaluate(self, expr, debug=False):
        r = await self.send('Runtime.evaluate', {
            'expression': expr,
            'returnByValue': True,
            'awaitPromise': True,
        })
        if r.get('__timeout__'):
            return {'__timeout__': True}
        if debug:
            print('DEBUG evaluate raw:', json.dumps(r)[:300])
        res = r.get('result', {})
        if res.get('exceptionDetails'):
            return {'__error__': json.dumps(res['exceptionDetails'])[:500]}
        return res.get('result', {}).get('value')

    def drain(self):
        out = [e for e in self.events]
        self.events.clear()
        return out


async def run():
    url = sys.argv[1] if len(sys.argv) > 1 else WS_URL
    async with websockets.connect(url) as ws:
        cdp = CDP(ws)
        reader = asyncio.create_task(cdp.run())
        await cdp.send('Runtime.enable')
        await cdp.send('Log.enable')

        for page in PAGES:
            await cdp.evaluate(f"switchPage('{page}')")
            await asyncio.sleep(0.5)
            btns = None
            for _try in range(3):
                btns = await cdp.evaluate("""
            (function(){
              const out = [];
              const els = document.querySelectorAll('button, [onclick], a[onclick]');              for (const el of els) {
                const oc = el.getAttribute('onclick');
                if (!oc) continue;
                const r = el.getBoundingClientRect();
                const visible = r.width > 0 && r.height > 0;
                out.push({tag: el.tagName, id: el.id || '',
                  text: (el.textContent||'').trim().slice(0,40),
                  onclick: oc.trim().slice(0,120), visible,
                  x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)});
              }
              return out;
            })()
            """)
                if isinstance(btns, list) and btns:
                    break
                await asyncio.sleep(0.5)
            if isinstance(btns, dict):
                print(f"[{page}] ERROR reading buttons: {btns}")
                continue
            seen = set()
            unique = []
            for b in btns:
                k = (b['onclick'], b['text'])
                if k in seen:
                    continue
                seen.add(k)
                unique.append(b)
            print(f"===== PAGE {page}: {len(unique)} buttons =====")
            for b in unique:
                ident = (b['text'] or b['id'] or b['onclick'])[:40]
                expr = f"""
                (async function(){{
                  const r = {{}};
                  const els = document.querySelectorAll('button, [onclick], a[onclick]');
                  let target = null;
                  for (const el of els) {{
                    const oc = el.getAttribute('onclick');
                    const t = (el.textContent||'').trim();
                    if (oc && oc.trim() === {json.dumps(b['onclick'])} && (t === {json.dumps(b['text'])} || (b===undefined))) {{ target = el; }}
                  }}
                  if (!target) {{ // fallback: match onclick only, first visible
                    for (const el of els) {{
                      const oc = el.getAttribute('onclick');
                      if (oc && oc.trim() === {json.dumps(b['onclick'])}) {{ const r2 = el.getBoundingClientRect(); if (r2.width>0) {{ target = el; break; }} }}
                    }}
                  }}
                  if (!target) return {{ok:false, why:'button not found'}};
                  window.__errs = [];
                  const oe = window.onerror;
                  window.onerror = function(m,s,l) {{ window.__errs.push('onerror:'+m+'@'+l); return true; }};
                  const origAlert=window.alert, origConfirm=window.confirm, origPrompt=window.prompt;
                  window.alert=function(m){{ r.alert=String(m).slice(0,80); return true; }};
                  window.confirm=function(m){{ r.confirm=String(m).slice(0,80); return true; }};
                  window.prompt=function(m){{ r.prompt=String(m).slice(0,80); return 'test'; }};
                  try {{ target.click(); r.clicked=true; }} catch(e) {{ r.clicked=false; r.err=String(e); }}
                  await new Promise(res => setTimeout(res, 400));
                  r.errs = window.__errs;
                  r.activePage = (document.querySelector('.page.active')||{{}}).id || '';
                  window.onerror = oe;
                  window.alert=origAlert; window.confirm=origConfirm; window.prompt=origPrompt;
                  return r;
                }})()
                """
                r = await cdp.evaluate(expr)
                if isinstance(r, dict) and r.get('__timeout__'):
                    print(f"  HANG {ident:45} (click blocked, likely media/permission flow)")
                    RESULTS.append((page, b, 'HANG'))
                    continue
                if isinstance(r, dict) and r.get('__error__'):
                    print(f"  FAIL {ident:45} eval-error: {r['__error__'][:120]}")
                    RESULTS.append((page, b, 'FAIL-eval'))
                    continue
                errs = (r or {}).get('errs') or []
                exc_here = cdp.drain()
                ok = (r or {}).get('clicked', False) and not errs and not exc_here
                extra = f" [alert: {r['alert']}]" if r and r.get('alert') else ''
                if not ok:
                    why = []
                    if not (r or {}).get('clicked'):
                        why.append('click failed')
                    if errs:
                        why.append('errored: ' + '; '.join(errs)[:100])
                    if exc_here:
                        why.append('exc: ' + '; '.join(f"{e[0]}:{e[1]}" for e in exc_here[:2])[:100])
                    print(f"  FAIL {ident:45} {'; '.join(why)}{extra}")
                    RESULTS.append((page, b, 'FAIL'))
                else:
                    print(f"  ok   {ident:45}{extra}")
        reader.cancel()
        try:
            await reader
        except Exception:
            pass


if __name__ == '__main__':
    asyncio.run(run())
