"""Block commits that contain secrets.

Checks every staged file (default) or every tracked file (--all) for:
  * forbidden paths (.env, secrets/, service-account JSON, keys)
  * private keys, Anthropic / Google API keys, service-account JSON content
  * the literal secret values found in the local .env and Firebase JSON

Exit code 1 if anything is found. Installed as .git/hooks/pre-commit by
`python scripts/check_secrets.py --install-hook`.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PATHS = [
    ".env",
    ".env.*",
    "secrets/*",
    "*service-account*.json",
    "*serviceAccount*.json",
    "*firebase-adminsdk*.json",
    "*credentials*.json",
    "*.pem",
    "*.key",
    "*.p12",
]
ALLOWED_PATHS = [".env.example"]

# Patterns are assembled from pieces so this file does not match itself.
_PEM = "-----BEGIN " + r"(?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
CONTENT_PATTERNS = {
    "private key block": re.compile(_PEM),
    "Anthropic API key": re.compile("sk-" + r"ant-[A-Za-z0-9_\-]{20,}"),
    "Google API key": re.compile("AI" + r"za[0-9A-Za-z_\-]{35}"),
    "service-account JSON": re.compile(r'"type"\s*:\s*"service_' + r'account"'),
    "private_key field": re.compile(r'"private_' + r'key"\s*:\s*"-'),
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")


def _local_secret_values() -> list[tuple[str, str]]:
    """Literal secrets from local files, so they can never leak verbatim."""
    values: list[tuple[str, str]] = []
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if any(t in key for t in ("KEY", "SECRET", "TOKEN", "PASSWORD")) and len(value) >= 12:
                values.append((f".env:{key}", value))
    for cred in (ROOT / "secrets").glob("*.json") if (ROOT / "secrets").is_dir() else []:
        try:
            data = json.loads(cred.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for field in ("private_key_id", "client_id"):
            if isinstance(data.get(field), str) and len(data[field]) >= 12:
                values.append((f"{cred.name}:{field}", data[field]))
        key = data.get("private_key")
        if isinstance(key, str):
            # A distinctive slice of the key body
            body = "".join(key.splitlines()[1:3])
            if len(body) >= 40:
                values.append((f"{cred.name}:private_key", body[:60]))
    return values


def _files(all_tracked: bool) -> list[str]:
    if all_tracked:
        out = _git("ls-files", "-z")
    else:
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [f for f in out.split("\0") if f]


def _content(path: str, all_tracked: bool) -> str:
    try:
        if all_tracked:
            return (ROOT / path).read_text(encoding="utf-8", errors="replace")
        return _git("show", f":{path}")
    except (OSError, subprocess.CalledProcessError):
        return ""


def check(all_tracked: bool = False) -> list[str]:
    problems: list[str] = []
    literals = _local_secret_values()
    for path in _files(all_tracked):
        name = path.replace("\\", "/")
        base = name.rsplit("/", 1)[-1]
        if name not in ALLOWED_PATHS and any(
            fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(base, pat) for pat in FORBIDDEN_PATHS
        ):
            problems.append(f"{name}: forbidden file path")
            continue
        text = _content(path, all_tracked)
        for label, pattern in CONTENT_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{name}: contains {label}")
        for label, value in literals:
            if value in text:
                problems.append(f"{name}: contains local secret value ({label})")
    return problems


def install_hook() -> None:
    hook = ROOT / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "python scripts/check_secrets.py || python3 scripts/check_secrets.py\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"installed {hook.relative_to(ROOT)}")


def main(argv: list[str]) -> int:
    if "--install-hook" in argv:
        install_hook()
        return 0
    all_tracked = "--all" in argv
    problems = check(all_tracked)
    scope = "tracked" if all_tracked else "staged"
    if problems:
        print(f"[secret-check] BLOCKED - {len(problems)} problem(s) in {scope} files:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"[secret-check] OK - no secrets in {scope} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
