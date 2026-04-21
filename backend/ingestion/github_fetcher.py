"""
github_fetcher.py
-----------------
Clones a GitHub repository to a temp directory and walks its files,
returning (file_path, relative_path, content) tuples for all supported
source files.

Supports:
  - Public repos (no token needed)
  - Private repos (set GITHUB_TOKEN in .env)
  - Branch / tag selection via ?ref= query param style input
"""

import os
import re
import fnmatch
import tempfile
import subprocess
from pathlib import Path
from typing import Generator, Tuple

from config import GITHUB_TOKEN, SUPPORTED_EXTENSIONS, IGNORE_PATTERNS


def _build_clone_url(repo_url: str) -> str:
    """Inject token into HTTPS URL if available (for private repos)."""
    if GITHUB_TOKEN and "github.com" in repo_url:
        # https://github.com/owner/repo  →  https://<token>@github.com/owner/repo
        repo_url = repo_url.replace("https://", f"https://{GITHUB_TOKEN}@")
    return repo_url


def _run_git_command(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a git command and raise a readable error on failure."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result


def _parse_repo_url(raw: str) -> Tuple[str, str]:
    """
    Accept several common formats and return (clone_url, ref).
    ref defaults to 'HEAD' (default branch).

    Accepted formats:
      https://github.com/owner/repo
      https://github.com/owner/repo/tree/branch-name
      github.com/owner/repo
      owner/repo
    """
    raw = raw.strip().rstrip("/")

    # Bare "owner/repo" → full URL
    if not raw.startswith("http") and raw.count("/") == 1:
        raw = f"https://github.com/{raw}"

    # Ensure scheme
    if raw.startswith("github.com"):
        raw = "https://" + raw

    # Extract branch from  /tree/<ref>
    ref = "HEAD"
    tree_match = re.search(r"/tree/(.+)$", raw)
    if tree_match:
        ref = tree_match.group(1)
        raw = raw[: tree_match.start()]

    # Strip .git suffix if present
    clone_url = raw if raw.endswith(".git") else raw + ".git"
    return clone_url, ref


def _should_ignore(path: Path) -> bool:
    """Return True if any component of the path matches an ignore pattern."""
    parts = path.parts
    for pattern in IGNORE_PATTERNS:
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def list_repository_branches(repo_url: str) -> list[str]:
    """Return available branch names for a repo URL/shorthand."""
    clone_url, _ = _parse_repo_url(repo_url)
    auth_url = _build_clone_url(clone_url)
    result = _run_git_command(["git", "ls-remote", "--heads", auth_url], timeout=60)
    branches: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branches.append(ref[len("refs/heads/"):])
    # Stable unique list
    return sorted(set(branches))


def fetch_repository(repo_url: str, branch: str | None = None) -> Generator[dict, None, None]:
    """
    Clone *repo_url* into a temporary directory, then yield one dict per
    supported source file:

    {
        "rel_path":  "src/main/App.java",   # relative to repo root
        "abs_path":  "/tmp/xyz/src/...",    # absolute path on disk
        "language":  "java",                # tree-sitter grammar name
        "content":   "public class App ...", # raw file text
    }

    The temp directory is cleaned up after the generator is exhausted OR
    if an exception is raised.
    """
    clone_url, ref = _parse_repo_url(repo_url)
    selected_ref = (branch or "").strip() or ref
    auth_url = _build_clone_url(clone_url)

    tmp_dir = tempfile.mkdtemp(prefix="coderag_")
    try:
        # Shallow clone — we only need the latest snapshot
        cmd = [
            "git", "clone",
            "--depth", "1",
            "--single-branch",
        ]
        if selected_ref != "HEAD":
            cmd += ["--branch", selected_ref]
        cmd += [auth_url, tmp_dir]

        _run_git_command(cmd, timeout=120)

        repo_root = Path(tmp_dir)
        for file_path in repo_root.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(repo_root)
            if _should_ignore(rel):
                continue
            ext = file_path.suffix.lower()
            language = SUPPORTED_EXTENSIONS.get(ext)
            if not language:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not content.strip():
                continue

            yield {
                "rel_path": str(rel),
                "abs_path": str(file_path),
                "language": language,
                "content": content,
            }

    finally:
        # Always clean up the temp clone
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
