#!/usr/bin/env python3
"""
Kubernetes TLS Secret Expiration Monitor
=========================================
Scans all TLS secrets across namespaces, checks certificate expiry dates,
and sends alerts (Email / Slack / console) when a certificate will expire
within a configurable threshold (default: 30 days).

Requirements:
    pip install kubernetes cryptography requests pyyaml

Usage:
    # Run once (e.g. from a CronJob):
    python k8s_tls_expiry_monitor.py

    # Run as a continuous loop (checks every POLL_INTERVAL_HOURS):
    python k8s_tls_expiry_monitor.py --daemon

Environment variables / config.yaml control alert destinations.
"""

import argparse
import base64
import datetime
import logging
import os
import smtplib
import sys
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import yaml
from cryptography import x509
from cryptography.hazmat.backends import default_backend

try:
    import requests
except ImportError:
    requests = None  # Slack alerting disabled if requests is missing

try:
    from kubernetes import client, config as k8s_config
except ImportError:
    print("ERROR: 'kubernetes' package is required.  pip install kubernetes")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tls-monitor")

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class CertInfo:
    """Holds parsed certificate metadata."""
    namespace: str
    secret_name: str
    subject: str
    issuer: str
    not_after: datetime.datetime
    days_remaining: int
    san_list: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return self.days_remaining <= 0

    def summary(self) -> str:
        status = "EXPIRED" if self.is_expired else f"{self.days_remaining}d remaining"
        return (
            f"  Secret : {self.namespace}/{self.secret_name}\n"
            f"  Subject: {self.subject}\n"
            f"  Issuer : {self.issuer}\n"
            f"  Expiry : {self.not_after:%Y-%m-%d %H:%M UTC}  ({status})\n"
            f"  SANs   : {', '.join(self.san_list) or 'none'}"
        )


@dataclass
class AlertConfig:
    """Configuration for alert thresholds and destinations."""
    # Threshold in days – alert if cert expires within this window
    expiry_threshold_days: int = 30

    # Polling interval when running in daemon mode
    poll_interval_hours: int = 24

    # Namespaces to scan (empty = all namespaces)
    namespaces: list[str] = field(default_factory=list)

    # Email settings
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: list[str] = field(default_factory=list)

    # Slack settings
    slack_enabled: bool = False
    slack_webhook_url: str = ""

    # Console (always active as fallback)
    console_enabled: bool = True


# ---------------------------------------------------------------------------
# Configuration Loader
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[str] = None) -> AlertConfig:
    """
    Build AlertConfig from (in priority order):
      1. Environment variables   (prefixed K8S_TLS_)
      2. config.yaml             (if present)
      3. Defaults
    """
    cfg = AlertConfig()

    # --- YAML file ---------------------------------------------------------
    yaml_path = Path(config_path) if config_path else Path(__file__).parent / "config.yaml"
    if yaml_path.exists():
        log.info("Loading config from %s", yaml_path)
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        cfg.expiry_threshold_days = data.get("expiry_threshold_days", cfg.expiry_threshold_days)
        cfg.poll_interval_hours = data.get("poll_interval_hours", cfg.poll_interval_hours)
        cfg.namespaces = data.get("namespaces", cfg.namespaces)

        email = data.get("email", {})
        cfg.email_enabled = email.get("enabled", cfg.email_enabled)
        cfg.smtp_host = email.get("smtp_host", cfg.smtp_host)
        cfg.smtp_port = email.get("smtp_port", cfg.smtp_port)
        cfg.smtp_user = email.get("smtp_user", cfg.smtp_user)
        cfg.smtp_password = email.get("smtp_password", cfg.smtp_password)
        cfg.email_from = email.get("from", cfg.email_from)
        cfg.email_to = email.get("to", cfg.email_to)

        slack = data.get("slack", {})
        cfg.slack_enabled = slack.get("enabled", cfg.slack_enabled)
        cfg.slack_webhook_url = slack.get("webhook_url", cfg.slack_webhook_url)

        cfg.console_enabled = data.get("console_enabled", cfg.console_enabled)

    # --- Environment overrides ---------------------------------------------
    cfg.expiry_threshold_days = int(os.getenv("K8S_TLS_EXPIRY_DAYS", cfg.expiry_threshold_days))
    cfg.poll_interval_hours = int(os.getenv("K8S_TLS_POLL_HOURS", cfg.poll_interval_hours))

    ns_env = os.getenv("K8S_TLS_NAMESPACES", "")
    if ns_env:
        cfg.namespaces = [n.strip() for n in ns_env.split(",") if n.strip()]

    cfg.email_enabled = os.getenv("K8S_TLS_EMAIL_ENABLED", str(cfg.email_enabled)).lower() == "true"
    cfg.smtp_host = os.getenv("K8S_TLS_SMTP_HOST", cfg.smtp_host)
    cfg.smtp_port = int(os.getenv("K8S_TLS_SMTP_PORT", cfg.smtp_port))
    cfg.smtp_user = os.getenv("K8S_TLS_SMTP_USER", cfg.smtp_user)
    cfg.smtp_password = os.getenv("K8S_TLS_SMTP_PASSWORD", cfg.smtp_password)
    cfg.email_from = os.getenv("K8S_TLS_EMAIL_FROM", cfg.email_from)
    to_env = os.getenv("K8S_TLS_EMAIL_TO", "")
    if to_env:
        cfg.email_to = [e.strip() for e in to_env.split(",") if e.strip()]

    cfg.slack_enabled = os.getenv("K8S_TLS_SLACK_ENABLED", str(cfg.slack_enabled)).lower() == "true"
    cfg.slack_webhook_url = os.getenv("K8S_TLS_SLACK_WEBHOOK", cfg.slack_webhook_url)

    return cfg


