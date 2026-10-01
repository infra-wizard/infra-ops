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
1. Fetches issues created within the displayed weekly window ("created" series).
2. Separately fetches CLOSED issues for the project — by default with no
   limit on how far back they were created, since an issue's closeDate is
   independent of its creationDate (a bug filed a year ago can close this
   week) — and buckets them by closeDate ("closed" series).
3. Writes sonar_weekly_dashboard.html with a bar chart + table, plus a
   breakdown by issue type (Bug / Vulnerability / Code Smell).

Usage
-----
    export SONAR_TOKEN="your_sonarcloud_token"

    # Single project
    python3 sonar_weekly_dashboard.py --projects my-project-key --weeks 12

    # Multiple projects — one dashboard, with a dropdown to switch between them
    python3 sonar_weekly_dashboard.py --projects "proj-key-1,proj-key-2,proj-key-3"

    # Optional: specific branch (defaults to each project's main branch;
    # applies to all projects passed in this run)
    python3 sonar_weekly_dashboard.py --projects my-project-key --branch develop

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
ISSUE_TYPES = ["BUG", "VULNERABILITY", "CODE_SMELL"]


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


def fetch_open_by_type(token, project_key, branch, extra_params):
    """One API call using the 'types' facet to get open-issue counts per type."""
    params = {
        "componentKeys": project_key,
        "ps": 1,
        "p": 1,
        "facets": "types",
        **extra_params,
    }
    if branch:
        params["branch"] = branch
    data = api_get("issues/search", token, params)
    counts = {t: 0 for t in ISSUE_TYPES}
    for facet in data.get("facets", []):
        if facet.get("property") == "types":
            for v in facet.get("values", []):
                if v["val"] in counts:
                    counts[v["val"]] = v["count"]
    return counts


