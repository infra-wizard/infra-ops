"""
web_server.py
-------------
Serves the cert-expiry dashboard UI on port 8080.
Runs alongside cert_expiry_alert.py in the same pod (started by entrypoint.sh).

Endpoints:
  GET  /              → dashboard HTML
  GET  /api/scan      → run scan now, return JSON
  GET  /api/status    → last scan result JSON
  GET  /api/data      → raw CSV rows as JSON
  POST /api/upload    → upload new CSV, replace in-memory data
"""

import csv
import io
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

DATA_FILE    = os.environ.get("DATA_FILE", "/data/certs.csv")
WARN_DAYS    = int(os.environ.get("WARN_DAYS", "30"))
DATE_COLUMNS = [c.strip() for c in os.environ.get(
    "DATE_COLUMNS",
    "Ingress-cert,SSO-cert,AKS Version- End of life,PTC License,ProvisioningKe,emessagekey,TWX_AppKey"
).split(",")]
ID_COLUMNS = [c.strip() for c in os.environ.get(
    "IDENTITY_COLUMNS",
    "TWX_AppKey,Azure AD -Client ID,Logtools B2C- Client ID"
).split(",")]

_lock        = threading.Lock()
_last_result = {"scanned_at": None, "alerts": [], "rows": [], "summary": {}}


def load_csv(filepath):
    p = Path(filepath)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


