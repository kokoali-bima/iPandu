#!/usr/bin/env python3
"""A release commit must not leave without its tag.

v0.2b.85 was pushed correctly and still turned CI red on all four Python
versions. The cause was ordering, not code: `git push origin master` fired the
workflow, and `git push origin v0.2b.85` followed seconds later. CI checks out
the commit and runs `git describe`, which cannot see a tag that is still on the
developer's machine -- so test_release_consistency reported "notes committed,
tag missing", which is precisely the v0.2b.71 defect it exists to catch. A
re-run once the tag landed was green, so the release itself was fine.

The wrong lesson is "remember to push them together". git already tells
pre-push which refs are being pushed, on stdin, so the hook can simply look.

The hook is driven for real here -- the actual .githooks/pre-push file, in a
scratch repository with a scratch origin -- rather than having its logic
re-implemented in the test. `dev/run_all.py` is shimmed to a green stub so the
suite step passes instantly; what is under test is the tag rule that runs
after it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(sys.argv[1]).resolve()
ROOT = SRC.parent
HOOK = ROOT / ".githooks" / "pre-push"

results: list[tuple[str, bool]] = []
skipped_checks = 0


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


def skip_block(n: int, why: str) -> None:
    global skipped_checks
    skipped_checks += n
    print(f"SKIP - {n} check(s): {why}")


HAVE_GIT = shutil.which("git") is not None
HAVE_SH = shutil.which("sh") is not None

GREEN_STUB = (
    "print('TOTAL   1/1  in 0.0s across 1 suite(s)')\n"
)


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def make_pair(declared: str, make_tag: bool, tag_on_remote: bool):
    """A working repo plus a bare 'origin' it can talk to."""
    base = Path(tempfile.mkdtemp(prefix="isla_pushguard_"))
    remote = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                   capture_output=True)

    repo = base / "work"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "remote", "add", "origin", str(remote))

    (repo / "dev").mkdir()
    (repo / "dev" / "run_all.py").write_text(GREEN_STUB, encoding="utf-8")
    (repo / "lite_agent.py").write_text("# stub\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {declared} -- something\n\nbody\n", encoding="utf-8")
    hooks = repo / ".githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-push")

    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release")
    git(repo, "push", "-q", "origin", "master")
    if make_tag:
        git(repo, "tag", "-a", declared, "-m", declared)
        if tag_on_remote:
            git(repo, "push", "-q", "origin", declared)
    return base, repo


def run_hook(repo: Path, refs: list[str]):
    """Feed the hook the refs git would name on stdin."""
    head = git(repo, "rev-parse", "HEAD")
    stdin = "".join(f"{r} {head} {r} 0000000000000000000000000000000000000000\n"
                    for r in refs)
    return subprocess.run(["sh", str(repo / ".githooks" / "pre-push"),
                           "origin", str(repo.parent / "origin.git")],
                          input=stdin, capture_output=True, text=True,
                          cwd=str(repo), encoding="utf-8", errors="replace",
                          env={**os.environ, "ISLA_PYTHON": sys.executable})


if not (HAVE_GIT and HAVE_SH):
    skip_block(6, "git and a POSIX sh are both needed to drive the real hook")
else:
    # --- the failure that actually happened --------------------------------
    base, repo = make_pair("v9.9.9", make_tag=True, tag_on_remote=False)
    proc = run_hook(repo, ["refs/heads/master"])
    check("pushing a release commit while its tag stays behind is REFUSED",
          proc.returncode != 0)
    check("...and the refusal hands over the command that does it right",
          "git push origin HEAD v9.9.9" in proc.stderr)
    shutil.rmtree(base, ignore_errors=True)

    # --- and the three ways that are fine ----------------------------------
    base, repo = make_pair("v9.9.9", make_tag=True, tag_on_remote=False)
    proc = run_hook(repo, ["refs/heads/master", "refs/tags/v9.9.9"])
    check("...while pushing both together passes", proc.returncode == 0)
    shutil.rmtree(base, ignore_errors=True)

    base, repo = make_pair("v9.9.9", make_tag=True, tag_on_remote=True)
    proc = run_hook(repo, ["refs/heads/master"])
    check("a tag already on the remote is not asked for twice",
          proc.returncode == 0)
    shutil.rmtree(base, ignore_errors=True)

    # Mid-development: the CHANGELOG edit lands before the tag exists, and
    # that is normal. Refusing it would make the hook unusable day to day.
    base, repo = make_pair("v9.9.9", make_tag=False, tag_on_remote=False)
    proc = run_hook(repo, ["refs/heads/master"])
    check("an ordinary commit with no tag yet is left alone", proc.returncode == 0)
    shutil.rmtree(base, ignore_errors=True)

    # Pushing only a tag must not be judged against the branch rule.
    base, repo = make_pair("v9.9.9", make_tag=True, tag_on_remote=False)
    proc = run_hook(repo, ["refs/tags/v9.9.9"])
    check("pushing the tag on its own is not refused", proc.returncode == 0)
    shutil.rmtree(base, ignore_errors=True)

hook_text = HOOK.read_text(encoding="utf-8")
check("the hook reads the refs git offers rather than guessing",
      "while read -r _lref _lsha rref _rsha" in hook_text)
check("...and still says how to skip it deliberately",
      "--no-verify" in hook_text)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed"
      + (f", {skipped_checks} skipped" if skipped_checks else ""))
if failed:
    print("FAILED:", failed)
    sys.exit(1)
