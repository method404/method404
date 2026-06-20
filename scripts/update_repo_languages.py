#!/usr/bin/env python3
import base64
import html
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone


API_BASE = "https://api.github.com"
README_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "README.md")
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
STATUS_CARD_PATH = os.path.join(ASSETS_DIR, "github-status-v2.svg")
LANGUAGES_CARD_PATH = os.path.join(ASSETS_DIR, "most-used-languages-v2.svg")

TECH_STACK_START = "<!-- tech-stack:start -->"
TECH_STACK_END = "<!-- tech-stack:end -->"
METRICS_START = "<!-- profile-metrics:start -->"
METRICS_END = "<!-- profile-metrics:end -->"

TECH_GROUPS = [
    ("Languages", [("Python", "py"), ("TypeScript", "ts"), ("JavaScript", "js"), ("HTML", "html"), ("CSS", "css"), ("SCSS", "sass"), ("Dart", "dart"), ("Go", "go"), ("C++", "cpp")]),
    ("Libraries", [("React", "react")]),
    ("Frameworks", [("Vue", "vue"), ("Next.js", "nextjs"), ("Flask", "flask")]),
    ("ETC", [("Node.js", "nodejs"), ("PostgreSQL", "postgres"), ("Docker", "docker"), ("Vercel", "vercel"), ("Git", "git"), ("VS Code", "vscode")]),
]

