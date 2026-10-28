#!/usr/bin/env python3
"""Supabase grants guard: a migration that creates a table or view in public
must grant Data API access to it in the same file.

Since 30 October 2026 Supabase no longer grants new tables in public to anon,
authenticated and service_role automatically. A table without grants returns
42501 through supabase-js, PostgREST and GraphQL, and every rebuild from
migrations recreates it without access. See Dankaro-projects/claude-team#21.

The same file runs in three places. Keep the copy in Dankaro-projects/.github
(.github/scripts/supabase_grants_guard.py) identical to this one.

  hook            Claude Code PreToolUse hook (Write, Edit, MultiEdit, Bash).
                  Write and Edit of a migration are checked on the resulting
                  text; a git commit is checked on the staged migrations.
                  A failing check is denied with the reason.
  commit [dir]    Check the staged migrations of the repository in dir.
  ci BASE HEAD    Check the migrations added or changed between two commits
                  (GitHub Actions). Exit status 1 on a failure.
  files PATH...   Check the given files.

A table that must stay closed to the Data API is declared in the migration
with a comment line:  -- grants-check: no-api <table> <reason>
"""
import json
import os
import re
import subprocess
import sys

MIGRATION = re.compile(r'(^|/)supabase/migrations/[^/]+\.sql$')
ROLES = ('anon', 'authenticated', 'service_role')
IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
QUALIFIED = r'(%s)(?:\s*\.\s*(%s))?' % (IDENT, IDENT)

CREATE_TABLE = re.compile(
    r'\bcreate\s+(?:(global|local)\s+)?(temp|temporary|unlogged\s+)?\s*table\s+'
    r'(?:if\s+not\s+exists\s+)?' + QUALIFIED + r'(\s+partition\s+of\b)?',
    re.I)
CREATE_VIEW = re.compile(
    r'\bcreate\s+(?:or\s+replace\s+)?(?:temp\s+|temporary\s+)?(?:recursive\s+)?'
    r'(materialized\s+)?view\s+(?:if\s+not\s+exists\s+)?' + QUALIFIED,
    re.I)
GRANT = re.compile(r'\bgrant\s+(.*?)\bon\s+(.*?)\bto\s+([^;]*)', re.I | re.S)
DEFAULT_PRIVS = re.compile(r'\balter\s+default\s+privileges\b[^;]*\bgrant\b[^;]*\bon\s+tables\b', re.I | re.S)
NO_API = re.compile(r'--\s*grants-check:\s*no-api\s+(%s)(?:\s*\.\s*(%s))?\s+\S' % (IDENT, IDENT), re.I)


def unquote(name):
    return name[1:-1] if name.startswith('"') else name.lower()


def relation(first, second):
    """Return the relation name when it is in public (or unqualified), else None."""
    if second is None:
        return unquote(first)
    return unquote(second) if unquote(first) == 'public' else None


def strip_comments(sql):
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    return re.sub(r'--[^\n]*', ' ', sql)


def check_sql(text):
    """Return a list of problems (plain sentences) for one migration."""
    declared = {relation(m.group(1), m.group(2)) for m in NO_API.finditer(text)}
    sql = strip_comments(text)
    created = []
    for m in CREATE_TABLE.finditer(sql):
        if m.group(1) or (m.group(2) and m.group(2).strip().lower() in ('temp', 'temporary')) or m.group(5):
            continue
        name = relation(m.group(3), m.group(4))
        if name:
            created.append(('table', name))
    for m in CREATE_VIEW.finditer(sql):
        if re.search(r'\btemp(orary)?\b', m.group(0), re.I):
            continue
        name = relation(m.group(2), m.group(3))
        if name:
            created.append(('materialized view' if m.group(1) else 'view', name))

    granted, blanket = set(), False
    for m in GRANT.finditer(sql):
        target, roles = m.group(2), m.group(3).lower()
        if not any(re.search(r'\b%s\b' % r, roles) for r in ROLES):
            continue
        if re.search(r'\ball\s+tables\s+in\s+schema\s+public\b', target, re.I):
            blanket = True
            continue
        if re.match(r'\s*(function|procedure|routine|sequence|schema|all\s+(functions|sequences|routines))\b', target, re.I):
            continue
        target = re.sub(r'^\s*table\s+', '', target, flags=re.I)
        for item in target.split(','):
            m2 = re.match(r'\s*' + QUALIFIED + r'\s*$', item)
            if m2:
                name = relation(m2.group(1), m2.group(2))
                if name:
                    granted.add(name)

    problems = []
    seen = set()
    for kind, name in created:
        if name in seen or name in granted or name in declared or blanket:
            continue
        seen.add(name)
        problems.append('%s public.%s is created without a grant to authenticated, anon or service_role '
                        'in the same migration.' % (kind.capitalize(), name))
    if DEFAULT_PRIVS.search(sql):
        problems.append('The migration uses alter default privileges to grant on tables. '
                        'Grant on each table by name instead.')
    return problems