def parse_date(val):
    if not val or val.strip().lower() in ("", "nan", "n/a", "none"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def row_identity(row):
    for col in ID_COLUMNS:
        v = (row.get(col) or "").strip()
        if v and v.lower() not in ("nan", "none", ""):
            return v
    return "unknown"


def run_scan(rows, warn_days):
    today    = datetime.now().date()
    deadline = today + timedelta(days=warn_days)
    alerts   = []
    existing = [c for c in DATE_COLUMNS if any(c in r for r in rows)]

    for row in rows:
        identity = row_identity(row)
        for col in existing:
            d = parse_date(row.get(col, ""))
            if not d:
                continue
            days_left = (d - today).days
            if d <= deadline:
                alerts.append({
                    "identity"   : identity,
                    "column"     : col,
                    "expiry_date": str(d),
                    "days_left"  : days_left,
                    "status"     : "EXPIRED" if d < today else "EXPIRING SOON",
                    "client_id"  : row.get("Azure AD -Client ID", ""),
                    "sheet"      : row.get("_sheet", "Global"),
                })
    expired = sum(1 for a in alerts if a["status"] == "EXPIRED")
    return {
        "scanned_at": datetime.now().isoformat(),
        "warn_days" : warn_days,
        "alerts"    : alerts,
        "rows"      : rows,
        "summary"   : {
            "total"         : len(rows),
            "expired"       : expired,
            "expiring_soon" : len(alerts) - expired,
            "healthy"       : max(0, len(rows) - len({a["identity"] for a in alerts})),
        }
    }


def do_scan():
    rows = load_csv(DATA_FILE)
    result = run_scan(rows, WARN_DAYS)
    with _lock:
        _last_result.update(result)
    log.info("[web] Scan complete — %d alert(s)", len(result["alerts"]))
    return result


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cert Expiry Monitor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f7;color:#1d1d1f;min-height:100vh}
.topbar{background:#1d1d1f;color:#fff;padding:0 2rem;height:52px;display:flex;align-items:center;justify-content:space-between}
.topbar h1{font-size:15px;font-weight:500;display:flex;align-items:center;gap:8px}
.topbar .right{display:flex;align-items:center;gap:12px;font-size:13px;color:#aaa}
.content{max-width:1100px;margin:0 auto;padding:1.5rem}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:1.5rem}
.metric{background:#fff;border-radius:12px;padding:1rem 1.25rem;border:0.5px solid #e0e0e0}
.metric-label{font-size:12px;color:#888;margin-bottom:4px}
.metric-value{font-size:26px;font-weight:500}
.red{color:#c0392b}.amber{color:#d35400}.green{color:#27ae60}.gray{color:#888}
.card{background:#fff;border-radius:12px;border:0.5px solid #e0e0e0;margin-bottom:1.5rem;overflow:hidden}
.card-header{padding:0.875rem 1.25rem;border-bottom:0.5px solid #e0e0e0;display:flex;align-items:center;justify-content:space-between}
.card-title{font-size:14px;font-weight:500}
.tabs{display:flex;gap:2px;background:#f5f5f7;border-radius:8px;padding:3px}
.tab{padding:5px 14px;border-radius:6px;font-size:13px;cursor:pointer;color:#555;border:none;background:transparent}
.tab.active{background:#fff;color:#1d1d1f;font-weight:500;box-shadow:0 1px 3px rgba(0,0,0,0.1)}
table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}
th{padding:9px 14px;text-align:left;font-size:11px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.04em;border-bottom:0.5px solid #e0e0e0;background:#fafafa}
td{padding:9px 14px;border-bottom:0.5px solid #f0f0f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafafa}
.pill{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;padding:2px 8px;border-radius:20px}
.pill-red{background:#fce8e8;color:#c0392b}
.pill-amber{background:#fef3e2;color:#d35400}
.pill-green{background:#e8f5e9;color:#27ae60}
.pill-gray{background:#f0f0f0;color:#888}
.btn{padding:7px 16px;border-radius:8px;border:0.5px solid #ddd;background:#fff;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-family:inherit}
.btn:hover{background:#f5f5f7}
.btn-primary{background:#1d1d1f;color:#fff;border-color:transparent}
.btn-primary:hover{background:#333}
.btn-sm{padding:4px 10px;font-size:12px}
.empty{padding:3rem;text-align:center;color:#aaa;font-size:13px}
.badge{font-size:10px;padding:2px 7px;border-radius:20px;font-weight:500;vertical-align:middle}
.upload-zone{border:1.5px dashed #ccc;border-radius:10px;padding:2.5rem;text-align:center;cursor:pointer;transition:all 0.15s;margin:1rem}
.upload-zone:hover,.upload-zone.drag{border-color:#1d1d1f;background:#f5f5f7}
.upload-icon{font-size:32px;margin-bottom:8px}
.mono{font-family:ui-monospace,monospace;font-size:12px;background:#f5f5f7;border-radius:8px;padding:1rem;white-space:pre-wrap;word-break:break-all;line-height:1.6}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}
select,input[type=number]{padding:5px 10px;border-radius:8px;border:0.5px solid #ddd;font-size:13px;font-family:inherit;background:#fff}
.controls{display:flex;align-items:center;gap:10px;padding:0.875rem 1.25rem;border-bottom:0.5px solid #e0e0e0;flex-wrap:wrap}
.controls label{font-size:13px;color:#666}
.spin{animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.toast{position:fixed;bottom:1.5rem;right:1.5rem;background:#1d1d1f;color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;transition:opacity 0.3s;pointer-events:none;z-index:999}
.toast.show{opacity:1}
</style>
</head>
<body>
<div class="topbar">
  <h1>&#x1F6E1; Cert Expiry Monitor</h1>
  <div class="right">
    <span id="last-scan">Loading…</span>
    <button class="btn btn-primary btn-sm" onclick="scanNow()" id="scan-btn">&#x21BB; Scan now</button>
  </div>
</div>

<div class="content">
  <div class="metrics">
    <div class="metric"><div class="metric-label">Total entries</div><div class="metric-value gray" id="m-total">—</div></div>
    <div class="metric"><div class="metric-label">Expired</div><div class="metric-value red" id="m-expired">—</div></div>
    <div class="metric"><div class="metric-label">Expiring soon</div><div class="metric-value amber" id="m-soon">—</div></div>
    <div class="metric"><div class="metric-label">Healthy</div><div class="metric-value green" id="m-ok">—</div></div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="tabs">
        <button class="tab active" onclick="switchTab('alerts',this)">Alerts</button>
        <button class="tab" onclick="switchTab('all',this)">All entries</button>
        <button class="tab" onclick="switchTab('upload',this)">Upload CSV</button>
        <button class="tab" onclick="switchTab('commands',this)">kubectl commands</button>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-sm" onclick="exportAlerts()">&#x2193; Export</button>
      </div>
    </div>

    <div class="controls">
      <label>Warn window</label>
      <input type="number" id="warn-days" value="30" min="1" max="365" style="width:70px"> days
      <label style="margin-left:8px">Status</label>
      <select id="filter-sel" onchange="renderAlerts()">
        <option value="all">All alerts</option>
        <option value="EXPIRED">Expired only</option>
        <option value="EXPIRING SOON">Expiring soon</option>
      </select>
      <span id="alert-count" style="margin-left:auto;font-size:12px;color:#888"></span>
    </div>

    <div id="tab-alerts">
      <table>
        <thead><tr>
          <th style="width:18%">App key</th>
          <th style="width:20%">Field</th>
          <th style="width:14%">Expiry</th>
          <th style="width:10%">Days</th>
          <th style="width:13%">Status</th>
          <th style="width:25%">Client ID</th>
        </tr></thead>
        <tbody id="alerts-body"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>

    <div id="tab-all" style="display:none">
      <table>
        <thead><tr>
          <th style="width:14%">App key</th>
          <th style="width:20%">Azure AD Client ID</th>
          <th style="width:12%">Ingress cert</th>
          <th style="width:12%">SSO cert</th>
          <th style="width:13%">AKS EOL</th>
          <th style="width:12%">PTC license</th>
          <th style="width:9%">Sheet</th>
        </tr></thead>
        <tbody id="all-body"><tr><td colspan="7" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>

    <div id="tab-upload" style="display:none;padding:1rem">
      <div class="upload-zone" id="drop-zone" onclick="document.getElementById('file-in').click()">
        <div class="upload-icon">&#x1F4C4;</div>
        <div style="font-size:14px;font-weight:500;margin-bottom:4px">Click or drag to upload CSV</div>
        <div style="font-size:12px;color:#aaa">Uploads to the pod and replaces /data/certs.csv for this session</div>
      </div>
      <input type="file" id="file-in" accept=".csv" style="display:none" onchange="uploadFile(this.files[0])">
    </div>

    <div id="tab-commands" style="display:none;padding:1.25rem">
      <div style="font-size:13px;color:#666;margin-bottom:1rem">Run these on your bastion server to update the ConfigMap and restart the pod.</div>
      <div class="mono" id="cmd-box">python3 -c "
import pandas as pd
df = pd.read_csv('certs.xlsx', sep='\t', dtype=str)
df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
df.to_csv('certs_fixed.csv', index=False)
print('Rows:', len(df))
"

kubectl delete configmap cert-data-file
kubectl create configmap cert-data-file \
  --from-file=certs.csv=certs_fixed.csv

kubectl patch configmap cert-monitor-config \
  --type merge \
  -p '{"data":{"DATA_FILE":"/data/certs.csv","SMTP_USE_TLS":"false","SMTP_PORT":"25","SMTP_REQUIRE_AUTH":"false"}}'

kubectl rollout restart deployment/cert-expiry-monitor
kubectl logs -l app=cert-expiry-monitor -f</div>
      <button class="btn btn-sm" style="margin-top:10px" onclick="copyCmd()">&#x2398; Copy</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let _data={alerts:[],rows:[],summary:{},warn_days:30};

function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500)}

function switchTab(name,el){
  ['alerts','all','upload','commands'].forEach(n=>document.getElementById('tab-'+n).style.display=n===name?'block':'none');
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  if(el)el.classList.add('active');
}

function pill(status,days){
  if(status==='EXPIRED') return `<span class="pill pill-red">&#x2715; Expired (${days}d)</span>`;
  if(status==='EXPIRING SOON') return `<span class="pill pill-amber">&#x26A0; ${days}d left</span>`;
  return `<span class="pill pill-green">&#x2713; OK</span>`;
}

function dateDot(val){
  if(!val||val.toLowerCase()==='n/a') return '<td style="color:#ccc">n/a</td>';
  const d=new Date(val),today=new Date();today.setHours(0,0,0,0);
  const days=Math.round((d-today)/86400000);
  const color=d<today?'#c0392b':days<=30?'#d35400':'#27ae60';
  return `<td><span class="dot" style="background:${color}"></span>${val.slice(0,10)}</td>`;
}

function renderAlerts(){
  const f=document.getElementById('filter-sel').value;
  const data=f==='all'?_data.alerts:_data.alerts.filter(a=>a.status===f);
  document.getElementById('alert-count').textContent=data.length+' alert(s)';
  const body=document.getElementById('alerts-body');
  if(!data.length){body.innerHTML='<tr><td colspan="6" class="empty">&#x2713; No alerts for this filter</td></tr>';return;}
  body.innerHTML=data.map(a=>`<tr>
    <td title="${a.identity}">${a.identity}</td>
    <td title="${a.column}">${a.column}</td>
    <td>${a.expiry_date}</td>
    <td>${a.days_left}</td>
    <td>${pill(a.status,a.days_left)}</td>
    <td title="${a.client_id}" style="color:#888;font-size:12px">${(a.client_id||'—').slice(0,32)}</td>
  </tr>`).join('');
}

function renderAll(){
  const body=document.getElementById('all-body');
  if(!_data.rows.length){body.innerHTML='<tr><td colspan="7" class="empty">No data</td></tr>';return;}
  body.innerHTML=_data.rows.map(r=>`<tr>
    <td title="${r['TWX_AppKey']||''}">${r['TWX_AppKey']||'—'}</td>
    <td title="${r['Azure AD -Client ID']||''}" style="font-size:11px;color:#888">${(r['Azure AD -Client ID']||'—').slice(0,28)}</td>
    ${dateDot(r['Ingress-cert'])}
    ${dateDot(r['SSO-cert'])}
    ${dateDot(r['AKS Version- End of life'])}
    ${dateDot(r['PTC License'])}
    <td style="color:#aaa">${r['_sheet']||'—'}</td>
  </tr>`).join('');
}

function updateMetrics(s){
  document.getElementById('m-total').textContent=s.total??'—';
  document.getElementById('m-expired').textContent=s.expired??'—';
  document.getElementById('m-soon').textContent=s.expiring_soon??'—';
  document.getElementById('m-ok').textContent=s.healthy??'—';
}

function loadStatus(){
  fetch('/api/status').then(r=>r.json()).then(d=>{
    _data=d;
    updateMetrics(d.summary||{});
    document.getElementById('last-scan').textContent=d.scanned_at?'Scanned '+new Date(d.scanned_at).toLocaleTimeString():'Not scanned yet';
    renderAlerts();renderAll();
  }).catch(()=>{});
}

function scanNow(){
  const btn=document.getElementById('scan-btn');
  btn.innerHTML='&#x21BB; Scanning…';btn.disabled=true;
  const wd=document.getElementById('warn-days').value;
  fetch('/api/scan?warn_days='+wd).then(r=>r.json()).then(d=>{
    _data=d;updateMetrics(d.summary||{});
    document.getElementById('last-scan').textContent='Scanned '+new Date(d.scanned_at).toLocaleTimeString();
    renderAlerts();renderAll();
    toast('Scan complete — '+d.alerts.length+' alert(s)');
    btn.innerHTML='&#x21BB; Scan now';btn.disabled=false;
  }).catch(()=>{btn.innerHTML='&#x21BB; Scan now';btn.disabled=false;});
}

function uploadFile(file){
  if(!file)return;
  const fd=new FormData();fd.append('file',file);
  fetch('/api/upload',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{
    if(d.ok){toast('Uploaded '+d.rows+' rows — rescanning…');scanNow();}
    else toast('Upload failed: '+d.error);
  });
}

function exportAlerts(){
  if(!_data.alerts.length){toast('No alerts to export');return;}
  const hdr='Identity,Field,Expiry,Days left,Status,Client ID';
  const lines=_data.alerts.map(a=>`"${a.identity}","${a.column}","${a.expiry_date}",${a.days_left},"${a.status}","${a.client_id}"`);
  const blob=new Blob([[hdr,...lines].join('\n')],{type:'text/csv'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cert_alerts.csv';a.click();
}

function copyCmd(){
  navigator.clipboard.writeText(document.getElementById('cmd-box').textContent).then(()=>toast('Copied!'));
}

const dz=document.getElementById('drop-zone');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('drag')});
dz.addEventListener('dragleave',()=>dz.classList.remove('drag'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('drag');uploadFile(e.dataTransfer.files[0])});

loadStatus();
setInterval(loadStatus, 60000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress access logs

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        qs   = self.path[len(path)+1:] if "?" in self.path else ""

        if path == "/":
            self._html(DASHBOARD_HTML)

        elif path == "/api/status":
            with _lock:
                self._json(_last_result)

        elif path == "/api/scan":
            warn = WARN_DAYS
            for part in qs.split("&"):
                if part.startswith("warn_days="):
                    try: warn = int(part.split("=")[1])
                    except: pass
            rows   = load_csv(DATA_FILE)
            result = run_scan(rows, warn)
            with _lock:
                _last_result.update(result)
            self._json(result)

        elif path == "/api/data":
            rows = load_csv(DATA_FILE)
            self._json({"rows": rows, "count": len(rows)})

        elif path == "/healthz":
            self._json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/upload":
            try:
                length  = int(self.headers.get("Content-Length", 0))
                raw     = self.rfile.read(length)
                # Extract CSV from multipart — find first line that looks like a header
                text = raw.decode("utf-8", errors="replace")
                # Find CSV portion (after multipart boundary headers)
                lines = text.split("\r\n")
                csv_start = next((i for i, l in enumerate(lines) if "TWX_AppKey" in l or "," in l[:80]), 4)
                csv_text = "\r\n".join(lines[csv_start:])
                # Strip trailing boundary
                csv_text = csv_text.split("\r\n--")[0]
                # Save to DATA_FILE path
                Path(DATA_FILE).write_text(csv_text, encoding="utf-8")
                rows = load_csv(DATA_FILE)
                self._json({"ok": True, "rows": len(rows)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()


def start(port=8080):
    server = HTTPServer(("0.0.0.0", port), Handler)
    log.info("[web] Dashboard running on http://0.0.0.0:%d", port)
    # Initial scan on startup
    threading.Thread(target=do_scan, daemon=True).start()
    server.serve_forever()
