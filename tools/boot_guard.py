#!/usr/bin/env python3
"""Return to the last working version when a new one will not start.

`apply_update()` already refuses a build that does not compile. That catches a
syntax error and nothing else. A release can compile perfectly and still die on
the way up -- a NameError at module level, a handler registered against a
function that was renamed, a constant read from a config key that no longer
exists. The process then dies before any code inside the bot runs, systemd
restarts it five seconds later, and it dies again. The operator finds out from
silence in Telegram, and the machine that could fix it is the machine that is
down.

So the guard cannot live inside lite_agent.py. It runs as ExecStartPre, before
the interpreter ever loads the bot, and it is deliberately made of nothing but
the standard library: a guard that needs the venv to be intact cannot help on
the day the venv is not.

The shape:

    apply_update()      arms it -- records the commit to come back to
    this script         counts each attempt to start; after BOOT_ROLLBACK_AFTER
                        it resets the checkout and leaves a note
    _announce_update()  disarms it, because reaching post_init means the new
                        build genuinely started

Two rules this file will not break:

  * It always exits 0. ExecStartPre failing would stop the service from
    starting at all, which would turn a guard against downtime into a cause
    of it.
  * It clears `rollback_to` the moment it acts. A guard that can roll back
    twice can roll back forever.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UPDATE_STATE_FILE = BASE_DIR / "update_state.json"
UPDATE_ANNOUNCE_FILE = BASE_DIR / "update_announce.json"

# Three, with RestartSec=5, is about fifteen seconds of silence before the
# checkout goes back. Two would trip on a single unrelated restart racing the
# first boot; five is most of a minute during which the operator is already
# wondering what happened.
BOOT_ROLLBACK_AFTER = int(os.environ.get("ISLA_BOOT_ROLLBACK_AFTER", "3"))


def log(msg: str) -> None:
    """stderr, so it lands in the journal beside the service's own output."""
    print(f"boot_guard: {msg}", file=sys.stderr)


def _git(*args: str, timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(BASE_DIR), *args],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8",
            errors="replace",
        )
        return proc.returncode == 0, (proc.stdout or proc.stderr or "").strip()
    except Exception as exc:                      # git missing, timeout, ...
        return False, str(exc)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(path: Path, data: dict) -> bool:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        log(f"could not write {path.name}: {exc}")
        return False


def roll_back(state: dict, target: str) -> None:
    """Reset the checkout and leave word for the process that comes back.

    The note goes into update_announce.json, which the bot already reads on
    startup to confirm an update -- so a rollback reaches the operator through
    the path that is known to work, rather than a second one written today and
    exercised never.
    """
    before_ok, before = _git("rev-parse", "HEAD")
    ok, detail = _git("reset", "--hard", target, timeout=120)

    # Disarm FIRST and unconditionally. If the reset failed, trying again on
    # the next boot will fail the same way; if it succeeded, there is nothing
    # left to come back from. Either way this must not run twice.
    state.pop("rollback_to", None)
    state["boot_attempts"] = 0
    state["last_rollback"] = {"to": target[:12], "ok": ok, "detail": detail[:200]}
    _write(UPDATE_STATE_FILE, state)

    if not ok:
        log(f"ROLLBACK FAILED to {target[:12]}: {detail[:200]}")
        return

    _, version = _git("describe", "--tags")
    log(f"ROLLED BACK to {target[:12]} ({version}) after "
        f"{BOOT_ROLLBACK_AFTER} failed starts")

    # Reuse the pending announcement's chat if there is one -- that is the
    # operator who pressed the button and is still waiting.
    note = _read(UPDATE_ANNOUNCE_FILE)
    if not note.get("chat_id"):
        log("no pending announcement, so the rollback cannot be reported in "
            "Telegram; it is in the journal only")
        return
    note.update({
        "rolled_back": True,
        "failed": note.get("to", before[:12] if before_ok else "?"),
        "to": version or target[:12],
    })
    _write(UPDATE_ANNOUNCE_FILE, note)


def main() -> None:
    state = _read(UPDATE_STATE_FILE)
    target = state.get("rollback_to")
    if not target:
        return                      # nothing was armed; a normal start

    attempts = int(state.get("boot_attempts") or 0) + 1
    state["boot_attempts"] = attempts

    if attempts < BOOT_ROLLBACK_AFTER:
        log(f"start attempt {attempts}/{BOOT_ROLLBACK_AFTER} since the update "
            f"(will return to {str(target)[:12]} if this build does not come up)")
        _write(UPDATE_STATE_FILE, state)
        return

    log(f"attempt {attempts}/{BOOT_ROLLBACK_AFTER} -- the new build is not "
        f"coming up; returning to {str(target)[:12]}")
    roll_back(state, target)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:        # noqa: BLE001 -- see the module docstring
        # Never let this stop the service. A guard that can block the boot is
        # a worse bug than the one it exists to catch.
        log(f"unexpected error, continuing anyway: {exc!r}")
    sys.exit(0)
