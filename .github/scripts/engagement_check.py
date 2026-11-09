#!/usr/bin/env python3
"""Fail when a file is outside INDEX.md or when work has no complete staffing row.

Run from the root of an engagement repository: in the session hook (--hook) and on every
push (--issues, which also reads the issues with gh). Standard library only. This file is
the one source. An identical copy in Dankaro-projects/.github serves GitHub Actions, and a
test in claude-team fails when they differ.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                          text=True, check=True).stdout.strip())
ROOT_FILES = {".gitignore", "CLAUDE.md", "HANDOFF.md", "INDEX.md", "README.md"}
SECOND_LEVEL = {
    "workshops": r"\d{2}-[a-z0-9-]+",
    "scripts": r"[a-z0-9-]+",
    "tools": r"[a-z0-9_.-]+",
}
KNOWLEDGE_PAGES = {"business.md", "customers.md", "data-map.md", "corrections.md"}
# Binary documents and data belong in the vault, not in git.
VAULT_ONLY = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".zip", ".csv",
              ".parquet", ".sqlite", ".db", ".png", ".jpg", ".jpeg", ".msg", ".eml"}
SCRIPT_TYPES = {".py", ".sh", ".ipynb", ".js", ".ts"}
MAX_BYTES = 5 * 1024 * 1024
ROW = re.compile(r"^\|\s*`([^`]+?)/?`\s*\|", re.M)
CURRENT = re.compile(r"^\|\s*`[^`]+`\s*\|[^|]*\|\s*`([^`]+)`\s*\|", re.M)
WORK_ROW = re.compile(r"^\|\s*`((?:workshops|scripts)/[^`]+)`\s*\|[^|]*\|[^|]*\|([^|]*)\|", re.M)
LISTED = {"workshops", "scripts"}
ISSUE = re.compile(r"#(\d+)")
DELIVERABLE = re.compile(r"^Deliverable: yes\s*$", re.M)
STAFFING = Path("tools") / "staffing.md"
HEADER = ["Deliverable", "Issue", "Talent", "Accountable", "Status", "Result"]
SEPARATOR = re.compile(r"\|[\s:|-]+\|")
SKILL_CELL = re.compile(r"^(`[a-z0-9:_-]+`|Gap: (new skill|outside specialist)\b.*|None needed: .+)")


def git(*args):
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True)
    return [f for f in out.stdout.decode().split("\0") if f]


def rows(index):
    return set(ROW.findall(index.read_text(encoding="utf-8"))) if index.exists() else set()


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def staffing():
    """Return the problems of the staffing page, the issues it covers and those still Pending."""
    path = ROOT / STAFFING
    if not path.exists():
        return [f"{STAFFING} is missing"], set(), set()
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("|")]
    if not lines or cells(lines[0]) != HEADER:
        return [f"{STAFFING}: the table must have the columns {', '.join(HEADER)}"], set(), set()
    problems, covered, pending = [], set(), set()
    for line in lines[1:]:
        if SEPARATOR.fullmatch(line.strip()):
            continue
        row = cells(line)
        name = row[0] or "(unnamed row)"
        if len(row) != len(HEADER) or not all(row):
            problems.append(f"{STAFFING}: {name}: every column must be filled")
            continue
        numbers = ISSUE.findall(row[1])
        if not numbers:
            problems.append(f"{STAFFING}: {name}: the Issue column must name an issue such as #3")
        covered.update(numbers)
        if row[5].lower() == "pending":
            pending.update(numbers)
        if not SKILL_CELL.match(row[2]):
            problems.append(f"{STAFFING}: {name}: name a skill in backticks, or write "
                            "Gap: new skill, Gap: outside specialist or None needed: reason")
    return problems, covered, pending


def deliverables():
    out = subprocess.run(["gh", "issue", "list", "--state", "all", "--limit", "1000",
                          "--json", "number,body,state"], cwd=ROOT, capture_output=True, check=True)
    return [(str(i["number"]), i["state"]) for i in json.loads(out.stdout) if DELIVERABLE.search(i["body"] or "")]


def check(with_issues=False):
    problems = []
    index = ROOT / "INDEX.md"
    folders = rows(index)
    if not index.exists():
        problems.append("INDEX.md is missing")
    # The vault exists only on K16, so its rows cannot be checked on GitHub.
    has_vault = (ROOT / "vault" / "INDEX.md").exists()
    text = index.read_text(encoding="utf-8") if index.exists() else ""
    current = set(CURRENT.findall(text))
    for name in sorted(folders | current):
        if name.startswith("vault/") and not has_vault:
            continue
        if not (ROOT / name).exists():
            problems.append(f"INDEX.md lists {name}, which does not exist")

    for name in git("ls-files", "-z", "--cached", "--others", "--exclude-standard"):
        path = Path(name)
        if not (ROOT / path).exists():
            continue
        parts = path.parts
        if len(parts) == 1:
            if name not in ROOT_FILES:
                problems.append(f"{name}: loose file in the root; move it to a listed folder")
            continue
        top = parts[0]
        if top not in folders:
            problems.append(f"{name}: folder {top}/ has no row in INDEX.md")
        if top in SECOND_LEVEL and len(parts) > 2 and not re.fullmatch(SECOND_LEVEL[top], parts[1]):
            problems.append(f"{name}: {parts[1]} does not follow the naming of {top}/")
        if top in SECOND_LEVEL and top != "tools" and len(parts) == 2 and path.name != "README.md":
            problems.append(f"{name}: place it in a subfolder of {top}/")
        if top in LISTED and len(parts) > 2 and f"{top}/{parts[1]}" not in folders:
            problems.append(f"{name}: {top}/{parts[1]}/ has no row in INDEX.md")
        if top == "knowledge" and (len(parts) > 2 or path.name not in KNOWLEDGE_PAGES):
            problems.append(f"{name}: knowledge/ holds only its four fixed pages")
        if top != "vault" and path.suffix.lower() in VAULT_ONLY:
            problems.append(f"{name}: this file type belongs in vault/")
        if top != "vault" and (ROOT / path).stat().st_size > MAX_BYTES:
            problems.append(f"{name}: over 5 MB; it belongs in vault/")

    for name in git("ls-files", "-z", "--", "vault"):
        if name != "vault/README.md":
            problems.append(f"{name}: tracked by git; the vault must stay out of git")

    vault = ROOT / "vault"
    vault_index = vault / "INDEX.md"
    if vault_index.exists():
        listed = rows(vault_index)
        for entry in sorted(vault.iterdir()):
            if entry.name not in {"README.md", "INDEX.md"} and entry.name not in listed:
                problems.append(f"vault/{entry.name}: no row in vault/INDEX.md")
        for path in vault.rglob("*"):
            if path.is_file() and path.suffix.lower() in SCRIPT_TYPES:
                problems.append(f"{path.relative_to(ROOT)}: scripts belong in scripts/<topic>/")

    staffing_problems, covered, pending = staffing()
    problems += staffing_problems
    for item, issues in WORK_ROW.findall(text):
        if not set(ISSUE.findall(issues)) & covered:
            problems.append(f"{item}: none of its issues has a row in {STAFFING}")
    if with_issues:
        try:
            found = deliverables()
        except (subprocess.CalledProcessError, OSError, ValueError) as error:
            problems.append(f"could not read the open issues with gh: {error}")
            found = []
        for number, state in found:
            if state == "OPEN" and number not in covered:
                problems.append(f"Deliverable issue #{number} has no row in {STAFFING}")
            if state == "CLOSED" and number in pending:
                problems.append(f"Deliverable issue #{number} is closed: write how the talent performed "
                                f"in the Result column of {STAFFING}")
    return problems


def main():
    problems = check(with_issues="--issues" in sys.argv)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        sys.exit(2 if "--hook" in sys.argv else 1)
    print("structure ok")


if __name__ == "__main__":
    main()
