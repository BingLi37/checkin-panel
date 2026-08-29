"""Refuse to commit a credential.

Exists because one already happened: a 40-char `gho_` GitHub OAuth token sat in `run.py`
for three commits (`dad4401`..`6748f47`), put there by a setup script that wrote `gh auth`'s
token into the file as a default. It was gone from the working tree long before anyone
noticed, which is exactly the problem — a secret leaves the tree and stays in the history,
and by then the fix is a rewrite plus a revocation rather than an edit.

So this checks what is *staged*, before it becomes history. Run it as a pre-commit hook:

	.venv\\Scripts\\python.exe check_secrets.py --install

or by hand over the whole tree, which is what to do before making a repo public:

	.venv\\Scripts\\python.exe check_secrets.py --all

Two things it deliberately does not do. It never prints a matched value — a scanner that
echoes the secret it found puts it in your terminal scrollback and your CI log. And it does
not try to be a general secret scanner: the patterns below are shapes with a fixed prefix and
no plausible innocent meaning, so a hit is a hit. Anything fuzzier (high-entropy strings,
`password=`) produced only false positives on this repo — the UI label `password: '账号密码…'`
and fixtures like `secret123` — and a check that cries wolf gets bypassed with `--no-verify`,
which is worse than no check.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Prefix-anchored shapes only. Each is a token format whose issuer documents the prefix, so
# there is no innocent reason for one to appear in source.
PATTERNS = (
	('GitHub token', re.compile(rb'gh[pousr]_[A-Za-z0-9]{16,}')),
	('GitHub fine-grained PAT', re.compile(rb'github_pat_[A-Za-z0-9_]{20,}')),
	('OpenAI-style key', re.compile(rb'sk-[A-Za-z0-9]{32,}')),
	('AWS access key id', re.compile(rb'AKIA[0-9A-Z]{16}')),
	('Slack token', re.compile(rb'xox[baprs]-[A-Za-z0-9-]{10,}')),
	('Google API key', re.compile(rb'AIza[A-Za-z0-9_-]{35}')),
	('private key', re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
	('JWT', re.compile(rb'eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}')),
)

# Paths that are allowed to describe these shapes: this file names every prefix it looks for,
# and the docs explain what leaked. Matching on the pattern text is the point there.
ALLOWED = {'check_secrets.py'}

# A database is never source. Committing one is how plaintext credentials (ADR-0003) would
# reach a public repo without matching any pattern at all, since sqlite stores them verbatim
# but the surrounding bytes are binary.
FORBIDDEN_SUFFIXES = ('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.pem', '.pfx', '.p12')
FORBIDDEN_NAMES = ('.env', 'panel.db')


def _git(*args: str) -> str:
	return subprocess.run(
		('git',) + args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace'
	).stdout


def _staged_paths() -> list[str]:
	out = _git('diff', '--cached', '--name-only', '--diff-filter=ACMR')
	return [line.strip() for line in out.splitlines() if line.strip()]


def _tracked_paths() -> list[str]:
	return [line.strip() for line in _git('ls-files').splitlines() if line.strip()]


def _staged_blob(path: str) -> bytes:
	"""The staged content, not the file on disk — they differ after `git add` plus an edit."""
	proc = subprocess.run(
		('git', 'show', f':{path}'), cwd=ROOT, capture_output=True
	)
	return proc.stdout if proc.returncode == 0 else b''


def _scan(path: str, blob: bytes) -> list[str]:
	name = Path(path).name
	problems = []
	if name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES):
		problems.append(f'{path}: a credential store should never be committed (ADR-0003)')
	if path.replace('\\', '/') in ALLOWED:
		return problems
	for label, pattern in PATTERNS:
		match = pattern.search(blob)
		if match:
			# Line number, never the value. A scanner that echoes what it found has just
			# copied the secret into a log.
			line = blob.count(b'\n', 0, match.start()) + 1
			problems.append(f'{path}:{line}: looks like a {label}')
	return problems


def main() -> int:
	args = set(sys.argv[1:])

	if '--install' in args:
		hook = ROOT / '.git' / 'hooks' / 'pre-commit'
		if not hook.parent.is_dir():
			print('[secrets] no .git/hooks — not a git checkout?')
			return 1
		# sys.executable, so the hook uses the same interpreter that installed it. A bare
		# `python` in a hook resolves against whatever PATH the committing shell has, which
		# on this machine is not the venv.
		# Forward slashes even on Windows: this is a POSIX sh script, where a backslash inside
		# a double-quoted string is an escape character -- `.venv` would arrive as `.venv`
		# only by luck, since \v is a real escape. Windows accepts / in every path API.
		interpreter = Path(sys.executable).as_posix()
		script = (ROOT / 'check_secrets.py').as_posix()
		hook.write_text(
			'#!/bin/sh\n'
			'# Installed by check_secrets.py --install\n'
			f'exec "{interpreter}" "{script}"\n',
			encoding='utf-8',
			newline='\n',
		)
		hook.chmod(0o755)
		print(f'[secrets] pre-commit hook installed at {hook}')
		print('[secrets] bypass for one commit with: git commit --no-verify')
		return 0

	whole_tree = '--all' in args
	paths = _tracked_paths() if whole_tree else _staged_paths()
	if not paths:
		return 0

	problems = []
	for path in paths:
		blob = (ROOT / path).read_bytes() if whole_tree and (ROOT / path).is_file() else _staged_blob(path)
		if blob:
			problems.extend(_scan(path, blob))

	scope = 'tracked' if whole_tree else 'staged'
	if problems:
		print(f'[secrets] refusing: {len(problems)} problem(s) in {scope} files\n')
		for line in problems:
			print(f'  {line}')
		print(
			'\nIf it is a real credential: revoke it first, then remove it. A revoked secret is'
			'\nharmless wherever it ended up; an unrevoked one in git history needs a rewrite.'
			'\nIf it is a false positive, add the path to ALLOWED in check_secrets.py.'
		)
		return 1

	print(f'[secrets] {len(paths)} {scope} file(s) clean')
	return 0


if __name__ == '__main__':
	sys.exit(main())
