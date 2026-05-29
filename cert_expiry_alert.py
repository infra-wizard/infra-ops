"""
cert_expiry_alert.py
--------------------
Runs continuously inside an AKS pod.
Configuration is injected via:
  - ConfigMap  "cert-monitor-config"  → environment variables (SMTP, schedule, columns)
  - ConfigMap  "cert-data-file"       → volume-mounted file at /data/certs.xlsx
  - Secret     "cert-monitor-secret"  → SMTP_PASSWORD

The script wakes up every SCAN_INTERVAL_HOURS, reads the xlsx/csv,
checks every DATE_COLUMN for expiry, and sends an SMTP HTML email.
"""

import base64
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


# ── Config from environment variables (injected by ConfigMap + Secret) ────────

def _env(key, default=None, required=False):
    val = os.environ.get(key, default)
    if required and not val:
        log.error("Missing required env var: %s", key)
        sys.exit(1)
    return val


# File path (ConfigMap volume-mounted xlsx/csv)
DATA_FILE            = _env("DATA_FILE", "/data/certs.xlsx", required=True)

# SMTP
SMTP_HOST            = _env("SMTP_HOST", required=True)
SMTP_PORT            = int(_env("SMTP_PORT", "587"))
SMTP_USER            = _env("SMTP_USER", required=True)
SMTP_PASSWORD        = _env("SMTP_PASSWORD", required=True)   # from Secret
SMTP_USE_TLS         = _env("SMTP_USE_TLS", "true").lower() == "true"

# Recipients
ALERT_RECIPIENTS     = [e.strip() for e in _env("ALERT_RECIPIENTS", required=True).split(",")]

# Alert window & scan cadence
WARN_DAYS            = int(_env("WARN_DAYS", "30"))
SCAN_INTERVAL_HOURS  = float(_env("SCAN_INTERVAL_HOURS", "24"))

# Columns (comma-separated, must match spreadsheet headers exactly)
DATE_COLUMNS = [
    c.strip() for c in _env(
        "DATE_COLUMNS",
        "Ingress-cert,SSO-cert,AKS Version- End of life,"
        "PTC License,ProvisioningKe,emessagekey,TWX_AppKey",
    ).split(",")
]

IDENTITY_COLUMNS = [
    c.strip() for c in _env(
        "IDENTITY_COLUMNS",
        "TWX_AppKey,Azure AD -Client ID,Logtools B2C- Client ID",
    ).split(",")
]


# ── Data loading ──────────────────────────────────────────────────────────────

def _get_raw_bytes(filepath: str) -> bytes:
    """
    Read file bytes from disk.
    When a binary file (xlsx) is stored in a Kubernetes ConfigMap via
    `kubectl create configmap --from-file=`, it is base64-encoded internally
    but Kubernetes decodes it back to raw bytes on volume mount.
    However, if the ConfigMap was created from a text representation or the
    file got corrupted, we attempt base64 decoding as a fallback.
    """
    raw = Path(filepath).read_bytes()

    # Check for xlsx magic bytes: PK\x03\x04 (ZIP format)
    if raw[:4] == b'PK\x03\x04':
        log.info("File has valid xlsx/ZIP magic bytes — using as-is.")
        return raw

    # If magic bytes missing, the bytes on disk may be base64-encoded text
    log.warning("xlsx magic bytes not found — attempting base64 decode.")
    try:
        decoded = base64.b64decode(raw.strip())
        if decoded[:4] == b'PK\x03\x04':
            log.info("Base64 decode succeeded — valid xlsx detected.")
            return decoded
    except Exception:
        pass

    # Last resort: return raw and let pandas fail with a clear message
    log.warning("Base64 decode did not produce valid xlsx. Returning raw bytes.")
    return raw


