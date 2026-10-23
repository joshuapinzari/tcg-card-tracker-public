"""Check a source tree for unwanted artifacts and common secret patterns.

This is a targeted release check, not a guarantee of security or a licence audit.
Only current files are checked; this distribution intentionally contains no .git.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.venv', 'venv', '.git', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}
FORBIDDEN_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.parquet', '.pem', '.key', '.pkl', '.pickle', '.zip', '.csv', '.log'}
PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'API token': re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'credential-bearing URL': re.compile(r'(?:https?|postgres(?:ql)?)://[^\s/:]+:[^\s/@]+@'),
}


def check_tree(root=ROOT):
    issues = []
    for path in Path(root).rglob('*'):
        rel = path.relative_to(root)
        if any(x in SKIP for x in rel.parts) or not path.is_file():
            continue
        if path.is_symlink():
            issues.append(f'Symlink requires review: {rel}')
            continue
        if (path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example')
                or path.name == 'secrets.toml' or path.suffix.lower() in FORBIDDEN_SUFFIXES
                or rel.parts[:2] == ('data', 'cache') or rel.parts[0] in {'research_outputs', 'logs'}):
            issues.append(f'Unwanted public artifact: {rel}')
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            issues.append(f'Unreviewed binary file: {rel}')
            continue
        if rel.as_posix() == 'tools/check_public_release.py':
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                issues.append(f'{label}: {rel} (value withheld)')
        if path.name == '.env.example':
            for line in text.splitlines():
                key, sep, value = line.partition('=')
                if sep and any(word in key.upper() for word in ['KEY', 'TOKEN', 'PASSWORD', 'SECRET']) and value.strip():
                    issues.append(f'Nonempty credential example: {rel}')
    return issues


if __name__ == '__main__':
    problems = check_tree()
    for item in problems:
        print('REVIEW:', item)
    if not problems:
        print('No forbidden data artifacts or common credential patterns found.')
    raise SystemExit(bool(problems))
