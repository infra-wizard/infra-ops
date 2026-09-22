#!/usr/bin/env python3
"""
SonarQube Cloud — weekly opened vs. closed issues dashboard generator.

Why a script and not a live web page?
--------------------------------------
SonarCloud's API does not support CORS, and Sonar has said they have no plans
to enable it (calling out permissive CORS as a security anti-pattern). That
means a browser page cannot call the SonarCloud API directly with your token
from any other origin — the request is blocked before it reaches Sonar. This
script calls the API server-side (no browser involved, so no CORS issue) and
writes a self-contained HTML file you can open in any browser.

What it does
------------
1. Fetches issues for your project from SonarCloud's `issues/search` API.
2. Buckets them by ISO week (Monday-start):
     - "opened"  = issues whose creationDate falls in that week
     - "closed"  = issues whose closeDate falls in that week
   (closeDate is set on an issue independent of when it was created, so a
   bug opened months ago that got fixed this week still counts as "closed
   this week".)
3. Writes sonar_weekly_dashboard.html with a bar chart + table.

Usage
-----
    export SONAR_TOKEN="your_sonarcloud_token"
    python3 sonar_weekly_dashboard.py --project my-project-key --weeks 12

    # If your project is inside an organization and the key alone isn't
    # unique, SonarCloud project keys are already globally unique, so you
    # normally don't need to pass the org separately.

    # Optional: specific branch (defaults to the project's main branch)
    python3 sonar_weekly_dashboard.py --project my-project-key --branch develop

Re-run it any time — e.g. as a weekly cron job / Windows Task Scheduler
entry — to refresh the dashboard with the latest data.

Getting a token
----------------
SonarCloud > My Account > Security > Generate Token.
The token only needs read access to the project(s) you want to report on.
"""
import argparse
import base64
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://sonarcloud.io/api"
PAGE_SIZE = 500
MAX_PAGES = 40  # safety cap: 40 * 500 = 20,000 issues


def api_get(path, token, params):
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    auth = base64.b64encode(f"{token}:".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(
            f"SonarCloud API error {e.code} calling {path}:\n{body}"
        )


def fetch_all_issues(token, project_key, branch, extra_params):
    issues = []
    page = 1
    while page <= MAX_PAGES:
        params = {
            "componentKeys": project_key,
            "statuses": "OPEN,CONFIRMED,REOPENED,RESOLVED,CLOSED",
            "ps": PAGE_SIZE,
            "p": page,
            **extra_params,
        }
        if branch:
            params["branch"] = branch
        data = api_get("issues/search", token, params)
        batch = data.get("issues", [])
        issues.extend(batch)
        total = data.get("total", 0)
        print(f"  page {page}: {len(batch)} issues (total so far: {len(issues)} of {total})")
        if page * PAGE_SIZE >= total or not batch:
            break
        page += 1
        time.sleep(0.2)  # be gentle with the API
    return issues


def fetch_total_count(token, project_key, branch, extra_params):
    """Get just the 'total' field for a query, without paginating full issues."""
    params = {
        "componentKeys": project_key,
        "ps": 1,
        "p": 1,
        **extra_params,
    }
    if branch:
        params["branch"] = branch
    data = api_get("issues/search", token, params)
    return data.get("total", 0)


def iso_week_start(d):
    monday = d - dt.timedelta(days=d.weekday())
    return dt.datetime(monday.year, monday.month, monday.day)


def parse_sonar_date(s):
    # SonarCloud dates look like "2024-05-01T10:22:31+0000"
    return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def build_html(project, labels, opened, closed, generated_at, note, currently_open):
    data = {
        "project": project,
        "labels": labels,
        "opened": opened,
        "closed": closed,
        "generated": generated_at,
        "note": note,
        "currentlyOpen": currently_open,
    }
    return TEMPLATE.replace("__DATA__", json.dumps(data))


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SonarCloud Weekly Issues Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #171a23;
    --text: #e6e8ee;
    --muted: #9aa1b1;
    --opened: #ef6c6c;
    --closed: #52c993;
    --net: #7aa8ff;
    --border: #262a36;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 32px 24px 60px;
  }
  .wrap { max-width: 1000px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 28px; }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 24px;
  }
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
  .stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  .stat .value { font-size: 26px; font-weight: 700; margin-top: 6px; }
  .opened-color { color: var(--opened); }
  .closed-color { color: var(--closed); }
  .net-color { color: var(--net); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--muted); font-weight: 600; }
  canvas { max-height: 380px; }
  .note { color: var(--muted); font-size: 12px; margin-top: 10px; }
</style>
</head>
<body>
<div class="wrap">
  <h1 id="title">SonarCloud Weekly Issues</h1>
  <div class="sub" id="subtitle"></div>

  <div class="stats" id="stats"></div>

  <div class="card">
    <canvas id="chart"></canvas>
  </div>

  <div class="card">
    <table id="table"></table>
    <div class="note" id="note"></div>
  </div>
</div>

<script>
const DATA = __DATA__;

document.getElementById('title').textContent = 'SonarCloud Weekly Issues — ' + DATA.project;
document.getElementById('subtitle').textContent = 'Generated ' + new Date(DATA.generated).toLocaleString();

const totalOpened = DATA.opened.reduce((a,b)=>a+b,0);
const totalClosed = DATA.closed.reduce((a,b)=>a+b,0);
const net = totalOpened - totalClosed;

