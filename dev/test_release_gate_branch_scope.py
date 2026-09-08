#!/usr/bin/env python3
"""The tag requirement applies to master. Not yet, on a PR branch.

PR #2 (merging feat/multi-deployment-and-per-room-scope) carried a real
version bump in the same commit that proposed it, and could not carry the tag
too -- the tag has to point at the actual master merge commit, which does not
exist until the PR lands. Every CI run against the PR branch was red for a
reason that resolves itself the moment it merges, not a defect in the change.

Driven against real, throwaway git repositories (not a mocked git() call),
because the whole value of this guard is that `git rev-parse --abbrev-ref
HEAD` returns something real. Both directions matter equally: relaxed on a
branch that isn't master, and -- the property that must NOT have quietly
broken -- still refusing on master itself.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(sys.argv[1]).resolve()
GATE_SRC = SRC.parent / "dev" / "test_release_consistency.py"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def make_repo(branch_name: str, version: str) -> Path:
    """A repo with one commit declaring `version` in both docs, on a branch
    literally named `branch_name`, and no tag anywhere for that version."""
    repo = Path(tempfile.mkdtemp(prefix="isla_relgate_"))
    git(repo, "init", "-q", "-b", branch_name)
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "lite_agent.py").write_text("# stub\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version} -- something\n\nbody\n", encoding="utf-8")
    (repo / "README.md").write_text(
        f"> **Status: {version} -- early/beta.**\n", encoding="utf-8")
    (repo / "dev").mkdir()
    shutil.copy(GATE_SRC, repo / "dev" / "test_release_consistency.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release commit, no tag")
    return repo


def run_gate(repo: Path):
    return subprocess.run(
        [sys.executable, str(repo / "dev" / "test_release_consistency.py"),
         str(repo / "lite_agent.py")],
        capture_output=True, text=True, cwd=str(repo),
        encoding="utf-8", errors="replace")


# --- relaxed: a version bump committed on a review branch is fine ----------
repo = make_repo("merge/some-feature", "v9.9.9")
proc = run_gate(repo)
shutil.rmtree(repo, ignore_errors=True)
check("a release commit with no tag PASSES on a non-master branch",
      proc.returncode == 0)
check("...and the reason names the branch, not just 'not tagged'",
      "branch=merge/some-feature" in proc.stdout)


# --- THE property that must not have broken: master still refuses ---------
repo = make_repo("master", "v9.9.9")
proc = run_gate(repo)
shutil.rmtree(repo, ignore_errors=True)
check("the SAME commit, on a branch literally named master, still FAILS "
      "-- the gate is relaxed elsewhere, not disabled",
      proc.returncode != 0)
check("...and says so plainly", "FAIL" in proc.stdout and "branch=master" in proc.stdout)


# --- master with the tag actually pushed still passes, as before ----------
repo = make_repo("master", "v9.9.9")
git(repo, "tag", "-a", "v9.9.9", "-m", "v9.9.9")
proc = run_gate(repo)
shutil.rmtree(repo, ignore_errors=True)
check("master WITH its tag passes, exactly as it always has",
      proc.returncode == 0)


# --- wiring: the fix lives in the gate itself, not a separate script -------
src = GATE_SRC.read_text(encoding="utf-8")
check("the scoping is read from the actual branch name, not hardcoded",
      'git("rev-parse", "--abbrev-ref", "HEAD")' in src)
check("...and the check still requires the tag OR an uncommitted bump OR "
      "not-master -- never drops the tag requirement outright",
      "ok_tag or not notes_are_committed or not on_master" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
