#!/usr/bin/env python3
"""Tests for choosing WHERE a Drive account writes: My Drive or a shared drive.

This encodes a real, silent failure. A report was sent "to the shared drive
TIPD"; the remote had no `team_drive` set, so rclone was working in the
account's My Drive the whole time. rclone models a shared drive as a different
ROOT, not a longer path, so no destination string the operator types can reach
one -- the upload would have reported success and the folder they were watching
would have stayed empty. (On the day, a quota error masked it: the shared
client_id rclone ships had run out, so nothing got that far.)

Everything here is therefore about not lying about where a file went:

  * `gdrive_target()` reports the truth for a remote, including the plain
    "My Drive" case where the key is simply absent.
  * `set_gdrive_target()` goes through `rclone config update`, never a
    hand-edit -- rclone owns that file's format and every other stanza in it
    is somebody's working credential.
  * It then LISTS the new root before claiming success. A setting that saved
    but cannot be reached is the same silent-success failure one step along.
  * `gdrive_shared_drives()` distinguishes "no shared drives" from "this token
    is not allowed to look", because the second is the drive.file scope and
    has a completely different fix.
"""
import atexit
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

SRC = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SRC.parent / "tools"))

HOME = tempfile.mkdtemp(prefix="isla_gdt_")
atexit.register(shutil.rmtree, str(HOME), ignore_errors=True)
os.environ["HOME"] = HOME
# Path.home() reads USERPROFILE on Windows and HOME on POSIX; setting only one
# points the suite at the developer's real home.
os.environ["USERPROFILE"] = HOME
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la_gdt", str(SRC))
mod = importlib.util.module_from_spec(spec)
sys.modules["la_gdt"] = mod
spec.loader.exec_module(mod)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)

