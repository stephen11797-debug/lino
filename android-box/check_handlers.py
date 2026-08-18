#!/usr/bin/env python3
"""Check every button's onclick handler is defined + wired, across all pages.

Catches ReferenceError bugs (handler not defined) without triggering
destructive media flows.
"""
import asyncio
import json
import sys
import time
import websockets

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
            if method == 'Runtime.consoleAPICalled' and msg.get('params', {}).get('type') == 'error':
                args = [a.get('value', a.get('description', '')) for a in msg.get('params', {}).get('args', [])]
                self.events.append('CONSOLEERR: ' + ' '.join(str(x) for x in args))
            elif method == 'Log.entryAdded' and msg.get('params', {}).get('entry', {}).get('level') == 'error':
                self.events.append('LOGERR: ' + msg['params']['entry'].get('text', ''))

    async def send(self, method, params=None, timeout=8):
        _id_seq[0] += 1
        mid = _id_seq[0]
        fut = self._loop.create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(mid, None)
            return {'__timeout__': True}

    async def evaluate(self, expr):
        r = await self.send('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
        if r.get('__timeout__'):
            return {'__timeout__': True}
        res = r.get('result', {})
        if res.get('exceptionDetails'):
            return {'__error__': json.dumps(res['exceptionDetails'])[:400]}
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

        failures = []
        for page in PAGES:
            await cdp.evaluate(f"switchPage('{page}')")
            await asyncio.sleep(0.4)
            btns = await cdp.evaluate("""
            (function(){
              const out = [];
              const els = document.querySelectorAll('button, [onclick], a[onclick]');
              for (const el of els) {
                const oc = el.getAttribute('onclick');
                if (!oc) continue;
                out.push({text: (el.textContent||'').trim().slice(0,40),
                          onclick: oc.trim(), visible: el.getBoundingClientRect().width>0});
              }
              return out;
            })()
            """)
            if isinstance(btns, dict):
                print(f"[{page}] read error: {btns}")
                continue
            seen = set(); unique = []
            for b in btns:
                k = (b['onclick'], b['text'])
                if k in seen: continue
                seen.add(k); unique.append(b)
            print(f"===== PAGE {page}: {len(unique)} unique onclick handlers =====")
            for b in unique:
                ident = (b['text'] or b['onclick'])[:40]
                expr = f"""
                (function(){{
                  const onclick = {json.dumps(b['onclick'])};
                  const m = onclick.match(/^([a-zA-Z_$][\\w$]*)/);
                  const name = m ? m[1] : null;
                  if (!name) return {{ok:true, kind:'inline', expr:onclick.slice(0,60)}};
                  let fn = 'undefined';
                  try {{ fn = typeof eval(name); }} catch(e) {{ return {{ok:false, why:'handler not defined: ' + name}}; }}
                  if (fn === 'undefined') return {{ok:false, why:'handler not defined: ' + name}};
                  let wired = false;
                  document.querySelectorAll('[onclick]').forEach(el => {{
                    if ((el.getAttribute('onclick')||'').trim() === onclick) wired = true;
                  }});
                  return {{ok:true, kind:'function', name, wired}};
                }})()
                """
                r = await cdp.evaluate(expr)
                if isinstance(r, dict) and r.get('__timeout__'):
                    print(f"  TIMEOUT {ident}")
                    failures.append((page, b, 'TIMEOUT'))
                    continue
                if isinstance(r, dict) and r.get('__error__'):
                    print(f"  FAIL {ident:45} eval-error {r['__error__'][:80]}")
                    failures.append((page, b, 'EVAL'))
                    continue
                if isinstance(r, dict) and not r.get('ok'):
                    print(f"  FAIL {ident:45} {r.get('why','?')}")
                    failures.append((page, b, r.get('why', '?')))
                else:
                    print(f"  ok   {ident:45} [{r.get('kind')}]{' wired' if r.get('wired') else ''}")
        reader.cancel()
        print()
        print("=" * 60)
        print(f"TOTAL FAILURES: {len(failures)}")
        for pg, b, why in failures:
            print(f"  [{pg}] {(b['text'] or b['onclick'])[:50]} -> {why}")


if __name__ == '__main__':
    asyncio.run(run())
