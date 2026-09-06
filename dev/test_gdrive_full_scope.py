#!/usr/bin/env python3
"""Tests for the full-drive connect path -- the one that can reach a shared drive.

Two facts drive all of this, both learned from a live deployment rather than
from documentation:

  1. **drive.file cannot touch a shared drive at all.** Not "can only see its
     own files": rclone resolves `team_drive` through Drives.Get, and Google
     answers that call with "Request had insufficient authentication scopes"
     under drive.file. The whole remote then fails -- My Drive uploads
     included -- until team_drive is cleared again. So a shared drive needs a
     token issued for full `drive`, which Google's device flow will not issue.

  2. **A refresh token is bound to the client that issued it.** Attaching the
     wrong client_id to a remote does not postpone the 2026 retirement of
     rclone's shared client, it breaks the account immediately. So the client
     stored on the remote has to be the one that actually issued the token --
     which the bot can only know because it printed the `rclone authorize`
     command itself.

The manual path used to hardcode drive.file and attach no client at all, which
made it useless for the case it exists for and quietly put the account back on
rclone's shared client.
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

HOME = tempfile.mkdtemp(prefix="isla_gdfs_")
atexit.register(shutil.rmtree, str(HOME), ignore_errors=True)
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME     # Path.home() reads this one on Windows
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la_gdfs", str(SRC))
mod = importlib.util.module_from_spec(spec)
sys.modules["la_gdfs"] = mod
spec.loader.exec_module(mod)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)

def proc(stdout="", stderr="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


TOKEN = '{"access_token":"ya29.x","refresh_token":"1//r","expiry":"2030-01-01T00:00:00Z"}'

# --- 1. connect_gdrive_account takes a scope, and honours it ---------------
import inspect  # noqa: E402
sig = inspect.signature(mod.connect_gdrive_account)
check("connect_gdrive_account() accepts a scope", "scope" in sig.parameters)
check("...defaulting to drive.file, which is right for the device flow",
      sig.parameters["scope"].default == "drive.file")

def run_connect(scope=None, client=None):
    """Capture what would be handed to `rclone config create`."""
    seen = []
    def fake(*args, **kw):
        seen.append(args)
        if args[0] == "lsd":
            return proc(stdout=f"  -1 2026 {mod.GDRIVE_ROOT}\n")
        return proc()
    kwargs = {}
    if client is not None:
        kwargs["oauth_client"] = client
    if scope is not None:
        kwargs["scope"] = scope
    with patch.object(mod, "_list_gdrive_accounts", return_value=[]), \
         patch.object(mod, "_rclone_run", side_effect=fake):
        ok, detail = mod.connect_gdrive_account("gdrive_x", TOKEN, **kwargs)
    create = next((a for a in seen if a[:2] == ("config", "create")), ())
    return ok, detail, create

ok, detail, create = run_connect()
check("a default connect still asks for drive.file", "scope=drive.file" in create)
check("...and reports success once verified", ok)

ok, detail, create = run_connect(scope="drive")
check("a full-drive connect writes scope=drive, which is what a shared drive "
      "needs", "scope=drive" in create and "scope=drive.file" not in create)

CLIENT = {"client_id": "123.apps.googleusercontent.com", "client_secret": "GOCSPX-s"}
ok, detail, create = run_connect(scope="drive", client=CLIENT)
check("the issuing OAuth client is stored on the remote",
      f"client_id={CLIENT['client_id']}" in create)
check("...with its secret, since rclone needs both to refresh",
      f"client_secret={CLIENT['client_secret']}" in create)

ok, detail, create = run_connect(scope="drive", client=None)
check("with no client of your own, none is invented -- attaching the wrong one "
      "breaks the account immediately rather than later",
      not any(str(a).startswith("client_id=") for a in create))

# --- 2. the command the operator is told to run matches what gets stored ---
check("_gdrive_authorize_command() exists",
      callable(getattr(mod, "_gdrive_authorize_command", None)))
with patch.object(mod, "read_gdrive_desktop_client", return_value={}):
    cmd = mod._gdrive_authorize_command()
check("with no client set up, the command is the plain one", "authorize drive" in cmd)
check("...and still asks for FULL drive, since that is why this path exists",
      "--drive-scope drive" in cmd and "drive.file" not in cmd)
check("...and passes no client id it does not have",
      "googleusercontent" not in cmd)

with patch.object(mod, "read_gdrive_desktop_client", return_value=CLIENT):
    cmd = mod._gdrive_authorize_command()
check("with a client set up, the command authorizes THROUGH it",
      CLIENT["client_id"] in cmd and CLIENT["client_secret"] in cmd)
check("...so the token the operator pastes back really was issued by the "
      "client the remote will store",
      "--drive-scope drive" in cmd)

# --- 3. the instructions say which client is being used, and why -----------
with patch.object(mod, "read_gdrive_desktop_client", return_value=CLIENT):
    text_id = mod._gdrive_connect_instructions("id", "gdrive_2")
    text_en = mod._gdrive_connect_instructions("en", "gdrive_2")
check("the instructions embed that exact command", CLIENT["client_id"] in text_id)
check("...and say the account will refresh through the operator's own project",
      "2026" in text_en and "your own project" in text_en)

with patch.object(mod, "read_gdrive_desktop_client", return_value={}):
    warn_en = mod._gdrive_connect_instructions("en", "gdrive_2")
    warn_id = mod._gdrive_connect_instructions("id", "gdrive_2")
check("without one, the instructions WARN that rclone's shared client is used",
      "shared" in warn_en and "2026" in warn_en)
check("...and name the command that fixes it, in both languages",
      "/connectgdrive setupclient desktop" in warn_en
      and "/connectgdrive setupclient desktop" in warn_id)

# --- 4. the manual path actually passes both ------------------------------
src = SRC.read_text(encoding="utf-8")
manual = src.split("# step == \"await_gdrive_token\"")[1][:2500]
check("the manual path hands connect_gdrive_account the DESKTOP client -- the "
      "only type `rclone authorize` can use",
      "read_gdrive_desktop_client()" in manual)
check("...and asks for full drive rather than the drive.file default",
      '"drive")' in manual)
check("...and the old call that passed neither is gone",
      "connect_gdrive_account, name, text)" not in src)

# --- 5. the two clients are kept apart ------------------------------------
# Google binds the grant type to the CLIENT type: the device flow needs a "TV
# and Limited Input devices" client, `rclone authorize` needs a "Desktop app"
# one. Reusing the first for the second was tried on a live deployment and
# Google answered "Error 400: invalid_request" before drawing the consent
# screen. A fallback between them would turn a clear "not set up yet" into
# exactly that, so there must not be one.
check("the desktop client has a file of its own",
      "gdrive_oauth_client_desktop" in str(mod.GDRIVE_DESKTOP_CLIENT_FILE))
check("...separate from the device-flow client's",
      mod.GDRIVE_DESKTOP_CLIENT_FILE != mod.GDRIVE_CLIENT_FILE)

# Both files live under BASE_DIR, which is the SOURCE directory -- not $HOME.
# Writing them for real would drop credentials into the repo and hand the next
# suite a client it never set up; test_gdrive_client_id.py reads the same file.
# Point them at scratch paths for the duration instead.
scratch = pathlib.Path(tempfile.mkdtemp(prefix="isla_gdcli_"))
atexit.register(shutil.rmtree, str(scratch), ignore_errors=True)
with patch.object(mod, "GDRIVE_CLIENT_FILE", scratch / "tv.json"), \
     patch.object(mod, "GDRIVE_DESKTOP_CLIENT_FILE", scratch / "desktop.json"):
    mod.write_gdrive_client("tv-id.apps.googleusercontent.com", "GOCSPX-tv")
    check("a device-flow client does NOT satisfy the desktop reader -- no silent "
          "fallback onto a client Google will reject",
          mod.read_gdrive_desktop_client() == {})

    mod.write_gdrive_client("desk-id.apps.googleusercontent.com", "GOCSPX-desk",
                            desktop=True)
    check("the desktop client is readable once stored",
          mod.read_gdrive_desktop_client().get("client_id")
          == "desk-id.apps.googleusercontent.com")
    check("...and storing it did not overwrite the device-flow one",
          mod.read_gdrive_client().get("client_id")
          == "tv-id.apps.googleusercontent.com")

check("`setupclient desktop` reaches the Desktop-app card, not the TV one",
      "_gdrive_desktop_client_setup_instructions" in src
      and 'second in ("desktop", "manual", "rclone")' in src)
card = mod._gdrive_desktop_client_setup_instructions("en")
check("...which says Desktop app explicitly", "Desktop app" in card)
check("...and explains why the other client cannot be reused, since that is "
      "the error the operator just hit", "invalid_request" in card)
check("...while pointing at the SAME Google Cloud project, so nothing else "
      "has to be set up twice", "same project" in card)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
