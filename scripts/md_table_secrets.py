#!/usr/bin/env python3
"""Prints the cells under password/key/token/secret columns of every markdown
table in a file, one per line, for use in a .scrub-blocklist "cmd:" line.

    cmd: python3 scripts/md_table_secrets.py ~/notes/passwords.md
"""
import re, sys

SECRET_COL = re.compile(r'pass|key|token|secret|pin', re.I)


def cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def main(path):
    secret_cols = []
    for line in open(path, errors='replace'):
        if not line.lstrip().startswith('|'):
            secret_cols = []
            continue
        row = cells(line)
        if all(re.fullmatch(r':?-{3,}:?', c) for c in row if c):
            continue
        if not secret_cols:
            secret_cols = [i for i, c in enumerate(row) if SECRET_COL.search(c)] or [-1]
            continue
        for i in secret_cols:
            if 0 <= i < len(row):
                for value in re.findall(r'`([^`]+)`', row[i]) or [row[i]]:
                    value = value.strip('*_ ')
                    if len(value) >= 6 and ' ' not in value:
                        print(value)


if __name__ == '__main__':
    main(sys.argv[1])