LANGUAGE_META = {
    "Vue": {"color": "#41b883", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/vuejs/vuejs-original.svg"},
    "Python": {"color": "#3572A5", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg"},
    "Dart": {"color": "#00B4AB", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/dart/dart-original.svg"},
    "TypeScript": {"color": "#3178c6", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/typescript/typescript-original.svg"},
    "JavaScript": {"color": "#f1e05a", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/javascript/javascript-original.svg"},
    "CSS": {"color": "#663399", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/css3/css3-original.svg"},
    "SCSS": {"color": "#c6538c", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/sass/sass-original.svg"},
    "C++": {"color": "#f34b7d", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/cplusplus/cplusplus-original.svg"},
    "HTML": {"color": "#e34c26", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/html5/html5-original.svg"},
    "Go": {"color": "#00ADD8", "icon": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/go/go-original-wordmark.svg"},
}


ICON_CACHE: dict[str, str] = {}


def resolve_token():
    for name in ("PROFILE_GH_TOKEN", "PROFILE_REPO_READ_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value, name

    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
        token = completed.stdout.strip()
        if token:
            return token, "gh auth token"
    except Exception:
        pass

    return None, None


def github_request(path: str, token: str | None, method: str = "GET", data: dict | None = None):
    url = f"{API_BASE}{path}"
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        method=method,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "method404-profile-readme-updater",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req) as response:
        payload = response.read().decode("utf-8")
        return (json.loads(payload) if payload else None), response.info()


def github_get(path: str, token: str | None):
    data, _ = github_request(path, token)
    return data


def github_graphql(query: str, variables: dict, token: str):
    data, _ = github_request("/graphql", token, method="POST", data={"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def fetch_data_uri(url: str):
    if url in ICON_CACHE:
        return ICON_CACHE[url]

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "method404-profile-readme-updater"})
        with urllib.request.urlopen(req) as response:
            raw = response.read()
    except Exception:
        return ""

    mime = "image/svg+xml"
    encoded = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:{mime};base64,{encoded}"
    ICON_CACHE[url] = data_uri
    return data_uri


def list_repos(username: str, token: str | None):
    repos = []
    page = 1
    while True:
        if token:
            query = urllib.parse.urlencode(
                {
                    "affiliation": "owner",
                    "visibility": "all",
                    "sort": "updated",
                    "per_page": 100,
                    "page": page,
                }
            )
            path = f"/user/repos?{query}"
        else:
            query = urllib.parse.urlencode({"per_page": 100, "page": page, "sort": "updated"})
            path = f"/users/{username}/repos?{query}"

        chunk = github_get(path, token)
        if not chunk:
            break

        repos.extend(repo for repo in chunk if not repo.get("fork"))
        page += 1

    return repos


def decode_file_content(item: dict):
    if item.get("encoding") != "base64":
        return ""
    return base64.b64decode(item["content"]).decode("utf-8", errors="ignore")


def fetch_root_contents(owner: str, repo: str, token: str | None):
    try:
        return github_get(f"/repos/{owner}/{repo}/contents", token)
    except Exception:
        return []


def fetch_text_file(owner: str, repo: str, path: str, token: str | None):
    try:
        item = github_get(f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}", token)
    except Exception:
        return ""
    return decode_file_content(item)


def aggregate_languages(repos: list[dict], token: str | None):
    totals = Counter()
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        data = github_get(f"/repos/{owner}/{name}/languages", token)
        for language, size in data.items():
            totals[language] += size
    return totals


def detect_repo_technologies(repos: list[dict], token: str | None):
    detected = set()
    language_totals = aggregate_languages(repos, token)

    if language_totals.get("Python"):
        detected.add("Python")
    if language_totals.get("TypeScript"):
        detected.add("TypeScript")
    if language_totals.get("JavaScript"):
        detected.add("JavaScript")
    if language_totals.get("HTML"):
        detected.add("HTML")
    if language_totals.get("CSS"):
        detected.add("CSS")
    if language_totals.get("SCSS"):
        detected.add("SCSS")
    if language_totals.get("Vue"):
        detected.add("Vue")
    if language_totals.get("Dart"):
        detected.add("Dart")
    if language_totals.get("Go"):
        detected.add("Go")
    if language_totals.get("C++"):
        detected.add("C++")

    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        contents = fetch_root_contents(owner, name, token)
        names = {item.get("name"): item for item in contents if item.get("type") == "file"}

        if "Dockerfile" in names or "docker-compose.yml" in names or "docker-compose.yaml" in names:
            detected.add("Docker")

        if "go.mod" in names or "go.sum" in names or any(file_name.endswith(".go") for file_name in names):
            detected.add("Go")

        if "vercel.json" in names:
            detected.add("Vercel")

        if "package.json" in names:
            package_json = fetch_text_file(owner, name, "package.json", token)
            try:
                package = json.loads(package_json)
            except Exception:
                package = {}

            deps = {}
            deps.update(package.get("dependencies", {}))
            deps.update(package.get("devDependencies", {}))

            if deps:
                detected.add("Node.js")
            if "react" in deps or "react-dom" in deps:
                detected.add("React")
            if "vue" in deps:
                detected.add("Vue")
            if "next" in deps:
                detected.add("Next.js")
            if "vercel" in deps:
                detected.add("Vercel")
            if any(dep in deps for dep in ("pg", "postgres", "postgresql", "pg-promise")):
                detected.add("PostgreSQL")

        pyproject = ""
        requirements = ""
        if "pyproject.toml" in names:
            pyproject = fetch_text_file(owner, name, "pyproject.toml", token).lower()
        if "requirements.txt" in names:
            requirements = fetch_text_file(owner, name, "requirements.txt", token).lower()

        python_manifest = "\n".join([pyproject, requirements])
        if python_manifest.strip():
            detected.add("Python")
        if "flask" in python_manifest:
            detected.add("Flask")
        if any(pkg in python_manifest for pkg in ("psycopg", "psycopg2", "asyncpg", "pg8000", "postgres")):
            detected.add("PostgreSQL")

    return detected, language_totals


def fetch_contribution_metrics(username: str, token: str | None):
    if not token:
        return None

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      viewer { login }
      rateLimit { remaining }
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              contributionYears
              contributionCalendar {
                totalContributions
                weeks {
                  contributionDays {
                    contributionCount
                    date
                  }
                }
              }
              restrictedContributionsCount
              totalCommitContributions
              totalIssueContributions
              totalPullRequestContributions
          totalPullRequestReviewContributions
        }
      }
    }
    """

    now = datetime.now(timezone.utc)
    current_year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    current = github_graphql(
        query,
        {
            "login": username,
            "from": current_year_start.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
        token,
    )["user"]["contributionsCollection"]

    years = current["contributionYears"]
    all_time_total = 0
    all_time_commit_total = 0

    for year in years:
        year_start = datetime(year, 1, 1, tzinfo=timezone.utc)
        year_end = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        year_data = github_graphql(
            query,
            {
                "login": username,
                "from": year_start.isoformat().replace("+00:00", "Z"),
                "to": year_end.isoformat().replace("+00:00", "Z"),
            },
            token,
        )["user"]["contributionsCollection"]
        all_time_total += year_data["contributionCalendar"]["totalContributions"]
        all_time_commit_total += year_data["totalCommitContributions"]

    _, headers = github_request("/user", token)
    scopes = headers.get("X-OAuth-Scopes", "") or ""
    has_read_user_scope = "read:user" in scopes.split(", ")

    return {
        "owned_repos": None,
        "current_year_contributions": current["contributionCalendar"]["totalContributions"],
        "current_year_commit_contributions": current["totalCommitContributions"],
        "all_time_contributions": all_time_total,
        "all_time_commit_contributions": all_time_commit_total,
        "restricted_contributions": current["restrictedContributionsCount"],
        "has_read_user_scope": has_read_user_scope,
        "weeks": current["contributionCalendar"]["weeks"],
    }


def join_words(words: list[str]):
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    if len(words) == 2:
        return f"{words[0]} and {words[1]}"
    return f"{', '.join(words[:-1])}, and {words[-1]}"


def build_tech_stack_block(detected: set[str], language_totals: Counter):
    sections = [TECH_STACK_START]

    if not detected:
        fallback = [name for name, _ in language_totals.most_common(6)]
        sections.extend(
            [
                "### Languages",
                "",
                f"`{join_words(fallback)}`" if fallback else "`No detected stack yet`",
            ]
        )
    else:
        for title, items in TECH_GROUPS:
            icons = [icon for tech, icon in items if tech in detected]
            if not icons:
                continue

            sections.extend(
                [
                    f"### {title}",
                    "",
                    f'<img src="https://skillicons.dev/icons?i={",".join(icons)}" alt="{title.lower()}" />',
                    "",
                ]
            )

    sections.append(TECH_STACK_END)
    return "\n".join(sections)


def build_metrics_block(metrics: dict | None, repo_count: int):
    return "\n".join(
        [
            METRICS_START,
            '<img src="./assets/github-status-v2.svg" alt="GitHub status" width="760" />',
            "",
            '<img src="./assets/most-used-languages-v2.svg" alt="Most used languages" width="760" />',
            METRICS_END,
        ]
    )


def generate_status_svg(metrics: dict | None, repo_count: int):
    current_year = datetime.now(timezone.utc).year

    if not metrics:
        stat_items = [
            ("Owned repos", str(repo_count)),
            ("Contributions", "Token needed"),
            ("Commit contributions", "Token needed"),
            ("All-time contributions", "Token needed"),
        ]
    else:
        commit_label = "Commit contributions"
        if metrics["restricted_contributions"] and not metrics["has_read_user_scope"]:
            commit_label = "Public-visible commits"

        stat_items = [
            ("Owned repos", str(repo_count)),
            (f"{current_year} contributions", str(metrics["current_year_contributions"])),
            (commit_label, str(metrics["current_year_commit_contributions"])),
            ("All-time contributions", str(metrics["all_time_contributions"])),
        ]

    width = 860
    height = 106
    padding = 28
    stat_w = 190
    stat_h = 44
    stat_gap = 12
    x_positions = [padding + idx * (stat_w + stat_gap) for idx in range(4)]

    stat_svgs = []
    for index, ((label, value), x) in enumerate(zip(stat_items, x_positions)):
        safe_label = html.escape(label.upper())
        safe_value = html.escape(value)
        stat_svgs.append(
            f'''
    <g transform="translate({x},26)">
      <text x="0" y="12" fill="var(--muted)" font-size="10" letter-spacing="0.1em" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif">{safe_label}</text>
      <text x="0" y="42" fill="var(--text)" font-size="28" font-weight="700" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif">{safe_value}</text>
    </g>'''
        )

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub status card">
  <style>
    :root {{
      --bg: #ffffff;
      --border: #d0d7de;
      --text: #24292f;
      --muted: #57606a;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0d1117;
        --border: #30363d;
        --text: #c9d1d9;
        --muted: #7d8590;
      }}
    }}
  </style>
  <rect width="{width}" height="{height}" rx="16" fill="var(--bg)"/>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="15.5" stroke="var(--border)"/>
  {''.join(stat_svgs)}
</svg>
'''

    os.makedirs(ASSETS_DIR, exist_ok=True)
    with open(STATUS_CARD_PATH, "w", encoding="utf-8") as fh:
        fh.write(svg)


def generate_languages_svg(language_totals: Counter):
    top_items = language_totals.most_common(8)
    width = 860
    height = max(220, 62 + len(top_items) * 46 + 18)
    padding = 28
    badge_left = 28
    badge_size = 18
    label_left = badge_left + badge_size + 10
    value_right = width - padding
    bar_left = 28
    bar_right = width - padding
    row_gap = 46
    start_y = 52
    max_value = top_items[0][1] if top_items else 1
    total_value = sum(value for _, value in top_items) or 1

    rows = []
    for index, (language, value) in enumerate(top_items):
        y = start_y + index * row_gap
        percent = (value / max_value) if max_value else 0
        share = value / total_value
        bar_width = max(8, (bar_right - bar_left) * percent)
        meta = LANGUAGE_META.get(language, {"color": "#2f81f7", "icon": ""})
        badge_fill = meta["color"]
        icon_markup = ""
        icon_uri = fetch_data_uri(meta["icon"]) if meta.get("icon") else ""
        if icon_uri:
            icon_markup = f'<image x="{badge_left}" y="{y - 2}" width="{badge_size}" height="{badge_size}" href="{icon_uri}" />'
        else:
            icon_markup = f'<circle cx="{badge_left + badge_size / 2}" cy="{y + 7}" r="7" fill="{badge_fill}" />'
        rows.append(
            f'''
  {icon_markup}
  <text x="{label_left}" y="{y + 12}" fill="var(--text)" font-size="14" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif">{html.escape(language)}</text>
  <text x="{value_right}" y="{y + 12}" text-anchor="end" fill="var(--muted)" font-size="11" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif">{share * 100:.1f}%</text>
  <rect x="{bar_left}" y="{y + 22}" width="{bar_right - bar_left}" height="8" rx="4" fill="var(--track)" />
  <rect x="{bar_left}" y="{y + 22}" width="{bar_width:.2f}" height="8" rx="4" fill="{badge_fill}" />'''
        )

    if not rows:
        rows.append(
            f'<text x="{padding}" y="{start_y}" fill="#7d8590" font-size="14" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif">No language data available.</text>'
        )

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Most used languages card">
  <style>
    :root {{
      --bg: #ffffff;
      --border: #d0d7de;
      --text: #24292f;
      --muted: #57606a;
      --track: #eaeef2;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0d1117;
        --border: #30363d;
        --text: #c9d1d9;
        --muted: #7d8590;
        --track: #161b22;
      }}
    }}
  </style>
  <rect width="{width}" height="{height}" rx="16" fill="var(--bg)"/>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="15.5" stroke="var(--border)"/>
  <text x="{padding}" y="30" fill="var(--text)" font-size="18" font-weight="700" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif">Most Used Languages</text>
  {''.join(rows)}
</svg>
'''

    with open(LANGUAGES_CARD_PATH, "w", encoding="utf-8") as fh:
        fh.write(svg)


def replace_block(readme: str, start_marker: str, end_marker: str, replacement: str):
    start = readme.find(start_marker)
    end = readme.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"README markers not found: {start_marker} / {end_marker}")
    end += len(end_marker)
    return readme[:start] + replacement + readme[end:]


def main():
    username = os.environ.get("GITHUB_USERNAME", "method404")
    token, token_source = resolve_token()

    repos = list_repos(username, token)
    detected, language_totals = detect_repo_technologies(repos, token)
    metrics = fetch_contribution_metrics(username, token)

    with open(README_PATH, "r", encoding="utf-8") as fh:
        readme = fh.read()

    readme = replace_block(readme, TECH_STACK_START, TECH_STACK_END, build_tech_stack_block(detected, language_totals))
    readme = replace_block(readme, METRICS_START, METRICS_END, build_metrics_block(metrics, repo_count=len(repos)))
    generate_status_svg(metrics, repo_count=len(repos))
    generate_languages_svg(language_totals)

    with open(README_PATH, "w", encoding="utf-8") as fh:
        fh.write(readme)

    source = token_source or "no auth"
    print(f"updated README using {len(repos)} repos via {source}", file=sys.stderr)


if __name__ == "__main__":
    main()
