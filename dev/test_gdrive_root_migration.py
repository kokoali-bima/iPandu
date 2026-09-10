#!/usr/bin/env python3
"""Each deployment gets its own Drive root, and an existing one migrates.

GDRIVE_ROOT was one fixed name ("iPandu Data") for every install. Two bots
on one host sharing one connected Google account -- a real setup now that the
just-merged SERVICE_NAME work makes multi-deployment easy -- would silently
write into the SAME root, distinguished only by each room's own subfolder,
which collides outright if both bots ever serve a room with the same name.

The root is now "iPandu/<SERVICE_NAME>". An account already holding the old
flat folder is migrated automatically, once, on the next start: the whole
tree -- moves in one `rclone moveto`, never recreated file by file.

The property tested hardest: the existence check is a SUBSTRING match against
the PARENT's listing, the same strategy test_connectgdrive.py's own mocks
already encode for a bare `lsd` on the drive root (always succeeds, whatever
it contains) -- not a bet that `lsd` fails cleanly on a path that has to
resolve through Drive's own name-based lookup, which is untested ground this
suite deliberately does not assume either way.
"""
import atexit
import importlib.util
import os
import shutil as _shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_gdriveroot_"))
atexit.register(_shutil.rmtree, str(scratch), ignore_errors=True)
os.environ["HOME"] = str(scratch)
# Path.home() ignores HOME on Windows -- USERPROFILE is what it reads,
# so a suite setting only HOME silently tests the real home there.
os.environ["USERPROFILE"] = str(scratch)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""
os.environ["SERVICE_NAME"] = "lite-agent-test"   # controls GDRIVE_ROOT's default