def proc(stdout="", stderr="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


for fn in ("gdrive_shared_drives", "gdrive_target", "set_gdrive_target"):
    check(f"{fn}() exists in this source", callable(getattr(mod, fn, None)))
if not all(ok for _, ok in results):
    print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
    print("FAILED: the shared-drive helpers are absent -- every check below needs them")
    sys.exit(1)

CONF_WITH = "[gdrive]\ntype = drive\nscope = drive\nteam_drive = 0ABCdefGHIjk\n"
CONF_WITHOUT = "[gdrive]\ntype = drive\nscope = drive.file\n"

# --- 1. reading the current destination ------------------------------------
with patch.object(mod, "_rclone_run", return_value=proc(stdout=CONF_WITH)):
    check("a remote pointed at a shared drive reports that drive's id",
          mod.gdrive_target("gdrive") == "0ABCdefGHIjk")
with patch.object(mod, "_rclone_run", return_value=proc(stdout=CONF_WITHOUT)):
    check("a remote with no team_drive reports My Drive, as an empty string "
          "rather than a guess",
          mod.gdrive_target("gdrive") == "")
with patch.object(mod, "_rclone_run", side_effect=OSError("rclone gone")):
    check("an unreadable remote does not raise into the command handler",
          mod.gdrive_target("gdrive") == "")

# --- 2. listing what the account can actually see --------------------------
DRIVES = '[{"id":"0AB1","name":"TIPD","kind":"drive#drive"},' \
         ' {"id":"0AB2","name":"Rektorat"}]'
with patch.object(mod, "_rclone_run", return_value=proc(stdout=DRIVES)):
    ok, drives = mod.gdrive_shared_drives("gdrive")
    check("shared drives are listed with id and name", ok and len(drives) == 2)
    check("...in a shape the command can print",
          ok and drives[0] == {"id": "0AB1", "name": "TIPD"})

with patch.object(mod, "_rclone_run", return_value=proc(stdout="[]")):
    ok, drives = mod.gdrive_shared_drives("gdrive")
    check("an account with no shared drives succeeds with an empty list, "
          "which is not an error", ok and drives == [])

# The drive.file scope cannot see a shared drive somebody else made -- by
# definition, since it only ever sees what this bot created. Saying "none
# found" there would send the operator looking in Google's sharing settings
# for a problem that is in the token.
with patch.object(mod, "_rclone_run",
                  return_value=proc(stderr="Error 403: Insufficient Permission", rc=1)):
    ok, detail = mod.gdrive_shared_drives("gdrive")
    check("a permission refusal is NOT reported as 'no shared drives'", not ok)
    check("...it names the drive.file scope as the cause", "drive.file" in str(detail))
    check("...and points at the command that fixes it",
          "/connectgdrive manual" in str(detail))

with patch.object(mod, "_rclone_run", return_value=proc(stdout="not json")):
    ok, detail = mod.gdrive_shared_drives("gdrive")
    check("unparseable output is an error, not an empty list", not ok)

# --- 3. setting it, and proving it before saying so ------------------------
calls = []
def record(*args, **kw):
    calls.append(args)
    if args[0] == "lsd":
        return proc(stdout="  -1 2026-01-01 00:00:00        -1 Something\n")
    return proc()

with patch.object(mod, "_list_gdrive_accounts", return_value=["gdrive"]), \
     patch.object(mod, "_rclone_run", side_effect=record):
    ok, detail = mod.set_gdrive_target("gdrive", "0AB1")
check("setting a shared drive reports success once it verifies", ok)
check("...via `rclone config update`, never by hand-editing rclone.conf",
      any(a[:2] == ("config", "update") for a in calls))
check("...writing team_drive with the id given",
      any("team_drive=0AB1" in a for a in calls for a in a))
check("...and LISTING the new root before claiming anything",
      any(a[0] == "lsd" for a in calls))
check("...with a message that says where it now writes",
      "0AB1" in detail and "shared drive" in detail)

calls.clear()
with patch.object(mod, "_list_gdrive_accounts", return_value=["gdrive"]), \
     patch.object(mod, "_rclone_run", side_effect=record):
    ok, detail = mod.set_gdrive_target("gdrive", "")
check("clearing it goes back to My Drive", ok and "My Drive" in detail)
check("...by writing an EMPTY team_drive, not by deleting the remote",
      any("team_drive=" in a for a in calls for a in a)
      and not any(a[:2] == ("config", "delete") for a in calls))

# The failure that matters: saved, but the root cannot be reached. Reporting
# success there recreates the exact silent-success problem this feature exists
# to remove.
def update_ok_list_fails(*args, **kw):
    if args[0] == "lsd":
        return proc(stderr="couldn't find root directory ID", rc=1)
    return proc()

with patch.object(mod, "_list_gdrive_accounts", return_value=["gdrive"]), \
     patch.object(mod, "_rclone_run", side_effect=update_ok_list_fails):
    ok, detail = mod.set_gdrive_target("gdrive", "0BAD")
check("a destination that saves but cannot be listed is reported as FAILED",
      not ok)
check("...and says the root could not be listed, not something vague",
      "cannot be listed" in detail)

with patch.object(mod, "_list_gdrive_accounts", return_value=["gdrive"]):
    ok, detail = mod.set_gdrive_target("nope", "0AB1")
check("an unknown account is refused before rclone is touched",
      not ok and "nope" in detail)

# --- 4. the command is actually reachable ----------------------------------
src = SRC.read_text(encoding="utf-8")
check("cmd_gdrivetarget exists", callable(getattr(mod, "cmd_gdrivetarget", None)))
# The own-OAuth-client setup card was once present, complete, and called by
# nothing -- found by a symbol index rather than by review. A handler that is
# never registered is a feature that does not exist.
check("...and is registered as /gdrivetarget",
      'CommandHandler("gdrivetarget", cmd_gdrivetarget)' in src)
check("...gated like /gdrive, as a room-wide setting rather than a personal one",
      "_may_authorize_group_action" in src.split("async def cmd_gdrivetarget")[1]
      .split("async def cmd_gdrive(")[0])
check("...and it reuses the room's chosen account rather than inventing one",
      "_gdrive_effective_default" in src.split("async def cmd_gdrivetarget")[1]
      .split("async def cmd_gdrive(")[0])

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
