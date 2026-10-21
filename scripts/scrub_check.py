#!/usr/bin/env python3
"""Refuses a commit that would publish secrets or personal details.

    python3 scripts/scrub_check.py --staged   # what's about to be committed (the pre-commit hook)
    python3 scripts/scrub_check.py --all      # every tracked file

Checks every text file for well-known secret shapes (private keys, cloud and
GitHub tokens, hard-coded passwords) plus your own blocklist in
.scrub-blocklist, which git ignores so the list itself never ships:

    # one regex per line, matched case-insensitively
    \\bmy-nas-name\\b
    203\\.0\\.113\\.
    # cmd: runs a command; each line it prints is blocked as a literal string,
    # and is never echoed back (handy for passwords kept in a password manager)
    cmd: pass show home/ssh

Install as a hook:  ln -s ../../scripts/scrub_check.py .git/hooks/pre-commit
"""
import os, re, subprocess, sys

ROOT = subprocess.run(['git', 'rev-parse', '--show-toplevel'], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip() or '.'
SECRETS = [
    ('private key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('AWS key', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('GitHub token', re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b')),
    ('Slack token', re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}')),
    ('Google key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('Anthropic/OpenAI key', re.compile(r'\bsk-(ant-)?[A-Za-z0-9_-]{20,}')),
    ('hard-coded password', re.compile(r'''(?i)\b(pass(word)?|passwd|secret|api_?key|token)\s*[:=]\s*['"][^'"\s{}$<>]{6,}['"]''')),
    ('email address', re.compile(r'\b[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.(com|net|org|io|me|dev|lol)\b')),
]
SKIP_DIRS = ('.git/', 'web/vendor/')
BINARY = ('.png', '.gif', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2')


def files(mode):
    if mode == '--staged':
        out = subprocess.run(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR'], cwd=ROOT,
                             stdout=subprocess.PIPE, universal_newlines=True).stdout
    else:
        out = subprocess.run(['git', 'ls-files'], cwd=ROOT, stdout=subprocess.PIPE, universal_newlines=True).stdout
    return [f for f in out.splitlines() if f and not f.startswith(SKIP_DIRS) and not f.lower().endswith(BINARY)]


def content(path, mode):
    if mode == '--staged':
        return subprocess.run(['git', 'show', ':' + path], cwd=ROOT, stdout=subprocess.PIPE).stdout.decode(errors='replace')
    with open(os.path.join(ROOT, path), errors='replace') as f:
        return f.read()


def blocklist():
    patterns, literals = [], []
    path = os.path.join(ROOT, '.scrub-blocklist')
    if not os.path.exists(path):
        return patterns, literals
    for line in open(path):
        line = line.rstrip('\n')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.startswith('cmd:'):
            out = subprocess.run(line[4:].strip(), shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 universal_newlines=True).stdout
            literals += [l.strip() for l in out.splitlines() if len(l.strip()) >= 6]
        else:
            patterns.append((line, re.compile(line, re.I)))
    return patterns, literals


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else '--staged'
    if os.path.basename(sys.argv[0]) == 'pre-commit':
        mode = '--staged'
    patterns, literals = blocklist()
    if not os.path.exists(os.path.join(ROOT, '.scrub-blocklist')):
        print('scrub: no .scrub-blocklist, so only generic secret checks ran')
    hits = 0
    for path in files(mode):
        text = content(path, mode)
        if path == 'scripts/scrub_check.py':
            continue  # this file describes the patterns it looks for
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in SECRETS:
                if rx.search(line):
                    hits += 1
                    print('%s:%d: looks like a %s' % (path, n, name))
            for src, rx in patterns:
                if rx.search(line):
                    hits += 1
                    print('%s:%d: matches blocklist %s' % (path, n, src))
            for i, lit in enumerate(literals):
                if lit in line:
                    hits += 1
                    print('%s:%d: contains blocked secret #%d (not shown)' % (path, n, i + 1))
    if hits:
        print('scrub: %d problem(s). Fix them, or add a false positive to an allow list before committing.' % hits)
        return 1
    print('scrub: clean (%d files)' % len(files(mode)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
