"""
cert_expiry_alert.py
--------------------
Runs continuously inside an AKS pod.
Configuration is injected via:
  - ConfigMap "cert-monitor-config" → environment variables
  - ConfigMap "cert-data-file"      → volume-mounted CSV at /data/certs.csv
  - Secret    "cert-monitor-secret" → SMTP_PASSWORD

NOTE: Store data as CSV in the ConfigMap (not xlsx).
      Kubernetes ConfigMaps corrupt binary xlsx files.
      Use convert_xlsx_to_csv.py locally to convert first.
"""

import io
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ── Config from environment variables ────────────────────────────────────────

def _env(key, default=None, required=False):
    val = os.environ.get(key, default)
    if required and not val:
        log.error("Missing required env var: %s", key)
        sys.exit(1)
    return val


DATA_FILE           = _env("DATA_FILE", "/data/certs.csv", required=True)
SMTP_HOST           = _env("SMTP_HOST", required=True)
SMTP_PORT           = int(_env("SMTP_PORT", "587"))
SMTP_USER           = _env("SMTP_USER", required=True)
SMTP_PASSWORD       = _env("SMTP_PASSWORD", required=True)
SMTP_USE_TLS        = _env("SMTP_USE_TLS", "true").lower() == "true"
ALERT_RECIPIENTS    = [e.strip() for e in _env("ALERT_RECIPIENTS", required=True).split(",")]
WARN_DAYS           = int(_env("WARN_DAYS", "30"))
SCAN_INTERVAL_HOURS = float(_env("SCAN_INTERVAL_HOURS", "24"))

DATE_COLUMNS = [c.strip() for c in _env(
    "DATE_COLUMNS",
    "Ingress-cert,SSO-cert,AKS Version- End of life,"
    "PTC License,ProvisioningKe,emessagekey,TWX_AppKey",
).split(",")]

IDENTITY_COLUMNS = [c.strip() for c in _env(
    "IDENTITY_COLUMNS",
    "TWX_AppKey,Azure AD -Client ID,Logtools B2C- Client ID",
).split(",")]


# ── Data loading ──────────────────────────────────────────────────────────────

def load_file(filepath: str) -> pd.DataFrame:
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(
            f"Data file not found: {filepath}\n"
            "  • Check the ConfigMap volume mount in deployment.yaml\n"
            "  • Verify DATA_FILE env var matches the mounted filename\n"
            "  • Run: kubectl exec <pod> -- ls -la /data/"
        )

    ext  = p.suffix.lower()
    size = p.stat().st_size
    log.info("Reading %s  (%.1f KB, type=%s)", filepath, size / 1024, ext)

    if ext == ".csv":
        # CSV is plain text — ConfigMap handles it perfectly
        text = p.read_text(encoding="utf-8", errors="replace")
        df   = pd.read_csv(io.StringIO(text), dtype=str)
        log.info("Loaded %d row(s) from CSV", len(df))
        return df

    elif ext in (".xlsx", ".xlsm", ".xls"):
        # Binary xlsx should NOT be stored in a ConfigMap.
        # If someone mounts one anyway, try to read it but warn loudly.
        log.warning(
            "xlsx detected in ConfigMap mount — binary files are likely corrupted.\n"
            "  → Run convert_xlsx_to_csv.py locally and recreate the ConfigMap with a CSV."
        )
        raw = p.read_bytes()
        if raw[:4] != b'PK\x03\x04':
            raise ValueError(
                "xlsx magic bytes missing — file is corrupted by ConfigMap encoding.\n"
                "FIX: convert to CSV first:\n"
                "  python convert_xlsx_to_csv.py certs.xlsx\n"
                "  kubectl create configmap cert-data-file --from-file=certs.csv "
                "--dry-run=client -o yaml | kubectl apply -f -\n"
                "  kubectl rollout restart deployment/cert-expiry-monitor"
            )
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None,
                               dtype=str, engine="openpyxl")
        frames = [df.assign(_sheet=name) for name, df in sheets.items()]
        combined = pd.concat(frames, ignore_index=True)
        log.info("Loaded %d row(s) from %d sheet(s)", len(combined), len(sheets))
        return combined

    else:
        raise ValueError(f"Unsupported file type '{ext}'. Use .csv or .xlsx")


# ── Date parsing & expiry check ───────────────────────────────────────────────

def parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=False)
    return df


def row_identity(row: pd.Series) -> str:
    parts = []
    for col in IDENTITY_COLUMNS:
        if col in row.index:
            val = str(row[col]).strip()
            if val and val.lower() not in ("nan", "none", ""):
                parts.append(f"{col}: {val}")
    return " | ".join(parts) if parts else f"Row {row.name}"


def find_expiring(df: pd.DataFrame) -> list[dict]:
    today    = datetime.now().date()
    deadline = today + timedelta(days=WARN_DAYS)
    alerts   = []

    existing = [c for c in DATE_COLUMNS if c in df.columns]
    missing  = [c for c in DATE_COLUMNS if c not in df.columns]
    if missing:
        log.warning("DATE_COLUMNS not found in file (check header names): %s", missing)

    for _, row in df.iterrows():
        identity = row_identity(row)
        for col in existing:
            val = row[col]
            if pd.isna(val):
                continue
            exp       = val.date()
            days_left = (exp - today).days
            if exp <= deadline:
                alerts.append({
                    "identity"    : identity,
                    "column"      : col,
                    "expiry_date" : exp.strftime("%Y-%m-%d"),
                    "days_left"   : days_left,
                    "status"      : "EXPIRED" if exp < today else "EXPIRING SOON",
                    "sheet"       : row.get("_sheet", "—"),
                })
    return alerts


# ── Email ─────────────────────────────────────────────────────────────────────