# ---------------------------------------------------------------------------
# Kubernetes helpers
# ---------------------------------------------------------------------------

def init_k8s_client():
    """Load in-cluster config or fall back to ~/.kube/config."""
    try:
        k8s_config.load_incluster_config()
        log.info("Using in-cluster Kubernetes config")
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        log.info("Using local kubeconfig")


def get_tls_secrets(v1: client.CoreV1Api, namespaces: list[str]) -> list:
    """Return all kubernetes.io/tls secrets from the requested namespaces."""
    secrets = []
    if namespaces:
        for ns in namespaces:
            try:
                result = v1.list_namespaced_secret(
                    namespace=ns, field_selector="type=kubernetes.io/tls"
                )
                secrets.extend(result.items)
            except client.exceptions.ApiException as e:
                log.warning("Cannot list secrets in namespace '%s': %s", ns, e.reason)
    else:
        result = v1.list_secret_for_all_namespaces(field_selector="type=kubernetes.io/tls")
        secrets = result.items
    return secrets


# ---------------------------------------------------------------------------
# Certificate parsing
# ---------------------------------------------------------------------------

def parse_certificate(pem_data: bytes) -> x509.Certificate:
    """Parse a PEM-encoded certificate."""
    return x509.load_pem_x509_certificate(pem_data, default_backend())


def extract_san(cert: x509.Certificate) -> list[str]:
    """Extract Subject Alternative Names from the certificate."""
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        return ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        return []


def inspect_secret(secret) -> Optional[CertInfo]:
    """
    Decode the tls.crt field of a Kubernetes TLS secret, parse the first
    certificate in the chain, and return a CertInfo.
    """
    ns = secret.metadata.namespace
    name = secret.metadata.name
    raw = secret.data.get("tls.crt")
    if not raw:
        log.warning("Secret %s/%s has no tls.crt data – skipping", ns, name)
        return None

    try:
        pem_bytes = base64.b64decode(raw)
        cert = parse_certificate(pem_bytes)
    except Exception as e:
        log.error("Failed to parse certificate in %s/%s: %s", ns, name, e)
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    not_after = cert.not_valid_after_utc
    days_remaining = (not_after - now).days

    return CertInfo(
        namespace=ns,
        secret_name=name,
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        not_after=not_after,
        days_remaining=days_remaining,
        san_list=extract_san(cert),
    )


# ---------------------------------------------------------------------------
# Alerting backends
# ---------------------------------------------------------------------------

def alert_console(expiring: list[CertInfo], cfg: AlertConfig):
    """Print alerts to stdout/stderr."""
    log.warning("=== TLS Certificate Expiry Alert ===")
    log.warning(
        "%d certificate(s) expiring within %d days:\n", len(expiring), cfg.expiry_threshold_days
    )
    for cert in expiring:
        log.warning(cert.summary() + "\n")


