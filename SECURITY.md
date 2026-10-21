# Security policy

## Reporting a vulnerability

Please **don't** open a public issue for a security problem.

Report it privately through GitHub instead: open the repository's **Security** tab and choose **Report a vulnerability**. That starts a private conversation with the maintainer.

Please include:

- what the problem is and what someone could do with it
- the steps to reproduce it
- the version (or commit) you tested

You'll get a reply as soon as possible. Once a fix is out, you'll be credited in the changelog if you'd like.

## What's in scope

Anything that would let someone:

- change files on a scanned device (the scanner must only ever read)
- run commands on the hub or a device without being allowed to
- see file names or settings while a login is turned on
- get a device password or the hub's SSH key
- get past the page login or the proxy-auth check

## Supported versions

Only the latest release gets security fixes.

## How Disk City is built to be safe

See [docs/security.md](docs/security.md): read-only scanning, no stored passwords, a restricted SSH key line, the login options, and a checklist.
