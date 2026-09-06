#!/usr/bin/env python3
"""Returning to the last working version when a new one will not start.

apply_update() has always refused a build that does not compile. That is a
narrow guard: `py_compile` proves the file parses, nothing more. A release can
compile and still die on the way up -- a NameError at module level, a handler
registered against a renamed function, a constant read from a key that no
longer exists. The process dies before any code inside the bot runs, systemd
restarts it five seconds later, and it dies again. Nothing is left running that
could undo it, and the operator learns about it from silence.

So the guard runs as ExecStartPre, outside the interpreter that loads the bot,
and everything below is driven against a REAL git repository rather than a
mocked one -- the whole value of this thing is that `git reset --hard` moves
HEAD when it is asked to, and a mock would assert that we called a function.

The two properties that matter most are the ones a careless version gets
wrong, so they are tested first and hardest:

  * it always exits 0 -- an ExecStartPre that fails would stop the service
    from starting, turning a guard against downtime into a cause of it;
  * it disarms the moment it acts -- a guard that can roll back twice can
    roll back forever.
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
GUARD_SRC = ROOT / "tools" / "boot_guard.py"

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


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return (proc.stdout or "").strip()


def make_repo() -> tuple[Path, str, str]:
    """A repo with two commits: 'good' then 'bad'. HEAD sits on bad."""
    repo = Path(tempfile.mkdtemp(prefix="isla_rollback_"))
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "tools").mkdir()
    shutil.copy(GUARD_SRC, repo / "tools" / "boot_guard.py")

    (repo / "marker.txt").write_text("good", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "good")
    good = git(repo, "rev-parse", "HEAD")

    (repo / "marker.txt").write_text("bad", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bad")
    bad = git(repo, "rev-parse", "HEAD")
    return repo, good, bad


def run_guard(repo: Path, env_extra: dict | None = None):
    env = {**os.environ, "ISLA_BOOT_ROLLBACK_AFTER": "3"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(repo / "tools" / "boot_guard.py")],
                          capture_output=True, text=True, env=env,
                          encoding="utf-8", errors="replace")


def state_of(repo: Path) -> dict:
    p = repo / "update_state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def arm(repo: Path, target: str, **extra) -> None:
    (repo / "update_state.json").write_text(
        json.dumps({"rollback_to": target, "boot_attempts": 0, **extra}),
        encoding="utf-8")


if not HAVE_GIT:
    skip_block(14, "git is not on PATH, and this guard is git or nothing")
else:
    # --- an unarmed start must be left completely alone --------------------
    repo, good, bad = make_repo()
    proc = run_guard(repo)
    check("an ordinary start exits 0", proc.returncode == 0)
    check("...and does not touch the checkout",
          git(repo, "rev-parse", "HEAD") == bad)
    check("...and writes no state it was not asked to write",
          not (repo / "update_state.json").exists())
    shutil.rmtree(repo, ignore_errors=True)

    # --- the counted climb -------------------------------------------------
    repo, good, bad = make_repo()
    arm(repo, good)

    run_guard(repo)
    check("the first attempt after an update only counts",
          state_of(repo)["boot_attempts"] == 1
          and git(repo, "rev-parse", "HEAD") == bad)

    run_guard(repo)
    check("...the second still only counts -- one slow start is not a failure",
          state_of(repo)["boot_attempts"] == 2
          and git(repo, "rev-parse", "HEAD") == bad)

    proc = run_guard(repo)
    check("the third rolls the checkout back for real",
          git(repo, "rev-parse", "HEAD") == good)
    check("...and the working tree really is the old version, not just the ref",
          (repo / "marker.txt").read_text(encoding="utf-8") == "good")
    check("...and it says so where the journal will keep it",
          "ROLLED BACK" in proc.stderr)
    check("...and still exits 0", proc.returncode == 0)

    # THE property that stops a rollback loop.
    st = state_of(repo)
    check("it disarms itself the moment it acts",
          "rollback_to" not in st and st["boot_attempts"] == 0)

    run_guard(repo)
    check("...so a later start cannot roll back a second time",
          git(repo, "rev-parse", "HEAD") == good)
    shutil.rmtree(repo, ignore_errors=True)

    # --- the operator has to be told --------------------------------------
    repo, good, bad = make_repo()
    arm(repo, good)
    (repo / "update_announce.json").write_text(
        json.dumps({"chat_id": 4242, "lang": "id", "from": "aaa", "to": "v9.9"}),
        encoding="utf-8")
    for _ in range(3):
        run_guard(repo)
    note = json.loads((repo / "update_announce.json").read_text(encoding="utf-8"))
    check("the rollback is handed to the chat that asked for the update",
          note.get("chat_id") == 4242 and note.get("rolled_back") is True)
    check("...naming the version that failed, not just the one restored",
          note.get("failed") == "v9.9")
    check("...and the language the operator was using is preserved",
          note.get("lang") == "id")
    shutil.rmtree(repo, ignore_errors=True)

    # --- failure paths: none of them may block the start -------------------
    repo, good, bad = make_repo()
    arm(repo, "0000000000000000000000000000000000000000")
    for _ in range(2):
        run_guard(repo)
    proc = run_guard(repo)
    check("a rollback to a commit that does not exist still exits 0",
          proc.returncode == 0)
    check("...reports the failure rather than claiming success",
          "ROLLBACK FAILED" in proc.stderr)
    check("...and STILL disarms, because retrying would fail identically",
          "rollback_to" not in state_of(repo))
    shutil.rmtree(repo, ignore_errors=True)

    repo, good, bad = make_repo()
    (repo / "update_state.json").write_text("{ not json at all",
                                            encoding="utf-8")
    proc = run_guard(repo)
    check("an unreadable state file is treated as 'nothing armed', not a crash",
          proc.returncode == 0 and git(repo, "rev-parse", "HEAD") == bad)
    shutil.rmtree(repo, ignore_errors=True)

    # A rollback with nobody waiting still has to happen.
    repo, good, bad = make_repo()
    arm(repo, good)
    for _ in range(3):
        proc = run_guard(repo)
    check("a rollback with no pending announcement still rolls back",
          git(repo, "rev-parse", "HEAD") == good)
    check("...and says the notice could not be delivered instead of pretending",
          "journal" in proc.stderr.lower())
    shutil.rmtree(repo, ignore_errors=True)


# --- the wiring inside the bot ---------------------------------------------
src = SRC.read_text(encoding="utf-8")

check("apply_update arms the guard once the new code is in place",
      '"rollback_to": before' in src)
check("...only AFTER the compile check, so a build that never landed is not "
      "armed against",
      src.index("does not compile; rolled back") < src.index('"rollback_to": before'))

start = src.index("async def _announce_update")
end = src.index("\n    #", start)
body = src[start:end]
check("reaching post_init disarms the guard", 'state.pop("rollback_to", None)' in body)
check("...BEFORE the early return, so a start with nothing to announce still "
      "counts as a successful start",
      body.index('state.pop("rollback_to", None)')
      < body.index("if not UPDATE_ANNOUNCE_FILE.exists()"))
check("a rollback is reported to the operator, not left in the journal alone",
      "rolled_back" in body)
check("...in both languages",
      "Rolled back to" in body and "Dikembalikan ke" in body)
check("...saying explicitly that the PIN and settings survived",
      "PIN are untouched" in body and "PIN Anda tidak tersentuh" in body)

unit = (ROOT / "systemd" / "lite-agent.service.template").read_text(encoding="utf-8")
check("the unit runs the guard before the interpreter",
      "ExecStartPre=" in unit and "boot_guard.py" in unit)
check("...with a leading '-', so the guard can never be the reason the "
      "service refuses to start",
      "ExecStartPre=-" in unit)
check("...and before ExecStart, where it can still change what gets loaded",
      unit.index("ExecStartPre=") < unit.index("ExecStart="))

guard_src = GUARD_SRC.read_text(encoding="utf-8")
check("the guard imports nothing outside the standard library -- it has to "
      "work on the day the venv does not",
      "import telegram" not in guard_src and "requests" not in guard_src)
check("...and its last act is always sys.exit(0)",
      guard_src.rstrip().endswith("sys.exit(0)"))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed"
      + (f", {skipped_checks} skipped" if skipped_checks else ""))
if failed:
    print("FAILED:", failed)
    sys.exit(1)