def alert_email(expiring: list[CertInfo], cfg: AlertConfig):
    """Send an HTML email with the expiry report."""
    if not cfg.email_to:
        log.error("Email alerting enabled but no recipients configured")
        return

    subject = f"⚠️ K8s TLS Alert: {len(expiring)} cert(s) expiring soon"

    # Build HTML body
    rows = ""
    for c in expiring:
        color = "#e74c3c" if c.is_expired else "#e67e22" if c.days_remaining < 7 else "#f39c12"
        rows += (
            f"<tr>"
            f"<td>{c.namespace}/{c.secret_name}</td>"
            f"<td>{c.subject}</td>"
            f"<td>{', '.join(c.san_list) or '—'}</td>"
            f"<td>{c.not_after:%Y-%m-%d}</td>"
            f'<td style="color:{color};font-weight:bold">'
            f'{"EXPIRED" if c.is_expired else f"{c.days_remaining} days"}</td>'
            f"</tr>"
        )

    html = f"""\
    <html><body>
    <h2>Kubernetes TLS Certificate Expiry Report</h2>
    <p>{len(expiring)} certificate(s) will expire within
       <b>{cfg.expiry_threshold_days} days</b>.</p>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse;font-family:monospace;font-size:13px">
      <tr style="background:#2c3e50;color:#fff">
        <th>Secret</th><th>Subject</th><th>SANs</th>
        <th>Expires</th><th>Remaining</th>
      </tr>
      {rows}
    </table>
    <p style="color:#888;font-size:11px">
      Generated by k8s-tls-expiry-monitor at {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M UTC}
    </p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.email_from
    msg["To"] = ", ".join(cfg.email_to)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.sendmail(cfg.email_from, cfg.email_to, msg.as_string())
        log.info("Email alert sent to %s", cfg.email_to)
    except Exception as e:
        log.error("Failed to send email: %s", e)


def alert_slack(expiring: list[CertInfo], cfg: AlertConfig):
    """Post an alert to a Slack incoming webhook."""
    if requests is None:
        log.error("Slack alerting requires the 'requests' package")
        return

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"⚠️ {len(expiring)} TLS cert(s) expiring soon",
            },
        },
        {"type": "divider"},
    ]

    for c in expiring:
        emoji = "🔴" if c.is_expired else "🟡" if c.days_remaining < 7 else "🟠"
        status = "EXPIRED" if c.is_expired else f"{c.days_remaining}d left"
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Secret:*\n`{c.namespace}/{c.secret_name}`"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{emoji} {status}"},
                    {"type": "mrkdwn", "text": f"*Expiry:*\n{c.not_after:%Y-%m-%d}"},
                    {"type": "mrkdwn", "text": f"*SANs:*\n{', '.join(c.san_list) or '—'}"},
                ],
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_Threshold: {cfg.expiry_threshold_days} days  •  "
                    f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M UTC}_",
                }
            ],
        }
    )

    payload = {"blocks": blocks}
    try:
        resp = requests.post(cfg.slack_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Slack alert sent successfully")
    except Exception as e:
        log.error("Failed to send Slack alert: %s", e)


# ---------------------------------------------------------------------------
# Main scanning logic
# ---------------------------------------------------------------------------

def scan_and_alert(cfg: AlertConfig) -> int:
    """
    Scan TLS secrets, identify expiring certs, and dispatch alerts.
    Returns the number of expiring/expired certificates found.
    """
    v1 = client.CoreV1Api()
    secrets = get_tls_secrets(v1, cfg.namespaces)
    log.info("Found %d TLS secret(s) to inspect", len(secrets))

    all_certs: list[CertInfo] = []
    for s in secrets:
        info = inspect_secret(s)
        if info:
            all_certs.append(info)

    # Filter to those within the alert threshold
    expiring = [c for c in all_certs if c.days_remaining <= cfg.expiry_threshold_days]
    # Sort: most urgent first
    expiring.sort(key=lambda c: c.days_remaining)

    if not expiring:
        log.info(
            "✅ All %d certificate(s) are valid beyond the %d-day threshold",
            len(all_certs),
            cfg.expiry_threshold_days,
        )
        return 0

    # Dispatch alerts
    if cfg.console_enabled:
        alert_console(expiring, cfg)
    if cfg.email_enabled:
        alert_email(expiring, cfg)
    if cfg.slack_enabled:
        alert_slack(expiring, cfg)

    return len(expiring)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kubernetes TLS Secret Expiry Monitor")
    parser.add_argument(
        "--config", "-c", help="Path to config.yaml (default: ./config.yaml)"
    )
    parser.add_argument(
        "--daemon", "-d", action="store_true",
        help="Run continuously, polling every POLL_INTERVAL_HOURS"
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    init_k8s_client()

    if args.daemon:
        log.info(
            "Running in daemon mode – polling every %d hour(s), threshold %d days",
            cfg.poll_interval_hours,
            cfg.expiry_threshold_days,
        )
        while True:
            try:
                scan_and_alert(cfg)
            except Exception:
                log.exception("Error during scan cycle")
            time.sleep(cfg.poll_interval_hours * 3600)
    else:
        count = scan_and_alert(cfg)
        sys.exit(1 if count > 0 else 0)


if __name__ == "__main__":
    main()
