# Kubernetes TLS Secret Expiry Monitor

Scans all `kubernetes.io/tls` secrets across your cluster, parses the X.509 certificates, and sends alerts when any certificate is within **30 days** (configurable) of expiring.

## Features

- Scans all namespaces or a specific subset
- Parses certificate subject, issuer, SANs, and expiry date
- Three alert backends: **Console**, **Email** (SMTP), **Slack** (webhook)
- Runs as a one-shot check or a continuous daemon
- Ships with a Kubernetes CronJob manifest for in-cluster deployment
- Fully configurable via `config.yaml` or environment variables

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy and edit `config.yaml`, or set environment variables:

| Env Variable               | Description                          | Default          |
|----------------------------|--------------------------------------|------------------|
| `K8S_TLS_EXPIRY_DAYS`     | Alert threshold in days              | `30`             |
| `K8S_TLS_POLL_HOURS`      | Daemon polling interval (hours)      | `24`             |
| `K8S_TLS_NAMESPACES`      | Comma-separated namespace list       | *(all)*          |
| `K8S_TLS_EMAIL_ENABLED`   | Enable email alerts                  | `false`          |
| `K8S_TLS_SMTP_HOST`       | SMTP server hostname                 | `smtp.gmail.com` |
| `K8S_TLS_SMTP_PORT`       | SMTP server port                     | `587`            |
| `K8S_TLS_SMTP_USER`       | SMTP username                        | —                |
| `K8S_TLS_SMTP_PASSWORD`   | SMTP password / app password         | —                |
| `K8S_TLS_EMAIL_FROM`      | Sender address                       | —                |
| `K8S_TLS_EMAIL_TO`        | Comma-separated recipient list       | —                |
| `K8S_TLS_SLACK_ENABLED`   | Enable Slack alerts                  | `false`          |
| `K8S_TLS_SLACK_WEBHOOK`   | Slack incoming webhook URL           | —                |

### 3. Run

```bash
# One-shot (exit code 1 if expiring certs found – useful in CI/CD)
python k8s_tls_expiry_monitor.py

# Continuous daemon
python k8s_tls_expiry_monitor.py --daemon

# Custom config path
python k8s_tls_expiry_monitor.py -c /etc/tls-monitor/config.yaml
```

## Deploy to Kubernetes

### Build & push the image

```bash
docker build -t your-registry/k8s-tls-expiry-monitor:latest .
docker push your-registry/k8s-tls-expiry-monitor:latest
```

### Create the Slack webhook secret

```bash
kubectl create namespace monitoring

kubectl -n monitoring create secret generic tls-monitor-secrets \
  --from-literal=slack-webhook-url="https://hooks.slack.com/services/T.../B.../xxx"
```

### Apply the CronJob

```bash
kubectl apply -f k8s-cronjob.yaml
```

This creates:
- A **ServiceAccount** with read-only access to secrets (ClusterRole + ClusterRoleBinding)
- A **CronJob** that runs daily at 08:00 UTC

### Test it manually

```bash
kubectl -n monitoring create job --from=cronjob/tls-expiry-monitor tls-test-run
kubectl -n monitoring logs -f job/tls-test-run
```

## How It Works

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  K8s API Server  │────▶│  List TLS Secrets │────▶│  Parse X.509    │
│  (all namespaces)│     │  (type=tls)       │     │  certificates   │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                        ┌─────────────────▼──────────────┐
                                        │  Filter: days_remaining ≤ 30   │
                                        └─────────────────┬──────────────┘
                                                          │
                                    ┌─────────┬───────────┼───────────┐
                                    ▼         ▼           ▼           │
                               Console     Email       Slack         │
                               (stdout)   (SMTP)    (webhook)        │
                                    └─────────┴───────────┴───────────┘
```

## Exit Codes

| Code | Meaning                                    |
|------|--------------------------------------------|
| `0`  | All certificates are healthy               |
| `1`  | One or more certificates are expiring soon |