spec = importlib.util.spec_from_file_location("la", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["la"] = mod
spec.loader.exec_module(mod)
mod.LEDGER_FILE = scratch / "spend.jsonl"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


def rc(stdout="", stderr="", code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


# --- the root is keyed by SERVICE_NAME, not one fixed name ------------------
check("GDRIVE_ROOT is per-deployment, built from SERVICE_NAME",
      mod.GDRIVE_ROOT == "iPandu/lite-agent-test")
check("GDRIVE_LEGACY_ROOT keeps the old flat name, for migration only",
      mod.GDRIVE_LEGACY_ROOT == "iPandu Data")
check("the two are different -- there is actually something to migrate FROM",
      mod.GDRIVE_ROOT != mod.GDRIVE_LEGACY_ROOT)


# --- _gdrive_path_exists: parent listing + substring, not a bet on lsd's own
# --- exit code for a path that might not resolve ---------------------------
def test_path_exists():
    calls = []

    def fake(*args, timeout=60):
        calls.append(args)
        if args[0] == "lsd" and args[1].endswith(":iPandu"):
            return rc(stdout="lite-agent-test\nother-bot\n", code=0)
        if args[0] == "lsd" and args[1].endswith(":"):
            return rc(stdout="iPandu\nSomeOtherFolder\n", code=0)
        return rc(code=1, stderr="not found")

    with patch.object(mod, "_rclone_run", side_effect=fake):
        check("a nested path is found via its PARENT's listing",
              mod._gdrive_path_exists("gdrive", "iPandu/lite-agent-test"))
        check("...and a sibling that isn't listed is correctly absent",
              not mod._gdrive_path_exists("gdrive", "iPandu/some-other-service"))
        check("a top-level path is found by listing the bare drive root",
              mod._gdrive_path_exists("gdrive", "iPandu"))
        check("...and one that was never created is correctly absent",
              not mod._gdrive_path_exists("gdrive", "iPandu Data"))

    def broken(*args, timeout=60):
        return rc(code=1, stderr="account suspended")
    with patch.object(mod, "_rclone_run", side_effect=broken):
        check("a parent listing that fails outright means 'does not exist', "
              "not a crash", not mod._gdrive_path_exists("gdrive", "iPandu/x"))


test_path_exists()


# --- _migrate_gdrive_root: the three real cases -----------------------------
def test_migrate():
    # 1. already migrated (or never had the legacy folder) -- a no-op
    def already_there(*args, timeout=60):
        if args[1].endswith(":iPandu"):
            return rc(stdout="lite-agent-test\n", code=0)
        return rc(stdout="", code=1)
    with patch.object(mod, "_rclone_run", side_effect=already_there):
        status, exists = mod._migrate_gdrive_root("gdrive")
    check("already migrated: reports nothing to do", status is None)
    check("...and confirms the root exists, so the caller never re-asks",
          exists is True)

    # 2. a fresh account, no legacy folder at all -- also a no-op
    def fresh_account(*args, timeout=60):
        return rc(stdout="", code=1)   # nothing exists anywhere yet
    with patch.object(mod, "_rclone_run", side_effect=fresh_account):
        status, exists = mod._migrate_gdrive_root("gdrive")
    check("a fresh account with no legacy folder: also nothing to do",
          status is None)
    check("...and correctly reports the root does not exist yet",
          exists is False)

    # 3. the real migration: legacy exists, new root does not -- a STATEFUL
    # fake Drive, since mkdir/moveto have to actually change what a later lsd
    # reports, the same way real rclone would.
    calls = []
    state = {"iPandu": False, "moved": False}
    def has_legacy(*args, timeout=60):
        calls.append(args)
        if args[0] == "mkdir" and args[1].endswith(":iPandu"):
            state["iPandu"] = True
            return rc(code=0)
        if args[0] == "moveto":
            state["moved"] = True
            return rc(code=0)
        if args[0] == "lsd" and args[1].endswith(":iPandu"):
            if not state["iPandu"]:
                return rc(stdout="", code=1)              # parent doesn't exist yet
            return rc(stdout="lite-agent-test\n" if state["moved"] else "",
                      code=0)                             # parent exists; child once moved
        if args[0] == "lsd" and args[1].endswith(":"):
            return rc(stdout="iPandu Data\n" if not state["moved"] else "",
                      code=0)                              # legacy, until it's moved away
        return rc(code=1)
    with patch.object(mod, "_rclone_run", side_effect=has_legacy):
        status, exists = mod._migrate_gdrive_root("gdrive")
    check("a real migration reports success", status is not None
          and "migrated" in status.lower())
    check("...confirms the new root now exists", exists is True)
    check("...the parent (iPandu) is created before the move",
          any(c[0] == "mkdir" and c[1].endswith(":iPandu") for c in calls))
    check("...moveto carries the WHOLE tree in one call, not file by file",
          any(c[0] == "moveto" and c[1].endswith(":iPandu Data")
              and c[2].endswith(":iPandu/lite-agent-test") for c in calls))
    mkdir_i = next(i for i, c in enumerate(calls) if c[0] == "mkdir")
    move_i = next(i for i, c in enumerate(calls) if c[0] == "moveto")
    check("...and the parent is created BEFORE the move, not after",
          mkdir_i < move_i)

    # a move that fails is reported, not silently swallowed
    def move_fails(*args, timeout=60):
        if args[0] == "lsd" and args[1].endswith(":iPandu"):
            return rc(stdout="", code=1)
        if args[0] == "lsd" and args[1].endswith(":"):
            return rc(stdout="iPandu Data\n", code=0)
        if args[0] == "mkdir":
            return rc(code=0)
        if args[0] == "moveto":
            return rc(code=1, stderr="quota exceeded")
        return rc(code=1)
    with patch.object(mod, "_rclone_run", side_effect=move_fails):
        status, exists = mod._migrate_gdrive_root("gdrive")
    check("a failed moveto is reported in the status, not hidden",
          status is not None and "failed" in status.lower())
    check("...and reports the root does NOT exist, since the move never landed",
          exists is False)

    # an account that resolves to the legacy name itself needs no migration
    with patch.object(mod, "GDRIVE_ROOT", mod.GDRIVE_LEGACY_ROOT):
        def legacy_is_current(*args, timeout=60):
            if args[1].endswith(":"):
                return rc(stdout="iPandu Data\n", code=0)
            return rc(code=1)
        with patch.object(mod, "_rclone_run", side_effect=legacy_is_current):
            status, exists = mod._migrate_gdrive_root("gdrive")
        check("SERVICE_NAME resolving to the legacy name needs no migration",
              status is None and exists is True)


test_migrate()


# --- the startup hook: best-effort, one account's crash doesn't stop others
def test_startup_hook():
    def boom(name):
        raise RuntimeError(f"drive unreachable for {name}")
    with patch.object(mod, "_list_gdrive_accounts",
                      return_value=["gdrive", "gdrive_second"]), \
            patch.object(mod, "_migrate_gdrive_root", side_effect=boom):
        try:
            mod.migrate_gdrive_roots_on_start()
            survived = True
        except Exception:
            survived = False
    check("a crash migrating one account never takes down startup",
          survived)

    seen = []
    def record(name):
        seen.append(name)
        return (f"migrated {name}", True)
    with patch.object(mod, "_list_gdrive_accounts",
                      return_value=["gdrive", "gdrive_company"]), \
            patch.object(mod, "_migrate_gdrive_root", side_effect=record):
        mod.migrate_gdrive_roots_on_start()
    check("every connected account is checked, not just the first",
          seen == ["gdrive", "gdrive_company"])


test_startup_hook()


# --- wiring: actually called at startup, alongside the other self-healing --
src = Path(SRC).read_text(encoding="utf-8")
check("migrate_gdrive_roots_on_start() runs inside apply_hardening_on_start()",
      "migrate_gdrive_roots_on_start()" in
      src.split("def apply_hardening_on_start")[1].split("\ndef ")[0])
check("...guarded the same way every other self-healing step there is -- a "
      "crash here must never block the bot from starting",
      "gdrive root migration failed on start" in src)
check("connect-time uses migrate's own existence answer, never re-asking",
      "migrated, root_exists = _migrate_gdrive_root(name)" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
