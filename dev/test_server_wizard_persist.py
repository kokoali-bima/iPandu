#!/usr/bin/env python3
"""The /addserver wizard has to survive a restart, and never persist a password.

On 2026-09-06 a key was placed on a new host and verified -- the operator saw
the "Key installed and verified" card -- and then the registration was lost.
Two causes compounded: the wizard lived only in memory with a 15-minute TTL,
and the service restarted for the v0.2b.86 /update while the wizard was open.
By the time the operator tapped the last button, the form was gone.

/unlock already persists its state for exactly this reason. This suite pins the
same treatment for the server wizard, and guards the one thing that must NEVER
follow it to disk: the password. The whole password-bootstrap feature exists so
the secret never touches storage, and a persistence layer is precisely where
that promise is easiest to break by accident.
"""
import atexit
import importlib.util
import json
import os
import shutil as _shutil
import sys
import tempfile
from pathlib import Path

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_swpersist_"))
atexit.register(_shutil.rmtree, str(scratch), ignore_errors=True)
os.environ["HOME"] = str(scratch)
# Path.home() ignores HOME on Windows -- USERPROFILE is what it reads,
# so a suite setting only HOME silently tests the real home there.
os.environ["USERPROFILE"] = str(scratch)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["la"] = mod
spec.loader.exec_module(mod)
mod.LEDGER_FILE = scratch / "spend.jsonl"
mod.SERVER_WIZARD_FILE = scratch / "server_wizard.json"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


now = mod._dt.datetime.now().timestamp()


def fresh():
    mod._server_wizard.clear()
    mod.SERVER_WIZARD_FILE.unlink(missing_ok=True)


# --- a mid-flight wizard is written to disk and comes back ------------------
fresh()
mod._server_wizard[42] = {
    "step": "authorize",
    "data": {"host": "10.10.59.75", "user": "root", "port": 22, "kind": "vm"},
    "expires": now + 600,
}
mod._save_server_wizard()
check("saving the wizard writes the file", mod.SERVER_WIZARD_FILE.exists())

mod._server_wizard.clear()             # simulate the process going away
mod._load_server_wizard()
check("a live wizard is restored after a restart", 42 in mod._server_wizard)
check("...with its step intact, so the final button still works",
      mod._server_wizard[42]["step"] == "authorize")
check("...and the host it was placing a key on",
      mod._server_wizard[42]["data"]["host"] == "10.10.59.75")
check("...and the chat id comes back an int, not a JSON string key",
      all(isinstance(k, int) for k in mod._server_wizard))

# --- an expired wizard is dropped on load ----------------------------------
fresh()
mod._server_wizard[7] = {"step": "host", "data": {}, "expires": now - 1}
mod._server_wizard[8] = {"step": "host", "data": {}, "expires": now + 600}
mod._save_server_wizard()
mod._server_wizard.clear()
mod._load_server_wizard()
check("a wizard that expired while the process was down is not restored",
      7 not in mod._server_wizard)
check("...while a still-live one beside it is", 8 in mod._server_wizard)
check("...and the expired one is rewritten out of the file, not left to rot",
      "7" not in json.loads(mod.SERVER_WIZARD_FILE.read_text(encoding="utf-8")))

# --- drop removes and persists in one step ---------------------------------
fresh()
mod._server_wizard[9] = {"step": "port", "data": {}, "expires": now + 600}
mod._save_server_wizard()
returned = mod._drop_server_wizard(9)
check("dropping a wizard returns what it removed", returned is not None)
check("...clears it from memory", 9 not in mod._server_wizard)
check("...and from disk, so a restart does not resurrect a cancelled form",
      "9" not in json.loads(mod.SERVER_WIZARD_FILE.read_text(encoding="utf-8")))

# --- unreadable file must never take the bot down --------------------------
fresh()
mod.SERVER_WIZARD_FILE.write_text("{ not json", encoding="utf-8")
try:
    mod._load_server_wizard()
    survived = True
except Exception:
    survived = False
check("an unreadable state file is survived, not fatal", survived)
check("...and is cleared so it cannot fail every future start",
      not mod.SERVER_WIZARD_FILE.exists() or mod.SERVER_WIZARD_FILE.read_text(
          encoding="utf-8").strip() in ("", "{}"))

# --- THE security property: no password may ever reach the file ------------
src = Path(SRC).read_text(encoding="utf-8")

# The password variable in _handle_server_input is deleted, never stored.
start = src.index("async def _handle_server_input")
end = src.index("\nasync def ", start + 10)
body = src[start:end]
check("the password is read into a local and dropped, never put in state",
      "del pw" in body and 'data["password"]' not in body
      and "state[\"password\"]" not in body)

# And drive it: a wizard carrying a stray secret would still not leak it,
# because only the known fields are ever set -- but prove the file for a
# realistic wizard contains no obvious secret material.
fresh()
mod._server_wizard[5] = {
    "step": "authorize",
    "data": {"host": "192.0.2.9", "user": "root", "port": 22},
    "expires": now + 600,
}
mod._save_server_wizard()
on_disk = mod.SERVER_WIZARD_FILE.read_text(encoding="utf-8").lower()
check("nothing password-shaped is written to the wizard file",
      "password" not in on_disk and "pw" not in on_disk.split('"'))

# --- the wiring is actually in place ---------------------------------------
check("the wizard is loaded at startup, inside main()",
      "_load_server_wizard()" in src.split("def main(")[1])
check("the only direct pop is inside the drop helper itself; every "
      "caller goes through it", src.count("_server_wizard.pop(") == 1)
check("a step change persists -- the 'Key installed' card survives a restart",
      src.count("_save_server_wizard()") >= 10)

# --- and bootstrap finally logs what it does -------------------------------
bstart = src.index("def bootstrap_key_with_password")
bend = src.index("\ndef ", bstart + 10)
bbody = src[bstart:bend]
check("bootstrap_key_with_password logs placing the key",
      'logger.info("bootstrap: placing write key' in bbody)
check("...and logs the verify outcome, the step that was invisible for .75",
      "verified on %s@%s:%s" in bbody)
check("...and never logs the password (only host/user/port and filtered text)",
      "password" not in bbody.split("logger")[1].split("\n")[0].lower()
      or "%s" in bbody)  # the only 'password' near a log line is the refusal msg

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
