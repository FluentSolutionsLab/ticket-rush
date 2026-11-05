#!/usr/bin/env python3
"""Create the 26-week plan on GitHub: labels, one milestone per week, one issue per checklist item.

Reads docs/specs/block-*.md. Idempotent: existing labels, milestones and issues (matched by title)
are left alone, so it is safe to re-run after editing a spec.

Block 3 lab weeks (13-17) go to the private labs repo, because MIT asks that lab solutions are
not published; everything else goes to the public repo.

Usage:
  python3 scripts/github/bootstrap_plan.py --dry-run
  python3 scripts/github/bootstrap_plan.py --start-date 2026-11-09

Requires the GitHub CLI (`gh`) authenticated with access to both repositories. Uses only the
REST API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PUBLIC_REPO = "FluentSolutionsLab/ticket-rush"
LABS_REPO = "FluentSolutionsLab/distsys-labs"
LAB_WEEKS = range(13, 18)  # weeks 13-17 are MIT lab work

SPECS = {
    1: "docs/specs/block-1-data-and-performance.md",
    2: "docs/specs/block-2-messaging-and-resilience.md",
    3: "docs/specs/block-3-distributed-systems.md",
    4: "docs/specs/block-4-cloud-architecture.md",
}

LABELS = {
    "block-1": ("1f77b4", "Block 1: data and performance"),
    "block-2": ("ff7f0e", "Block 2: messaging and resilience"),
    "block-3": ("2ca02c", "Block 3: distributed systems"),
    "block-4": ("9467bd", "Block 4: cloud architecture"),
    "task": ("c5def5", "One checklist item from a spec"),
    "wrap-up": ("fbca04", "End-of-block deliverables"),
    "aws": ("ff9900", "AWS work"),
    "azure": ("0078d4", "Azure work"),
    "lab": ("5319e7", "MIT 6.5840 lab work"),
}

AWS_WORDS = re.compile(r"\b(AWS|ECS|Fargate|DynamoDB|Aurora|ElastiCache|SNS|SQS|Lambda|CloudWatch|ADOT|ECR|IAM|Secrets Manager|Fault Injection Service|Cost Explorer)\b")
AZURE_WORDS = re.compile(r"\b(Azure|Cosmos DB|Container Apps|Service Bus|Key Vault|Entra|Application Insights|Chaos Studio|ACR|Flexible Server|Cost Management)\b")

WEEK_HEADING = re.compile(r"^### Week (\d+) — (.+)$")
CHECK_ITEM = re.compile(r"^- \[ \] (.+)$")


@dataclass
class Week:
    block: int
    number: int
    title: str
    anchor: str
    tasks: list[str] = field(default_factory=list)
    acceptance: str = ""


def github_slug(heading: str) -> str:
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\- ]", "", slug)
    return slug.replace(" ", "-")


def plain(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = text.replace("\\_", "_")
    return text.strip()


def parse_spec(block: int, path: Path) -> tuple[list[Week], list[str]]:
    weeks: list[Week] = []
    deliverables: list[str] = []
    current: Week | None = None
    in_deliverables = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = None
            in_deliverables = line.lower().startswith("## deliverables")
            continue
        m = WEEK_HEADING.match(line)
        if m:
            heading = line[4:]
            current = Week(block, int(m.group(1)), plain(m.group(2)), github_slug(heading))
            weeks.append(current)
            continue
        if line.startswith("### "):
            current = None
            continue
        item = CHECK_ITEM.match(line)
        if item and current:
            current.tasks.append(plain(item.group(1)))
        elif item and in_deliverables:
            deliverables.append(plain(item.group(1)))
        elif current and line.startswith("**Acceptance:**"):
            current.acceptance = plain(line[len("**Acceptance:**"):])
    return weeks, deliverables


def gh(*args: str, payload: dict | None = None) -> object:
    cmd = ["gh", "api", *args]
    if payload is not None:
        cmd += ["--input", "-"]
    for attempt in range(5):
        proc = subprocess.run(cmd, input=json.dumps(payload) if payload else None,
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else None
        if "rate limit" in proc.stderr.lower() or "abuse" in proc.stderr.lower():
            time.sleep(30 * (attempt + 1))
            continue
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    raise RuntimeError(f"{' '.join(cmd)} kept hitting rate limits")


def paged(path: str) -> list[dict]:
    out = gh("--paginate", "--slurp", path)
    return [item for page in out for item in page]  # type: ignore[union-attr]


def short_title(week: int, text: str) -> str:
    text = re.sub(r"`", "", text)
    text = re.split(r"[:;(]| — ", text)[0].strip() if len(text) > 90 else text
    if len(text) > 90:
        text = text[:87].rstrip() + "..."
    return f"W{week:02d}: {text}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", default="2026-11-09", help="Monday of week 1 (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    start = dt.date.fromisoformat(args.start_date)

    weeks: list[Week] = []
    wrapups: dict[int, list[str]] = {}
    for block, rel in SPECS.items():
        w, d = parse_spec(block, root / rel)
        weeks += w
        wrapups[block] = d

    def repo_for(week: int) -> str:
        return LABS_REPO if week in LAB_WEEKS else PUBLIC_REPO

    plan: list[tuple[str, int, str, str, list[str]]] = []  # repo, week, title, body, labels
    for w in weeks:
        spec_url = f"https://github.com/{PUBLIC_REPO}/blob/main/{SPECS[w.block]}#{w.anchor}"
        for task in w.tasks:
            labels = [f"block-{w.block}", "task"]
            if w.number in LAB_WEEKS:
                labels.append("lab")
            if w.block == 4:
                if AWS_WORDS.search(task) or "AWS" in w.title:
                    labels.append("aws")
                if AZURE_WORDS.search(task) or "Azure" in w.title:
                    labels.append("azure")
            body = (f"**Week {w.number}: {w.title}**\n\n"
                    f"### Task\n{task}\n\n"
                    f"### Week acceptance criteria\n{w.acceptance or '_See the spec._'}\n\n"
                    f"Spec: {spec_url}\n")
            plan.append((repo_for(w.number), w.number, short_title(w.number, task), body, labels))

    last_week = {1: 6, 2: 12, 3: 18, 4: 26}
    wrapup_extra = {
        1: ["Final write-up in docs/block1-writeup.md (template in the Block 1 spec)",
            "Benchmark table in docs/benchmarks/block1-concurrency.md complete",
            "Tag the release: block-1-monolith"],
        2: ["Tag the release: block-2-event-driven"],
        3: ["Tag the release: block-3-distributed"],
        4: ["Tag the release: block-4-cloud", "Update the README's results section"],
    }
    for block, items in wrapups.items():
        checklist = "\n".join(f"- [ ] {i}" for i in items + wrapup_extra[block])
        body = (f"Everything that closes Block {block}.\n\n{checklist}\n\n"
                f"Spec: https://github.com/{PUBLIC_REPO}/blob/main/{SPECS[block]}\n")
        plan.append((PUBLIC_REPO, last_week[block], f"Block {block} wrap-up: deliverables and tag",
                     body, [f"block-{block}", "wrap-up"]))

    week_titles = {w.number: (w.block, w.title, w.anchor) for w in weeks}
    repos = sorted({p[0] for p in plan})
    print(f"{len(weeks)} weeks, {len(plan)} issues "
          f"({sum(1 for p in plan if p[0] == PUBLIC_REPO)} public, {sum(1 for p in plan if p[0] == LABS_REPO)} labs)")
    if args.dry_run:
        for repo, week, title, _, labels in plan:
            print(f"  [{repo.split('/')[1]}] {title}  {labels}")
        return 0

    for repo in repos:
        existing = {l["name"] for l in paged(f"repos/{repo}/labels?per_page=100")}
        for name, (color, desc) in LABELS.items():
            if name not in existing:
                gh(f"repos/{repo}/labels", payload={"name": name, "color": color, "description": desc})
                print(f"label {repo} {name}")

        milestones = {m["title"]: m["number"] for m in paged(f"repos/{repo}/milestones?state=all&per_page=100")}
        repo_weeks = sorted({p[1] for p in plan if p[0] == repo})
        for n in repo_weeks:
            block, title, anchor = week_titles[n]
            mtitle = f"Week {n:02d}: {title}"
            due = start + dt.timedelta(days=7 * n - 1)
            if mtitle not in milestones:
                m = gh(f"repos/{repo}/milestones", payload={
                    "title": mtitle,
                    "due_on": f"{due.isoformat()}T23:59:59Z",
                    "description": f"Block {block}. Spec: https://github.com/{PUBLIC_REPO}/blob/main/{SPECS[block]}#{anchor}",
                })
                milestones[mtitle] = m["number"]  # type: ignore[index]
                print(f"milestone {repo} {mtitle} (due {due})")

        issues = {i["title"] for i in paged(f"repos/{repo}/issues?state=all&per_page=100") if "pull_request" not in i}
        for r, week, title, body, labels in plan:
            if r != repo or title in issues:
                continue
            block, wtitle, _ = week_titles[week]
            gh(f"repos/{repo}/issues", payload={
                "title": title, "body": body, "labels": labels,
                "milestone": milestones[f"Week {week:02d}: {wtitle}"],
            })
            issues.add(title)
            print(f"issue {repo} {title}")
            time.sleep(1.5)  # stay under GitHub's content-creation rate limits
    return 0


if __name__ == "__main__":
    sys.exit(main())