def report(path, problems):
    return '%s:\n%s' % (path, '\n'.join('  - ' + p for p in problems))


HELP = ('Supabase no longer exposes new tables in public to the Data API (since 30 October 2026). '
        'Add the grants in the same migration: authenticated by default, anon only for intended public reads, '
        'service_role where edge functions or n8n use the table; keep RLS on. '
        'For a table that must stay closed, add the line: -- grants-check: no-api <table> <reason>')


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True).stdout


def check_staged(cwd):
    root = git(cwd, 'rev-parse', '--show-toplevel').strip()
    if not root:
        return []
    out = []
    for path in git(root, 'diff', '--cached', '--name-only', '--diff-filter=AM').split('\n'):
        if MIGRATION.search(path):
            problems = check_sql(git(root, 'show', ':' + path))
            if problems:
                out.append(report(path, problems))
    return out


def commit_dir(command, cwd):
    """Best guess of the repository a shell command commits in."""
    m = re.search(r'git\s+-C\s+("[^"]+"|\'[^\']+\'|\S+)\s+commit\b', command)
    if m:
        target = m.group(1).strip('"\'')
    else:
        m = re.search(r'\bcd\s+("[^"]+"|\'[^\']+\'|[^\s;&|]+)\s*(?:&&|;)[^|]*?\bgit\s+commit\b', command)
        target = m.group(1).strip('"\'') if m else cwd
    target = os.path.expanduser(target)
    return target if os.path.isabs(target) else os.path.join(cwd, target)


def hook():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = data.get('tool_name', '')
    inp = data.get('tool_input') or {}
    cwd = data.get('cwd') or os.getcwd()
    failures = []
    if tool in ('Write', 'Edit', 'MultiEdit'):
        path = inp.get('file_path', '')
        if not MIGRATION.search(path):
            return 0
        if tool == 'Write':
            text = inp.get('content', '')
        else:
            try:
                with open(path) as f:
                    text = f.read()
            except OSError:
                text = ''
            edits = inp.get('edits') if tool == 'MultiEdit' else [inp]
            for e in edits or []:
                old, new = e.get('old_string', ''), e.get('new_string', '')
                text = text.replace(old, new) if e.get('replace_all') else text.replace(old, new, 1)
        problems = check_sql(text)
        if problems:
            failures.append(report(path, problems))
    elif tool == 'Bash':
        command = inp.get('command', '')
        if not re.search(r'\bgit\b[^|;&]*\bcommit\b', command):
            return 0
        failures = check_staged(commit_dir(command, cwd))
    if not failures:
        return 0
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'Supabase grants guard blocked this.\n' + '\n'.join(failures) + '\n' + HELP,
    }}))
    return 0


def ci(base, head):
    if not base or set(base) == {'0'}:
        base = git('.', 'rev-parse', head + '~1').strip() or head
    paths = git('.', 'diff', '--name-only', '--diff-filter=AM', base, head).split('\n')
    return files([p for p in paths if MIGRATION.search(p)], ref=head)


def files(paths, ref=None):
    failed = False
    for path in paths:
        text = git('.', 'show', '%s:%s' % (ref, path)) if ref else open(path).read()
        problems = check_sql(text)
        if problems:
            failed = True
            print(report(path, problems))
    if failed:
        print(HELP)
        return 1
    print('Supabase grants check passed for %d migration file(s).' % len(paths))
    return 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else 'hook'
    if mode == 'hook':
        return hook()
    if mode == 'commit':
        failures = check_staged(argv[2] if len(argv) > 2 else '.')
        print('\n'.join(failures + ([HELP] if failures else [])))
        return 1 if failures else 0
    if mode == 'ci':
        return ci(argv[2], argv[3])
    if mode == 'files':
        return files(argv[2:])
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
