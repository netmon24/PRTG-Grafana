"""Sucht vor einer Veröffentlichung nach Zugangsdaten, Schlüsseln, Mailadressen und Kundenbezug.

Aufruf: python scripts/scan_publish.py [--deny begriff,...] [Repository]
Prüft den Arbeitsbaum und den gesamten Verlauf. `--deny` nimmt Begriffe, die nirgends vorkommen
dürfen (Kundennamen, echte Hostnamen). Endet mit Status 1, wenn etwas gefunden wurde.
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PATTERNS = {
    "token": re.compile(
        r"\b(gh[opsu]_[A-Za-z0-9]{30,}|github_pat_\w{20,}|glpat-[\w-]{20,}|AKIA[0-9A-Z]{16}|xox[bp]-[\w-]{10,}"
        r"|glsa_\w{20,}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}|[A-Za-z0-9_-]{60,}==)"
    ),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    "e-mail": re.compile(
        r"\b[\w.+-]+@(?!example\.|users\.noreply\.github\.com)[\w-]+(\.[\w-]+)+\b"
    ),
}
IGNORED_MAIL = re.compile(r"@example\.|noreply")
SENSITIVE_FILES = re.compile(
    r"(^|/)(\.env(\.(?!example$)[\w.-]+)?|[\w.-]+\.env|cores\.yaml|id_(rsa|dsa|ecdsa|ed25519)|\.netrc)$"
    r"|\.(db|sqlite3?|pem|key|crt|pfx|p12|jks|kdbx)$"
)
LOCKFILE = "uv.lock"


@dataclass(frozen=True)
class Finding:
    kind: str
    what: str
    where: str  # "tree" oder "history"
    file: str = ""


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return out.stdout.decode("utf-8", errors="replace")


def _scan_text(text: str, where: str, file: str, deny: list[str]) -> list[Finding]:
    found = []
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(0)
            if kind == "e-mail" and IGNORED_MAIL.search(value):
                continue
            found.append(Finding(kind, value[:60], where, file))
    lowered = text.lower()
    for term in deny:
        if term.lower() in lowered:
            found.append(Finding("begriff", term, where, file))
    return found


def scan(repo: Path, deny: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    names = set(_git(repo, "ls-files").splitlines())
    history_names = set(_git(repo, "log", "--all", "--name-only", "--format=").splitlines())
    for name in sorted(names | history_names):
        if name and SENSITIVE_FILES.search(name):
            findings.append(
                Finding("datei", Path(name).name, "tree" if name in names else "history", name)
            )
    for name in names:
        if Path(name).name == LOCKFILE:
            continue
        try:
            text = (repo / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings += _scan_text(text, "tree", name, deny)
    added = "\n".join(
        line[1:]
        for line in _git(
            repo, "log", "--all", "-p", "--format=", "--", ".", f":!{LOCKFILE}"
        ).splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    tree_hits = {(f.kind, f.what) for f in findings}
    for f in _scan_text(added, "history", "", deny):
        if (f.kind, f.what) not in tree_hits:
            findings.append(f)
    authors = set(_git(repo, "log", "--all", "--format=%ae%n%ce").splitlines())
    for mail in sorted(a for a in authors if a and not IGNORED_MAIL.search(a)):
        findings.append(Finding("autor", mail, "history"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--deny", default="", help="Begriffe, durch Komma getrennt")
    args = parser.parse_args()
    deny = [t.strip() for t in args.deny.split(",") if t.strip()]
    findings = scan(Path(args.repo), deny)
    for f in findings:
        print(f"{f.where:8} {f.kind:12} {f.what}  {f.file}")
    print(f"{len(findings)} Fund(e)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