def _table_rows(items: list[dict]) -> str:
    html = ""
    for a in items:
        color = "#c0392b" if a["status"] == "EXPIRED" else "#d35400"
        html += (
            f"<tr>"
            f"<td>{a['identity']}</td>"
            f"<td>{a['column']}</td>"
            f"<td>{a['expiry_date']}</td>"
            f"<td style='color:{color};font-weight:bold'>"
            f"{a['status']} ({a['days_left']}d)</td>"
            f"<td>{a['sheet']}</td>"
            f"</tr>"
        )
    return html


def _section(title: str, bg: str, items: list[dict]) -> str:
    if not items:
        return ""
    return (
        f"<h3 style='color:{bg};margin-top:24px'>{title} — {len(items)} item(s)</h3>"
        f"<table>"
        f"<thead style='background:{bg};color:#fff'>"
        f"<tr><th>Identity</th><th>Field</th><th>Expiry</th><th>Status</th><th>Sheet</th></tr>"
        f"</thead><tbody>{_table_rows(items)}</tbody></table>"
    )


def build_html(alerts: list[dict]) -> str:
    expired  = [a for a in alerts if a["status"] == "EXPIRED"]
    expiring = [a for a in alerts if a["status"] == "EXPIRING SOON"]
    pod      = os.environ.get("HOSTNAME", "unknown")

    return f"""<html><head><style>
  body  {{font-family:Arial,sans-serif;color:#222;max-width:980px;margin:auto;padding:20px}}
  h2   {{background:#1a252f;color:#fff;padding:14px 20px;border-radius:6px;margin:0 0 16px}}
  p    {{margin:3px 0;font-size:13px}}
  table{{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}}
  th,td{{border:1px solid #ddd;padding:7px 10px;text-align:left;vertical-align:top}}
  thead tr{{background:#2c3e50;color:#fff}}
  tr:nth-child(even){{background:#f7f7f7}}
</style></head><body>
  <h2>🔔 Certificate / License Expiry Alert</h2>
  <p>Scan time   : <b>{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</b></p>
  <p>Source file : <b>{DATA_FILE}</b></p>
  <p>Pod         : <b>{pod}</b></p>
  <p>Warn window : <b>{WARN_DAYS} days</b></p>
  <p>Total alerts: <b>{len(alerts)}</b>
     (🚨 Expired: <b>{len(expired)}</b>
      ⚠️ Expiring soon: <b>{len(expiring)}</b>)</p>
  {_section('🚨 Already Expired', '#c0392b', expired)}
  {_section(f'⚠️ Expiring Within {WARN_DAYS} Days', '#d35400', expiring)}
  <hr style='margin-top:30px'>
  <p style='font-size:11px;color:#aaa'>
    Auto-generated by cert-expiry-monitor | pod: {pod}
  </p>
</body></html>"""


def send_alert(alerts: list[dict]):
    subject = (
        f"[CERT ALERT] {len(alerts)} item(s) expiring/expired "
        f"— {datetime.now().strftime('%Y-%m-%d')}"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(ALERT_RECIPIENTS)
    msg.attach(MIMEText(build_html(alerts), "html"))

    # SMTP_REQUIRE_AUTH controls whether to login.
    # Internal corporate relays (port 25) use IP-based auth — no login needed.
    require_auth = _env("SMTP_REQUIRE_AUTH", "false").lower() == "true"

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.ehlo()
            if SMTP_USE_TLS:
                srv.starttls()
                srv.ehlo()
            if require_auth:
                srv.login(SMTP_USER, SMTP_PASSWORD)
            srv.sendmail(SMTP_USER, ALERT_RECIPIENTS, msg.as_string())
        log.info("✅ Email sent to: %s", ", ".join(ALERT_RECIPIENTS))
    except smtplib.SMTPAuthenticationError:
        log.error("SMTP auth failed — check SMTP_USER / SMTP_PASSWORD")
    except smtplib.SMTPConnectError:
        log.error("Cannot connect to %s:%d", SMTP_HOST, SMTP_PORT)
    except Exception as exc:
        log.exception("Email error: %s", exc)


# ── Scan cycle ────────────────────────────────────────────────────────────────

def scan():
    log.info("── Scan started — file=%s  warn=%dd", DATA_FILE, WARN_DAYS)
    try:
        df     = load_file(DATA_FILE)
        df     = parse_dates(df)
        alerts = find_expiring(df)
        log.info("Scan complete — %d alert(s)", len(alerts))

        if alerts:
            for a in alerts:
                log.warning("[%s] %s | %s | expires %s (%dd)",
                            a["status"], a["identity"], a["column"],
                            a["expiry_date"], a["days_left"])
            send_alert(alerts)
        else:
            log.info("No items expiring within %d days.", WARN_DAYS)

    except FileNotFoundError as exc:
        log.error("%s", exc)
    except ValueError as exc:
        log.error("%s", exc)
    except Exception as exc:
        log.exception("Unhandled scan error: %s", exc)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("=== cert-expiry-monitor starting ===")
    log.info("DATA_FILE           : %s", DATA_FILE)
    log.info("SMTP_HOST           : %s:%d (TLS=%s)", SMTP_HOST, SMTP_PORT, SMTP_USE_TLS)
    log.info("ALERT_RECIPIENTS    : %s", ALERT_RECIPIENTS)
    log.info("WARN_DAYS           : %d", WARN_DAYS)
    log.info("SCAN_INTERVAL_HOURS : %.1f", SCAN_INTERVAL_HOURS)
    log.info("DATE_COLUMNS        : %s", DATE_COLUMNS)

    while True:
        scan()
        log.info("💤 Next scan in %.1f hour(s) …", SCAN_INTERVAL_HOURS)
        time.sleep(SCAN_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    main()