def load_history(path):
    """Load the persisted snapshot history. Returns {} if the file doesn't exist yet."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"WARNING: could not read {path}, starting a fresh history.")
        return {}


def prune_history(history, days):
    """Keep only entries whose date is within the last `days` days."""
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days - 1)
    kept = {}
    for date_str, entry in history.items():
        try:
            d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= cutoff:
            kept[date_str] = entry
    return kept


def save_history(path, history):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)


def iso_week_start(d):
    monday = d - dt.timedelta(days=d.weekday())
    return dt.datetime(monday.year, monday.month, monday.day)


def parse_sonar_date(s):
    # SonarCloud dates look like "2024-05-01T10:22:31+0000"
    return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def compute_project_data(token, project, branch, weeks_n, lookback_buffer_weeks):
    """Fetch and bucket all data for one project. Returns a dict ready for build_html."""
    today = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    window_start = iso_week_start(today) - dt.timedelta(weeks=weeks_n - 1)
    window_end = window_start + dt.timedelta(weeks=weeks_n)  # exclusive

    weeks = [window_start + dt.timedelta(weeks=i) for i in range(weeks_n)]
    week_labels = [w.strftime("%Y-%m-%d") for w in weeks]
    week_set = set(weeks)

    def bucket_for(date):
        wk = iso_week_start(date)
        return wk if wk in week_set else None

    print(f"[{project}] Fetching issues created between {window_start.date()} and {window_end.date()} ...")
    created_params = {
        "createdAfter": window_start.strftime("%Y-%m-%d"),
        "createdBefore": window_end.strftime("%Y-%m-%d"),
    }
    created_issues = fetch_all_issues(token, project, branch, created_params)
    print(f"[{project}] Fetched {len(created_issues)} issues created in window.")

    closed_params = {"statuses": "CLOSED"}
    if lookback_buffer_weeks is not None:
        lookback_start = window_start - dt.timedelta(weeks=lookback_buffer_weeks)
        closed_params["createdAfter"] = lookback_start.strftime("%Y-%m-%d")
        print(f"[{project}] Fetching CLOSED issues created on/after {lookback_start.date()} "
              f"(--lookback-buffer-weeks={lookback_buffer_weeks}) ...")
    else:
        print(f"[{project}] Fetching ALL closed issues for this project (no creation-date limit) ...")
    closed_issues = fetch_all_issues(token, project, branch, closed_params)
    print(f"[{project}] Fetched {len(closed_issues)} closed issues total.")
    with_close_date = [i for i in closed_issues if i.get("closeDate")]
    print(f"[{project}]   of which {len(with_close_date)} have a closeDate set.")
    if closed_issues and not with_close_date:
        print(f"[{project}]   NOTE: none of the closed issues returned by the API have a "
              "closeDate field — your SonarCloud instance/plan may not expose "
              "it for this project.")

    opened = {w: 0 for w in weeks}
    closed = {w: 0 for w in weeks}
    created_by_type = {t: 0 for t in ISSUE_TYPES}
    closed_by_type = {t: 0 for t in ISSUE_TYPES}

    for issue in created_issues:
        issue_type = issue.get("type")
        created = parse_sonar_date(issue["creationDate"])
        wk = bucket_for(created)
        if wk is not None:
            opened[wk] += 1
            if issue_type in created_by_type:
                created_by_type[issue_type] += 1

    for issue in with_close_date:
        issue_type = issue.get("type")
        closed_dt = parse_sonar_date(issue["closeDate"])
        wk2 = bucket_for(closed_dt)
        if wk2 is not None:
            closed[wk2] += 1
            if issue_type in closed_by_type:
                closed_by_type[issue_type] += 1

    print(f"[{project}] Fetching current open-issue total (snapshot, all-time)...")
    currently_open = fetch_total_count(
        token, project, branch, {"statuses": "OPEN,CONFIRMED,REOPENED"},
    )
    print(f"[{project}] Currently open: {currently_open}")

    print(f"[{project}] Fetching open-issue breakdown by type...")
    open_by_type = fetch_open_by_type(
        token, project, branch, {"statuses": "OPEN,CONFIRMED,REOPENED"},
    )
    print(f"[{project}] Open by type: {open_by_type}")

    if lookback_buffer_weeks is not None:
        note = (
            f"'Created' counts issues created in the displayed window. 'Closed' counts "
            f"issues whose closeDate falls in the window, searched among CLOSED issues "
            f"created in the last {lookback_buffer_weeks} weeks before the window. "
            "If closures still look too low, drop --lookback-buffer-weeks entirely to "
            "search all closed issues regardless of creation date."
        )
    else:
        note = (
            "'Created' counts issues created in the displayed window. 'Closed' counts "
            "issues whose closeDate falls in the window, searched across ALL closed "
            "issues for this project regardless of when they were created."
        )

    return {
        "project": project,
        "labels": week_labels,
        "opened": [opened[w] for w in weeks],
        "closed": [closed[w] for w in weeks],
        "note": note,
        "currentlyOpen": currently_open,
        "openByType": open_by_type,
        "createdByType": created_by_type,
        "closedByType": closed_by_type,
    }


def build_html(projects, generated_at, history):
    data = {"generated": generated_at, "projects": projects, "history": history}
    return TEMPLATE.replace("__DATA__", json.dumps(data))


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
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
  .grid2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 24px;
  }
  @media (max-width: 700px) { .grid2 { grid-template-columns: 1fr; } }
  .card-title { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 12px; }
  .type-stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 18px;
  }
  .type-stat {
    text-align: center;
    padding: 10px 6px;
    border-radius: 10px;
    background: #1d2029;
    border: 1px solid var(--border);
  }
  .type-stat .n { font-size: 24px; font-weight: 700; }
  .type-stat .t { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .top-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .project-select {
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    min-width: 200px;
  }
  .project-select:focus { outline: 1px solid var(--net); }
</style>
</head>
<body>
<div class="wrap">
  <div class="top-row">
    <div>
      <h1 id="title">SonarCloud Weekly Issues</h1>
      <div class="sub" id="subtitle"></div>
    </div>
    <select id="projectSelect" class="project-select"></select>
  </div>

  <div class="stats" id="stats"></div>

  <div class="card">
    <div class="card-title">Currently open — last 30 days</div>
    <canvas id="trendChart"></canvas>
    <div class="note" id="trendNote"></div>
  </div>

  <div class="card">
    <canvas id="chart"></canvas>
  </div>

  <div class="grid2">
    <div class="card">
      <div class="card-title">Currently open, by type</div>
      <div class="type-stats" id="typeStats"></div>
      <canvas id="typeChart"></canvas>
    </div>
    <div class="card">
      <div class="card-title">Created / closed in window, by type</div>
      <table id="typeTable"></table>
    </div>
  </div>

  <div class="card">
    <table id="table"></table>
    <div class="note" id="note"></div>
  </div>
</div>

<script>
const DATA = __DATA__;
const TYPE_LABELS = { BUG: 'Bug', VULNERABILITY: 'Vulnerability', CODE_SMELL: 'Code Smell' };
const TYPE_COLORS = { BUG: '#ef6c6c', VULNERABILITY: '#f0a35c', CODE_SMELL: '#7aa8ff' };

let mainChart = null;
let typeChart = null;
let trendChart = null;

const select = document.getElementById('projectSelect');
DATA.projects.forEach((p, i) => {
  const opt = document.createElement('option');
  opt.value = i;
  opt.textContent = p.project;
  select.appendChild(opt);
});
select.style.display = DATA.projects.length > 1 ? '' : 'none';
select.addEventListener('change', () => renderProject(parseInt(select.value, 10)));

function renderProject(idx) {
  const proj = DATA.projects[idx];

  document.getElementById('title').textContent = 'SonarCloud Weekly Issues — ' + proj.project;
  document.getElementById('subtitle').textContent = 'Generated ' + new Date(DATA.generated).toLocaleString();

  const totalOpened = proj.opened.reduce((a,b)=>a+b,0);
  const totalClosed = proj.closed.reduce((a,b)=>a+b,0);
  const net = totalOpened - totalClosed;

  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="label">Currently open (right now)</div><div class="value">${proj.currentlyOpen}</div></div>
    <div class="stat"><div class="label">Created in window</div><div class="value opened-color">${totalOpened}</div></div>
    <div class="stat"><div class="label">Closed in window</div><div class="value closed-color">${totalClosed}</div></div>
    <div class="stat"><div class="label">Net change (window)</div><div class="value net-color">${net >= 0 ? '+' + net : net}</div></div>
  `;

  const historyDates = Object.keys(DATA.history).sort();
  const trendSeries = historyDates.map(d => {
    const entry = DATA.history[d][proj.project];
    return entry ? entry.currentlyOpen : null;
  });
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(document.getElementById('trendChart'), {
    type: 'line',
    data: {
      labels: historyDates,
      datasets: [{
        label: 'Currently open',
        data: trendSeries,
        borderColor: '#7aa8ff',
        backgroundColor: 'rgba(122,168,255,0.15)',
        fill: true,
        tension: 0.25,
        spanGaps: true,
        pointRadius: 3
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#e6e8ee' } } },
      scales: {
        x: { ticks: { color: '#9aa1b1' }, grid: { color: '#262a36' } },
        y: { beginAtZero: true, ticks: { color: '#9aa1b1' }, grid: { color: '#262a36' } }
      }
    }
  });
  document.getElementById('trendNote').textContent = historyDates.length < 2
    ? 'Only one snapshot recorded so far — the trend fills in as this workflow runs on its schedule.'
    : `${historyDates.length} snapshots recorded (${historyDates[0]} to ${historyDates[historyDates.length - 1]}).`;

  if (mainChart) mainChart.destroy();
  mainChart = new Chart(document.getElementById('chart'), {
    type: 'bar',
    data: {
      labels: proj.labels,
      datasets: [
        { label: 'Created', data: proj.opened, backgroundColor: '#ef6c6c' },
        { label: 'Closed', data: proj.closed, backgroundColor: '#52c993' }
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
  proj.labels.forEach((label, i) => {
    const o = proj.opened[i], c = proj.closed[i], n = o - c;
    rows += `<tr><td>${label}</td><td>${o}</td><td>${c}</td><td>${n >= 0 ? '+' + n : n}</td></tr>`;
  });
  document.getElementById('table').innerHTML = rows;
  document.getElementById('note').textContent = proj.note;

  const typeKeys = Object.keys(proj.openByType);

  document.getElementById('typeStats').innerHTML = typeKeys.map(k => `
    <div class="type-stat">
      <div class="n" style="color:${TYPE_COLORS[k] || '#fff'}">${proj.openByType[k]}</div>
      <div class="t">${TYPE_LABELS[k] || k}</div>
    </div>
  `).join('');

  if (typeChart) typeChart.destroy();
  typeChart = new Chart(document.getElementById('typeChart'), {
    type: 'doughnut',
    data: {
      labels: typeKeys.map(k => TYPE_LABELS[k] || k),
      datasets: [{
        data: typeKeys.map(k => proj.openByType[k]),
        backgroundColor: typeKeys.map(k => TYPE_COLORS[k] || '#888')
      }]
    },
    options: {
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: '#e6e8ee',
            generateLabels: (chart) => {
              const ds = chart.data.datasets[0];
              return chart.data.labels.map((label, i) => ({
                text: `${label}: ${ds.data[i]}`,
                fillStyle: ds.backgroundColor[i],
                strokeStyle: ds.backgroundColor[i],
                index: i
              }));
            }
          }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.label}: ${ctx.parsed}`
          }
        }
      }
    }
  });

  let typeRows = '<tr><th>Type</th><th>Created</th><th>Closed</th></tr>';
  typeKeys.forEach(k => {
    typeRows += `<tr><td>${TYPE_LABELS[k] || k}</td><td>${proj.createdByType[k] || 0}</td><td>${proj.closedByType[k] || 0}</td></tr>`;
  });
  document.getElementById('typeTable').innerHTML = typeRows;
}

renderProject(0);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--projects",
        default=None,
        help="Comma-separated SonarCloud project keys, e.g. "
             "'proj-key-1,proj-key-2'. One dashboard is generated with a "
             "dropdown to switch between them.",
    )
    ap.add_argument("--project", default=None, help="Single SonarCloud project key (alias for --projects with one value)")
    ap.add_argument("--branch", default=None, help="Branch (default: main branch). Applies to all projects passed.")
    ap.add_argument("--weeks", type=int, default=12, help="Number of weeks to show (default 12)")
    ap.add_argument(
        "--lookback-buffer-weeks",
        type=int,
        default=None,
        help="By default, closed issues are searched with NO limit on how far "
             "back they were created (so closures of old issues are never "
             "missed). For very large/old projects this can mean scanning a "
             "lot of history; pass this to only search CLOSED issues created "
             "in the last N weeks before the displayed window, as a "
             "performance shortcut. Leave unset unless you hit rate limits "
             "or the run is too slow.",
    )
    ap.add_argument("--out", default="sonar_weekly_dashboard.html", help="Output HTML file path")
    ap.add_argument(
        "--history-file",
        default="sonar_history.json",
        help="Path to a JSON file that accumulates one 'currently open' snapshot per "
             "day across runs, so the page can show a trend over time instead of just "
             "this run's numbers. This file should be committed back to your repo "
             "between runs (the provided GitHub Actions workflow does this) — it is "
             "NOT the same as --out, which is build output and gets overwritten.",
    )
    ap.add_argument("--history-days", type=int, default=30, help="How many days of history to keep (default 30)")
    args = ap.parse_args()

    raw = args.projects if args.projects else args.project
    project_keys = [p.strip() for p in (raw or "").split(",") if p.strip()]

    if not project_keys:
        raise SystemExit(
            "No project key(s) provided (--projects was empty). "
            "In GitHub Actions this usually means the SONAR_PROJECT_KEY(S) "
            "repository *variable* isn't set, or it was added as a *secret* "
            "instead of a variable (secrets aren't exposed via ${{ vars.* }}). "
            "Check Settings > Secrets and variables > Actions > Variables tab."
        )

    token = os.environ.get("SONAR_TOKEN")
    if not token:
        token = input("SonarCloud token: ").strip()
    if not token:
        raise SystemExit("A SonarCloud token is required (set SONAR_TOKEN or enter it when prompted).")

    projects_data = []
    for key in project_keys:
        print(f"\n=== {key} ===")
        projects_data.append(
            compute_project_data(token, key, args.branch, args.weeks, args.lookback_buffer_weeks)
        )

    print(f"\nUpdating history file: {args.history_file}")
    history = load_history(args.history_file)
    today_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    history[today_str] = {
        pd["project"]: {"currentlyOpen": pd["currentlyOpen"], "openByType": pd["openByType"]}
        for pd in projects_data
    }
    history = prune_history(history, args.history_days)
    save_history(args.history_file, history)
    print(f"History now has {len(history)} day(s) on record (kept last {args.history_days} days).")

    html = build_html(projects_data, dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), history)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nDashboard written to: {os.path.abspath(args.out)}")
    print("Open it in your browser to view the chart and table.")


if __name__ == "__main__":
    main()