def load_file(filepath: str) -> pd.DataFrame:
    """
    Load xlsx/csv mounted from a Kubernetes ConfigMap volume.
    Explicitly sets engine='openpyxl' to avoid the
    'Excel file format cannot be determined' error.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(
            f"Data file not found at {filepath}. "
            "Verify the ConfigMap volume mount and the 'items.key' in deployment.yaml."
        )

    ext = p.suffix.lower()

    if ext in (".xlsx", ".xlsm"):
        raw = _get_raw_bytes(filepath)
        # Always specify engine explicitly — avoids format-detection errors
        sheets = pd.read_excel(
            io.BytesIO(raw),
            sheet_name=None,
            dtype=str,
            engine="openpyxl",        # ← key fix
        )
        frames = []
        for name, df in sheets.items():
            df["_sheet"] = name
            frames.append(df)
        combined = pd.concat(frames, ignore_index=True)
        log.info("Loaded %d row(s) from %d sheet(s) in %s",
                 len(combined), len(sheets), filepath)
        return combined

    elif ext == ".xls":
        raw = _get_raw_bytes(filepath)
        sheets = pd.read_excel(
            io.BytesIO(raw),
            sheet_name=None,
            dtype=str,
            engine="xlrd",            # legacy .xls format
        )
        frames = [df.assign(_sheet=name) for name, df in sheets.items()]
        combined = pd.concat(frames, ignore_index=True)
        log.info("Loaded %d row(s) from %d sheet(s) in %s",
                 len(combined), len(sheets), filepath)
        return combined

    elif ext == ".csv":
        raw = Path(filepath).read_bytes()
        df = pd.read_csv(io.BytesIO(raw), dtype=str)
        log.info("Loaded %d row(s) from %s", len(df), filepath)
        return df

    else:
        raise ValueError(f"Unsupported file extension '{ext}'. Use .xlsx or .csv")


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
    if not existing:
        log.warning("None of the configured DATE_COLUMNS found in file. "
                    "Check column names in ConfigMap: %s", DATE_COLUMNS)

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
            f"  {a['status']} ({a['days_left']}d)"
            f"</td>"
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
        f"</thead>"
        f"<tbody>{_table_rows(items)}</tbody>"
        f"</table>"
    )


def build_html(alerts: list[dict]) -> str:
    expired  = [a for a in alerts if a["status"] == "EXPIRED"]
    expiring = [a for a in alerts if a["status"] == "EXPIRING SOON"]
    pod      = os.environ.get("HOSTNAME", "unknown")
    now      = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    return f"""
<html><head><style>
  body  {{font-family:Arial,sans-serif;color:#222;max-width:980px;margin:auto;padding:20px}}
  h2   {{background:#1a252f;color:#fff;padding:14px 20px;border-radius:6px;margin:0 0 16px}}
  p    {{margin:3px 0;font-size:13px}}
  table{{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}}
  th,td{{border:1px solid #ddd;padding:7px 10px;text-align:left;vertical-align:top}}
  thead tr{{background:#2c3e50;color:#fff}}
  tr:nth-child(even){{background:#f7f7f7}}
</style></head><body>
  <h2>🔔 Certificate / License Expiry Alert</h2>
  <p>Scan time    : <b>{now}</b></p>
  <p>Source file  : <b>{DATA_FILE}</b></p>
  <p>Pod          : <b>{pod}</b></p>
  <p>Warn window  : <b>{WARN_DAYS} days</b></p>
  <p>Total alerts : <b>{len(alerts)}</b>
     &nbsp;(🚨 Expired: <b>{len(expired)}</b>
     &nbsp;⚠️ Expiring soon: <b>{len(expiring)}</b>)</p>

  {_section('🚨 Already Expired', '#c0392b', expired)}
  {_section(f'⚠️ Expiring Within {WARN_DAYS} Days', '#d35400', expiring)}

  <hr style='margin-top:30px'>
  <p style='font-size:11px;color:#aaa'>
    Auto-generated by cert-expiry-monitor running in AKS pod <b>{pod}</b>.
    Do not reply to this email.
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

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.ehlo()
            if SMTP_USE_TLS:
                srv.starttls()
                srv.ehlo()
            srv.login(SMTP_USER, SMTP_PASSWORD)
            srv.sendmail(SMTP_USER, ALERT_RECIPIENTS, msg.as_string())
        log.info("✅ Email sent to: %s", ", ".join(ALERT_RECIPIENTS))
    except smtplib.SMTPAuthenticationError:
        log.error("SMTP authentication failed — check SMTP_USER and SMTP_PASSWORD")
    except smtplib.SMTPConnectError:
        log.error("Cannot connect to SMTP server %s:%d", SMTP_HOST, SMTP_PORT)
    except Exception as exc:
        log.exception("Unexpected error sending email: %s", exc)


# ── Scan cycle ────────────────────────────────────────────────────────────────

def scan():
    log.info("── Scan started ── file=%s  warn=%dd", DATA_FILE, WARN_DAYS)
    try:
        df     = load_file(DATA_FILE)
        df     = parse_dates(df)
        alerts = find_expiring(df)
        log.info("Scan complete — %d alert(s) found", len(alerts))

        if alerts:
            for a in alerts:
                log.warning("[%s] %s | %s | expires %s (%dd)",
                            a["status"], a["identity"], a["column"],
                            a["expiry_date"], a["days_left"])
            send_alert(alerts)
        else:
            log.info("No items expiring within %d days — no email sent.", WARN_DAYS)

    except FileNotFoundError as exc:
        log.error("%s", exc)
    except Exception as exc:
        log.exception("Unhandled error during scan: %s", exc)


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
        sleep_sec = SCAN_INTERVAL_HOURS * 3600
        log.info("💤 Next scan in %.1f hour(s) …", SCAN_INTERVAL_HOURS)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