document.getElementById('stats').innerHTML = `
  <div class="stat"><div class="label">Currently open (right now)</div><div class="value">${DATA.currentlyOpen}</div></div>
  <div class="stat"><div class="label">Created in window</div><div class="value opened-color">${totalOpened}</div></div>
  <div class="stat"><div class="label">Closed in window</div><div class="value closed-color">${totalClosed}</div></div>
  <div class="stat"><div class="label">Net change (window)</div><div class="value net-color">${net >= 0 ? '+' + net : net}</div></div>
`;

new Chart(document.getElementById('chart'), {
  type: 'bar',
  data: {
    labels: DATA.labels,
    datasets: [
      { label: 'Created', data: DATA.opened, backgroundColor: '#ef6c6c' },
      { label: 'Closed', data: DATA.closed, backgroundColor: '#52c993' }
    ]
  },
  options: {
    responsive: true,
    plugins: {
      legend: { labels: { color: '#e6e8ee' } },
      title: { display: false }
    },
    scales: {
      x: { ticks: { color: '#9aa1b1' }, grid: { color: '#262a36' } },
      y: { beginAtZero: true, ticks: { color: '#9aa1b1' }, grid: { color: '#262a36' } }
    }
  }
});

let rows = '<tr><th>Week of</th><th>Created</th><th>Closed</th><th>Net</th></tr>';
DATA.labels.forEach((label, i) => {
  const o = DATA.opened[i], c = DATA.closed[i], n = o - c;
  rows += `<tr><td>${label}</td><td>${o}</td><td>${c}</td><td>${n >= 0 ? '+' + n : n}</td></tr>`;
});
document.getElementById('table').innerHTML = rows;
document.getElementById('note').textContent = DATA.note;
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="SonarCloud project key")
    ap.add_argument("--branch", default=None, help="Branch (default: main branch)")
    ap.add_argument("--weeks", type=int, default=12, help="Number of weeks to show (default 12)")
    ap.add_argument(
        "--lookback-buffer-weeks",
        type=int,
        default=52,
        help="Extra weeks searched before the window, to catch issues created "
             "earlier but closed inside the window (default 52). Increase this "
             "if issues in your project often stay open for a long time before "
             "being closed.",
    )
    ap.add_argument("--out", default="sonar_weekly_dashboard.html", help="Output HTML file path")
    args = ap.parse_args()

    if not args.project or not args.project.strip():
        raise SystemExit(
            "No project key was provided (--project was empty). "
            "In GitHub Actions this usually means the SONAR_PROJECT_KEY "
            "repository *variable* isn't set, or it was added as a *secret* "
            "instead of a variable (secrets aren't exposed via ${{ vars.* }}). "
            "Check Settings > Secrets and variables > Actions > Variables tab."
        )

    token = os.environ.get("SONAR_TOKEN")
    if not token:
        token = input("SonarCloud token: ").strip()
    if not token:
        raise SystemExit("A SonarCloud token is required (set SONAR_TOKEN or enter it when prompted).")

    today = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    window_start = iso_week_start(today) - dt.timedelta(weeks=args.weeks - 1)
    fetch_start = window_start - dt.timedelta(weeks=args.lookback_buffer_weeks)

    print(f"Fetching issues for '{args.project}' created on/after {fetch_start.date()} ...")
    issues = fetch_all_issues(
        token,
        args.project,
        args.branch,
        {"createdAfter": fetch_start.strftime("%Y-%m-%d")},
    )
    print(f"Fetched {len(issues)} issues total.")

    weeks = [window_start + dt.timedelta(weeks=i) for i in range(args.weeks)]
    week_labels = [w.strftime("%Y-%m-%d") for w in weeks]
    opened = {w: 0 for w in weeks}
    closed = {w: 0 for w in weeks}
    week_set = set(weeks)

    def bucket_for(date):
        wk = iso_week_start(date)
        return wk if wk in week_set else None

    for issue in issues:
        created = parse_sonar_date(issue["creationDate"])
        wk = bucket_for(created)
        if wk is not None:
            opened[wk] += 1
        close_date_str = issue.get("closeDate")
        if close_date_str:
            closed_dt = parse_sonar_date(close_date_str)
            wk2 = bucket_for(closed_dt)
            if wk2 is not None:
                closed[wk2] += 1

    opened_series = [opened[w] for w in weeks]
    closed_series = [closed[w] for w in weeks]

    print("Fetching current open-issue total (snapshot, all-time)...")
    currently_open = fetch_total_count(
        token,
        args.project,
        args.branch,
        {"statuses": "OPEN,CONFIRMED,REOPENED"},
    )
    print(f"Currently open: {currently_open}")

    note = (
        f"Issues searched from {fetch_start.date()} onward "
        f"({args.lookback_buffer_weeks}-week lookback buffer before the displayed window) "
        "to catch issues created earlier but closed inside the window. "
        "If issues in this project can stay open longer than that, increase "
        "--lookback-buffer-weeks and re-run."
    )

    html = build_html(
        args.project,
        week_labels,
        opened_series,
        closed_series,
        dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        note,
        currently_open,
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nDashboard written to: {os.path.abspath(args.out)}")
    print("Open it in your browser to view the chart and table.")


if __name__ == "__main__":
    main()
