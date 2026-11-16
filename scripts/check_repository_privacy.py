"""Reject known credential forms and private data files in Git's index.

Uses staged bytes, not working-tree bytes. Reports locations/categories only,
never secret values. This is a guardrail, not a guarantee against every secret.
"""
import re
import subprocess


SECRET_PATTERNS = {
    'provider credential': re.compile(rb'(?:sk-ant-[A-Za-z0-9_-]{20,}|sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})'),
    'literal credential assignment': re.compile(rb"(?i)\b[\w-]*(?:api[_-]?key|access[_-]?token|secret|password)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_+/=-]{28,}[\"']"),
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential in connection URL': re.compile(rb'(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s:/]+:[^\s@]{12,}@'),
}
PRIVATE_PATH = re.compile(r'(^|/)(?:\.local|private|secrets|resumes|data)/|\.(?:db|sqlite3?|pdf|docx?|xlsx?|pem|p12|pfx)(?:-|$)|(^|/)\.env(?:\.|$)', re.I)


def violations(path, content):
    reasons = []
    if PRIVATE_PATH.search(path) and path.rsplit('/', 1)[-1] != '.env.example':
        reasons.append('private data or credential file')
    for name, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(content):
            # Explicit illustrative placeholders are permitted; real credentials
            # must never be added to an allowlist.
            value = match.group().upper()
            if b'YOUR_' not in value and b'EXAMPLE' not in value and b'REPLACE_' not in value:
                reasons.append(name)
                break
    return reasons


def main():
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z']).split(b'\0')
    found, count = [], 0
    for entry in entries:
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        path = raw_path.decode('utf-8', errors='replace')
        if stage != b'0':
            found.append((path, 'unmerged file')); continue
        if mode == b'160000':
            continue
        content = subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()])
        count += 1
        found.extend((path, reason) for reason in violations(path, content))
    for path, reason in found:
        print(f'{path}: {reason}')
    print(f'Checked {count} indexed files; {len(found)} finding(s).')
    return bool(found)


if __name__ == '__main__':
    raise SystemExit(main())
