#!/usr/bin/env python3
"""Tests for pinning an upload folder by browsing to it.

The failure this ends, reported from a live room: the same weekly report
landed in "Laporan", then "laporan/september", then "Reports/2026". Nothing
errored -- the `to=` folder is written by the MODEL, freshly each turn, and
rclone creates whatever it is given. Three folders nobody chose, and the
operator hunting for the file each time.

So a room browses the real tree and pins a folder. Under that pin:

  * a directory the model names is kept only if it ALREADY EXISTS. Nothing is
    ever created, so an invented folder costs nothing -- the file lands in the
    pinned folder, where it would have gone anyway.
  * the group-name subfolder is not added -- somebody who browsed to a folder
    and pressed "use this one" meant that folder, not a child of it.

The first rule was originally "drop every directory", which stopped the
invention and also flattened a structure the operator had built by hand:
"Wahyu-Work" holds "BACKUP OPNSENSE" and "Laporan", and backups and reports
were landing together. Existence is the right test, not absence.

And one that is easy to miss: `team_drive` is per-ACCOUNT rclone config, not
per-room. A second room pinning a different shared drive on the same account
moves the root under the first one, whose next upload would then land in the
wrong drive at the same folder name. The upload path re-asserts it.
"""
import atexit
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

SRC = pathlib.Path(sys.argv[1]).resolve()

HOME = tempfile.mkdtemp(prefix="isla_gdfold_")
atexit.register(shutil.rmtree, str(HOME), ignore_errors=True)
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME     # Path.home() reads this one on Windows
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la_gdfold", str(SRC))
mod = importlib.util.module_from_spec(spec)
sys.modules["la_gdfold"] = mod
spec.loader.exec_module(mod)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)

