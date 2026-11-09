"""`voc-detector demo`: live readings in a web browser, for showing the client.

Starts a small web server on the Pi. On any phone/laptop on the SAME Wi-Fi,
open  http://<pi-name>.local:8000  (the command prints the exact address).

  /          live page: big numbers + last-10-minutes charts, and buttons to
             mark when each item is brought close (saved as a DEMO session,
             so `voc-detector evidence` can include the demo afterwards)
  /report    the evidence report (from `voc-detector evidence`)
  /data.csv  its raw data

Read-only apart from the marker buttons. Stop with Ctrl+C.
"""

import datetime as dt
import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .storage import Storage

LIVE_KEYS = ["ens_tvoc", "sgp_voc_index", "ens_eco2", "temp_c", "rh"]
MARKS = {"compound": "compound", "perfume": "confounder:perfume",
         "alcohol": "confounder:isopropyl_alcohol", "other": "confounder:other"}


def _ips():
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ips)


def serve(db_path, evidence_dir, port=8000):
    store = Storage(db_path)
    session = "DEMO-" + dt.datetime.now().strftime("%Y%m%d-%H%M")
    state = {"open": None}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(200, LIVE_PAGE)
            if u.path == "/api/live":
                since = float(parse_qs(u.query).get("since", ["0"])[0] or 0)
                since = max(since, time.time() - 600)
                rows = [{"t": r["ts"], **{k: r[k] for k in LIVE_KEYS}}
                        for r in store.iter_samples(since + 0.001)]
                return self._send(200, json.dumps({"rows": rows, "open": state["open"]}),
                                  "application/json")
            if u.path in ("/report", "/data.csv"):
                name = "report.html" if u.path == "/report" else "data.csv"
                p = os.path.join(evidence_dir, name)
                if not os.path.exists(p):
                    return self._send(404, "No report yet: run  voc-detector evidence  on the Pi first.")
                with open(p, "rb") as f:
                    body = f.read()
                return self._send(200, body, "text/html; charset=utf-8" if name.endswith("html")
                                  else "text/csv")
            return self._send(404, "not found")

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/mark":
                return self._send(404, "not found")
            what = parse_qs(u.query).get("what", [""])[0]
            if state["open"]:
                store.end_label(state["open"]["id"])
                state["open"] = None
            if what in MARKS:
                lid = store.start_label(MARKS[what], session, f"live demo: {what}")
                state["open"] = {"id": lid, "what": what, "since": time.time()}
            return self._send(200, json.dumps({"open": state["open"]}), "application/json")

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    host = socket.gethostname()
    print(f"Live demo running. On a phone/laptop on the SAME Wi-Fi as the Pi, open:")
    print(f"   http://{host}.local:{port}")
    for ip in _ips():
        print(f"   http://{ip}:{port}")
    print("Evidence report:  .../report     Stop with Ctrl+C.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if state["open"]:
            store.end_label(state["open"]["id"])
        srv.server_close()


def watch(db_path):
    """Terminal live view (works in the Raspberry Pi Connect shell)."""
    store = Storage(db_path)
    base = None
    print("Live readings. Changes are vs. the first 20 seconds. Ctrl+C to stop.\n")
    print(f"{'time':<9}{'TVOC ppb':>14}{'VOC index':>14}{'eCO2 ppm':>14}")
    first, last_ts = [], 0
    try:
        while True:
            r = store.db.execute("SELECT ts, ens_tvoc, sgp_voc_index, ens_eco2 FROM samples "
                                 "ORDER BY ts DESC LIMIT 1").fetchone()
            if r and r[0] > last_ts:
                last_ts = r[0]
                if base is None:
                    first.append(r[1:])
                    if len(first) >= 20:
                        base = [sum(x[i] or 0 for x in first) / len(first) for i in range(3)]
                cells = []
                for i, v in enumerate(r[1:]):
                    if v is None:
                        cells.append(f"{'-':>14}")
                    elif base is None:
                        cells.append(f"{v:>14.0f}")
                    else:
                        cells.append(f"{f'{v:.0f} ({v - base[i]:+.0f})':>14}")
                print(f"{dt.datetime.fromtimestamp(r[0]).strftime('%H:%M:%S'):<9}{''.join(cells)}", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()


LIVE_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live Sensor Demo</title>
<style>
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--line:#2a78d6;
--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--line:#3987e5;--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0}.sub{color:var(--muted);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--surface);border-radius:12px;padding:12px}.tile .k{color:var(--muted);font-size:12px}
.tile .v{font-size:34px;font-weight:650;font-variant-numeric:tabular-nums}.tile .d{font-size:14px;color:var(--ink2);font-variant-numeric:tabular-nums}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 4px}button{font:inherit;border-radius:10px;padding:9px 14px;border:1px solid var(--grid);background:var(--surface);color:var(--ink);cursor:pointer}
button.on{outline:3px solid var(--line)}#status{font-size:13px;color:var(--ink2);min-height:20px}
.chart{background:var(--surface);border-radius:12px;padding:10px;margin-top:10px}.chart h2{font-size:14px;margin:0 0 4px}
canvas{width:100%;height:190px;display:block}.foot{margin-top:16px;font-size:13px}a{color:var(--line)}
.legend{font-size:12px;color:var(--ink2);display:flex;gap:14px;flex-wrap:wrap;margin-top:4px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;opacity:.6}
</style></head><body><main>
<h1>Live sensor readings</h1>
<div class="sub">Updated every second &middot; change shown against the baseline (press "Reset baseline" in clean air)</div>
<div class="tiles">
 <div class="tile"><div class="k">Total VOC (ENS160)</div><div class="v" id="v0">-</div><div class="d" id="d0"></div></div>
 <div class="tile"><div class="k">VOC index (SGP40)</div><div class="v" id="v1">-</div><div class="d" id="d1"></div></div>
 <div class="tile"><div class="k">eCO2 (ENS160)</div><div class="v" id="v2">-</div><div class="d" id="d2"></div></div>
 <div class="tile"><div class="k">Temperature / humidity</div><div class="v" id="v3">-</div><div class="d" id="d3"></div></div>
</div>
<div class="btns">
 <button data-m="compound">Compound placed</button>
 <button data-m="alcohol">Isopropyl alcohol placed</button>
 <button data-m="perfume">Perfume placed</button>
 <button data-m="">Item removed</button>
 <button id="reset">Reset baseline</button>
</div>
<div id="status"></div>
<div class="legend"><span><i style="background:var(--c1)"></i>compound</span><span><i style="background:var(--c2)"></i>isopropyl alcohol</span><span><i style="background:var(--c3)"></i>perfume</span></div>
<div class="chart"><h2>Total VOC, last 10 minutes (ppb)</h2><canvas id="c0"></canvas></div>
<div class="chart"><h2>VOC index, last 10 minutes</h2><canvas id="c1"></canvas></div>
<div class="foot"><a href="/report" target="_blank">Open the full results report</a> &middot; <a href="/data.csv">raw data (CSV)</a></div>
</main>
<script>
"use strict";
const rows=[]; let since=0, base=null, marks=[], open=null;
const COL={compound:"--c1",alcohol:"--c2",perfume:"--c3",other:"--c4"};
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const $=id=>document.getElementById(id);
function avg(k,n){const r=rows.slice(-n).map(x=>x[k]).filter(v=>v!=null);return r.length?r.reduce((a,b)=>a+b,0)/r.length:null}
function setBase(){base={tvoc:avg("ens_tvoc",20),voc:avg("sgp_voc_index",20),eco2:avg("ens_eco2",20)}}
$("reset").onclick=setBase;
document.querySelectorAll("button[data-m]").forEach(b=>b.onclick=async()=>{
  const r=await fetch("/api/mark?what="+b.dataset.m,{method:"POST"});const j=await r.json();
  const now=Date.now()/1000; if(open){open.end=now} open=null;
  if(j.open){open={what:j.open.what,start:now,end:null};marks.push(open)}
  document.querySelectorAll("button[data-m]").forEach(x=>x.classList.toggle("on",!!j.open&&x.dataset.m===j.open.what));
  $("status").textContent=j.open?("Marked: "+j.open.what+" placed at "+new Date().toLocaleTimeString()):"Marked: item removed";
});
function fmt(v,d=0){return v==null?"-":v.toFixed(d)}
function delta(v,b){if(v==null||b==null)return "";const d=v-b;return (d>=0?"+":"")+d.toFixed(0)+" vs baseline"}
function draw(id,key){
  const c=$(id),dpr=window.devicePixelRatio||1,W=c.clientWidth,H=c.clientHeight;c.width=W*dpr;c.height=H*dpr;
  const g=c.getContext("2d");g.scale(dpr,dpr);g.clearRect(0,0,W,H);
  const now=Date.now()/1000,t0=now-600,ml=48,mb=18,pw=W-ml-6,ph=H-mb-6;
  const pts=rows.filter(r=>r.t>=t0&&r[key]!=null);
  let hi=Math.max(10,...pts.map(r=>r[key]))*1.1;
  {const m=Math.pow(10,Math.floor(Math.log10(hi/4)));const st=[1,2,2.5,5,10].map(k=>k*m).find(k=>k*4>=hi);hi=st*4}
  const X=t=>ml+(t-t0)/600*pw,Y=v=>6+ph-v/hi*ph;
  for(const m of marks){const a=Math.max(m.start,t0),b=m.end||now;if(b<t0)continue;g.globalAlpha=.22;g.fillStyle=css(COL[m.what]||"--c4");g.fillRect(X(a),6,Math.max(2,X(b)-X(a)),ph);g.globalAlpha=1}
  g.strokeStyle=css("--grid");g.fillStyle=css("--muted");g.font="11px system-ui";g.textAlign="right";
  for(let i=0;i<=4;i++){const v=hi*i/4,y=Y(v);g.beginPath();g.moveTo(ml,y);g.lineTo(ml+pw,y);g.stroke();g.fillText(Number.isInteger(v)?v.toFixed(0):v.toFixed(1),ml-6,y+4)}
  for(let m=10;m>=0;m-=2){g.textAlign=m===10?"left":m===0?"right":"center";g.fillText(m?("-"+m+" min"):"now",X(now-m*60),H-4)}
  g.strokeStyle=css("--line");g.lineWidth=2;g.beginPath();let pen=false,lt=0;
  for(const r of pts){const x=X(r.t),y=Y(r[key]);if(pen&&r.t-lt<10)g.lineTo(x,y);else g.moveTo(x,y);pen=true;lt=r.t}
  g.stroke();
}
async function tick(){
  try{const r=await fetch("/api/live?since="+since);const j=await r.json();
    for(const x of j.rows){rows.push(x);since=x.t}
    while(rows.length&&rows[0].t<Date.now()/1000-600)rows.shift();
    if(!base&&rows.length>=20)setBase();
    const L=rows[rows.length-1]||{};
    $("v0").textContent=fmt(L.ens_tvoc);$("d0").textContent=base?delta(L.ens_tvoc,base.tvoc):"";
    $("v1").textContent=fmt(L.sgp_voc_index);$("d1").textContent=base?delta(L.sgp_voc_index,base.voc):"";
    $("v2").textContent=fmt(L.ens_eco2);$("d2").textContent=base?delta(L.ens_eco2,base.eco2):"";
    $("v3").textContent=fmt(L.temp_c,1)+"°C";$("d3").textContent=fmt(L.rh)+"% RH";
    draw("c0","ens_tvoc");draw("c1","sgp_voc_index");
  }catch(e){$("status").textContent="Lost connection to the Pi - retrying..."}
}
setInterval(tick,1000);tick();
</script></body></html>"""