def proc(stdout="", stderr="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)

# Keep every write inside the scratch dir: BASE_DIR is the SOURCE directory,
# so an unpatched run would drop json into the repo and hand the next suite
# state it never set up.
scratch = pathlib.Path(tempfile.mkdtemp(prefix="isla_gdfold_s_"))
atexit.register(shutil.rmtree, str(scratch), ignore_errors=True)
DEST = scratch / "gdrive_dest.json"
_dest_patch = patch.object(mod, "GDRIVE_DEST_FILE", DEST)
_dest_patch.start()
atexit.register(_dest_patch.stop)


# --- 1. storing and reading a pin ------------------------------------------
ACCOUNTS = ["gdrive_2"]
PIN = {"account": "gdrive_2", "drive_id": "0APRxA_XdLBmyUk9PVA",
       "drive_name": "TIPD", "folder": "Laporan/2026"}

with patch.object(mod, "_list_gdrive_accounts", return_value=ACCOUNTS):
    check("with nothing pinned, a room has no destination",
          mod.gdrive_pinned_dest("-100") is None)
    mod.set_gdrive_pinned_dest("-100", PIN)
    check("a pin is stored and read back", mod.gdrive_pinned_dest("-100") == PIN)
    check("...and is per-room, not global", mod.gdrive_pinned_dest("-200") is None)
    check("the file it lives in is the patched one, not one in the repo",
          DEST.exists())

# The account can be disconnected after a room pinned inside it. Uploading to
# a same-named folder on a different account is exactly the surprise this
# feature exists to end, so the pin stops applying instead.
with patch.object(mod, "_list_gdrive_accounts", return_value=["gdrive_other"]):
    check("a pin on an account that no longer exists is ignored, not followed "
          "onto whatever account remains", mod.gdrive_pinned_dest("-100") is None)

with patch.object(mod, "_list_gdrive_accounts", return_value=ACCOUNTS):
    for junk in ("nonsense", {"folder": "x"}, {}, None):
        mod.set_gdrive_pinned_dest("-300", junk)
        check(f"a malformed entry ({junk!r}) is treated as no pin",
              mod.gdrive_pinned_dest("-300") is None)
    mod.set_gdrive_pinned_dest("-100", None)
    check("clearing a pin removes it", mod.gdrive_pinned_dest("-100") is None)
    mod.set_gdrive_pinned_dest("-100", PIN)

DEST.write_text("{ this is not json", encoding="utf-8")
with patch.object(mod, "_list_gdrive_accounts", return_value=ACCOUNTS):
    check("an unreadable file reports no pin rather than crashing the upload",
          mod.gdrive_pinned_dest("-100") is None)
DEST.write_text(json.dumps({"-100": PIN}), encoding="utf-8")


# --- 2. listing folders ----------------------------------------------------
LSJSON = json.dumps([{"Name": "zeta"}, {"Name": "Alpha"}, {"Name": "beta"},
                     {"Path": "no name here"}])

def listing(stdout=LSJSON, rc=0, err=""):
    seen = []
    def fake(*args, timeout=60):
        seen.append(args)
        return proc(stdout=stdout, stderr=err, rc=rc)
    with patch.object(mod, "_rclone_run", side_effect=fake):
        ok, out = mod.gdrive_list_folders("gdrive_2", "Laporan")
    return ok, out, seen[0] if seen else ()

ok, folders, args = listing()
check("folders come back", ok and "Alpha" in folders)
check("...sorted case-insensitively, so the keyboard reads like a folder list",
      folders == ["Alpha", "beta", "zeta"])
check("...with files excluded -- a busy folder is unusable as a keyboard",
      "--dirs-only" in args)
check("...and a row with no name is skipped rather than becoming an empty button",
      "" not in folders)
check("the path asked for is the one given", f"gdrive_2:Laporan" in args)

_, _, args_root = listing()
with patch.object(mod, "_rclone_run", side_effect=lambda *a, timeout=60: proc(stdout=LSJSON)):
    ok_root, _ = mod.gdrive_list_folders("gdrive_2", "")
check("an empty path means the drive's own root", ok_root)

ok, detail, _ = listing(rc=1, err="permission denied")
check("an rclone failure is reported, not shown as an empty folder -- those "
      "look identical in a keyboard and mean opposite things",
      not ok and "permission denied" in str(detail))
ok, detail, _ = listing(stdout="<html>nope</html>")
check("output that is not JSON is an error too", not ok)


# --- 3. what the upload actually does with a pin ---------------------------
src = SRC.read_text(encoding="utf-8")
upload = src.split("pinned = gdrive_pinned_dest(chat_id)")[1][:1600]
check("a pinned room takes its ACCOUNT from the pin, not from the room "
      "default -- pinning browsed inside one account",
      'remote = pinned["account"]' in upload)
check("...its root folder from the pin", 'root = pinned.get("folder", "")' in upload)
check("...and only the BASENAME of what the model wrote, so it cannot invent "
      "a folder any more",
      'rsplit("/", 1)[-1]' in upload)
check("_gdrive_effective_path -- which prepends the group's own folder -- is "
      "used only when nothing is pinned",
      upload.index("_gdrive_effective_path") > upload.index('root = pinned.get'))
check("the pinned drive is re-asserted before uploading, because team_drive "
      "is per-account and another room can have moved it",
      "gdrive_target" in upload and "set_gdrive_target" in upload)

check("_gdrive_upload takes a root override", "root: Optional[str] = None"
      in src.split("def _gdrive_upload")[1][:300])

def upload_dest(root, rel="report.pdf"):
    seen = []
    def fake_run(argv, **kw):
        seen.append(argv)
        return proc()
    with patch.object(mod.subprocess, "run", side_effect=fake_run), \
         patch.object(mod, "_rclone_path", return_value="rclone"), \
         patch.object(mod, "_gdrive_share_link", return_value="", create=True):
        try:
            mod._gdrive_upload("gdrive_2", "/tmp/x.pdf", rel, root)
        except Exception:
            pass
    return next((a for a in seen if any("gdrive_2:" in str(x) for x in a)), [])

argv = upload_dest("Laporan/2026")
check("with a pinned root the destination is <account>:<folder>/<file>",
      any(str(a) == "gdrive_2:Laporan/2026/report.pdf" for a in argv))
argv = upload_dest(None)
check("with no override it still goes under GDRIVE_ROOT, unchanged",
      any(str(a) == f"gdrive_2:{mod.GDRIVE_ROOT}/report.pdf" for a in argv))
argv = upload_dest("")
check("a pin at the drive's own root writes there, with no stray slash",
      any(str(a) == "gdrive_2:report.pdf" for a in argv))


# --- 4. the picker keyboard ------------------------------------------------
DRIVES = [{"id": "0AAaa", "name": "TIPD"}, {"id": "0ABbb", "name": "Riset"}]
state_drive = {"account": "gdrive_2", "step": "drive", "drives": DRIVES,
               "drive_id": "", "drive_name": "", "path": "", "folders": []}
rows = mod._gdrive_picker_rows(state_drive, "id")
labels = [b.text for r in rows for b in r]
data = [b.callback_data for r in rows for b in r]
check("the drive step offers My Drive", any("My Drive" in l for l in labels))
check("...and every shared drive it can see",
      any("TIPD" in l for l in labels) and any("Riset" in l for l in labels))
check("My Drive is index -1, which is not a shared-drive index",
      "gdf:drive:-1" in data)
check("a cancel button is always present", "gdf:cancel" in data)

state_browse = {"account": "gdrive_2", "step": "browse", "drives": DRIVES,
                "drive_id": "0AAaa", "drive_name": "TIPD",
                "path": "Laporan/2026", "folders": ["Januari", "Februari"]}
rows = mod._gdrive_picker_rows(state_browse, "id")
data = [b.callback_data for r in rows for b in r]
check("browsing offers each subfolder by INDEX -- a Drive path routinely "
      "exceeds Telegram's 64-byte callback_data limit",
      "gdf:open:0" in data and "gdf:open:1" in data)
check("...an up button, since we are not at the root", "gdf:up" in data)
check("...and a way to accept where we stand", "gdf:use" in data)
check("no callback_data can breach Telegram's 64-byte cap",
      all(len(d.encode()) <= 64 for d in data))

state_root = dict(state_browse, path="", folders=[])
data_root = [b.callback_data for r in mod._gdrive_picker_rows(state_root, "id") for b in r]
check("at the root there is no up button to press", "gdf:up" not in data_root)
check("...but an empty folder can still be chosen -- that is a real answer",
      "gdf:use" in data_root)

for lang in ("en", "id"):
    text = mod._gdrive_picker_text(state_browse, lang)
    check(f"[{lang}] the text says which drive and where in it",
          "TIPD" in text and "Laporan/2026" in text)
    empty = mod._gdrive_picker_text(state_root, lang)
    check(f"[{lang}] an empty folder says so, rather than looking broken",
          "subfolder" in empty.lower())


# --- 5. wiring -------------------------------------------------------------
check("/gdrivefolder is registered as a command",
      'CommandHandler("gdrivefolder", cmd_gdrivefolder)' in src)
check("...and its buttons have a handler", 'pattern="^gdf:"' in src)
check("...that does not collide with /gdrive's own prefix",
      'pattern="^gdrv:"' in src)
check("it is offered in /help, in both languages",
      src.count("/gdrivefolder [off]") == 2)
check("`off` clears the pin, so this is reversible from the chat",
      'in ("off", "clear", "hapus", "mati")' in src)
check("gdrive_dest.json is not committed -- it names folders in the "
      "operator's Drive",
      "gdrive_dest.json" in (SRC.parent / ".gitignore").read_text(encoding="utf-8"))


# --- 6. a subfolder that already exists survives the pin -------------------
# Dropping every directory stopped the invention it was aimed at and also
# flattened a structure the operator had built by hand: on a real Drive,
# "Wahyu-Work" holds "BACKUP OPNSENSE" and "Laporan", and backups and reports
# were landing together. A named folder is kept when it is really there.
TREE = {
    "Wahyu-Work": ["BACKUP OPNSENSE", "Laporan"],
    "Wahyu-Work/Laporan": ["2026"],
    "Wahyu-Work/BACKUP OPNSENSE": [],
    "Wahyu-Work/Laporan/2026": [],
}

def subdir(to_raw, base="Wahyu-Work", tree=None, fail_at=None):
    asked = []
    def fake(account, path):
        asked.append(path)
        if fail_at is not None and path == fail_at:
            return False, "403 insufficient permissions"
        return True, list((TREE if tree is None else tree).get(path, []))
    with patch.object(mod, "gdrive_list_folders", side_effect=fake):
        return mod._gdrive_existing_subdir("gdrive_3", base, to_raw), asked

got, _ = subdir("BACKUP OPNSENSE/opnsense-0908.xml")
check("a real subfolder of the pin is kept", got == "BACKUP OPNSENSE")
got, _ = subdir("Laporan/juni.pdf")
check("...so backups and reports can land in their own folders again",
      got == "Laporan")
got, _ = subdir("Laporan/2026/juni.pdf")
check("...nested, as deep as it is real", got == "Laporan/2026")

got, _ = subdir("backup opnsense/x.xml")
check("matching ignores case -- a brief saying 'backup opnsense' must reach "
      "'BACKUP OPNSENSE'", got == "BACKUP OPNSENSE")
check("...and the name STORED IN DRIVE is what is used, or Drive would happily "
      "hold two folders differing only in case",
      got == "BACKUP OPNSENSE" and got != "backup opnsense")

got, asked = subdir("Reports/2026/juni.pdf")
check("a folder that does not exist is NOT created -- the file falls back to "
      "the pinned folder itself", got == "")
check("...and the walk stops at the first miss rather than probing deeper",
      asked == ["Wahyu-Work"])

got, _ = subdir("Laporan/Tidak-Ada/juni.pdf")
check("a real folder followed by an invented one keeps only the real part",
      got == "Laporan")

got, _ = subdir("juni.pdf")
check("a bare filename asks for no subfolder at all", got == "")
got, _ = subdir("/Laporan/juni.pdf")
check("a leading slash is not an escape, just noise", got == "Laporan")
got, _ = subdir("../../etc/passwd")
check("..-segments cannot climb out of the pinned folder", got == "")

got, _ = subdir("Laporan/juni.pdf", base="", tree={"": ["Laporan"]})
check("a pin at the drive root still resolves subfolders", got == "Laporan")

got, asked = subdir("Laporan/juni.pdf", fail_at="Wahyu-Work")
check("if the folder cannot be listed, nothing is assumed -- guessing 'it "
      "probably exists' is how a folder gets created", got == "")

deep = "/".join(f"L{i}" for i in range(6)) + "/x.pdf"
tree = {"Wahyu-Work": ["L0"], **{f"Wahyu-Work/{'/'.join(f'L{j}' for j in range(i+1))}":
                                 [f"L{i+1}"] for i in range(6)}}
got, asked = subdir(deep, tree=tree)
check("the walk is capped, so a pathological path cannot spend an rclone call "
      "per segment", len(asked) <= mod.GDRIVE_PINNED_MAX_DEPTH)

up = src.split("pinned = gdrive_pinned_dest(chat_id)")[1][:1600]
check("the upload path consults it", "_gdrive_existing_subdir" in up)
check("...and still keeps only the BASENAME as the file itself",
      'rsplit("/", 1)[-1]' in up)

# --- 7. the brief teaches the syntax the parser actually accepts -----------
# It said `GDRIVE: <file> -> <folder/name>`. extract_gdrive() needs file= and
# to=, so an arrow parsed to nothing: no upload, no error, no log line. MOVE
# really does take an arrow, which is how the arrow spread onto the one
# marker that rejects it.
brief = mod.CAPABILITIES_BRIEF
check("the brief shows the upload marker with file= and to=",
      "GDRIVE: file=" in brief and "| to=" in brief)
check("...and no longer with an arrow, which parses to nothing",
      "GDRIVE: <local file> ->" not in brief)
check("MOVE keeps its arrow, because that one really is an arrow",
      "GDRIVE_MOVE: <from> -> <to>" in brief)
_, parsed = mod.extract_gdrive("GDRIVE: file=/tmp/a.pdf | to=Laporan/a.pdf")
check("the documented form is what the parser accepts",
      parsed and parsed[0]["to"] == "Laporan/a.pdf")
_, parsed_arrow = mod.extract_gdrive("GDRIVE: /tmp/a.pdf -> Laporan/a.pdf")
check("...and the old documented form really did parse to nothing, which is "
      "why this mattered", parsed_arrow == [])
# Deliberately NOT asserting the brief names /gdrivefolder. The model cannot
# run it -- pinning is the operator's act -- and test_capabilities_brief.py
# caps this text at ~800 tokens because both CLIs pay for it every
# conversation. What the model has to know is the RULE.
check("the brief tells the model an existing subfolder is honoured under a pin",
      "ALREADY EXISTS" in brief)
check("...and that one which does not exist is not created for it",
      "not created" in brief and "pinned folder" in brief)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
