#!/usr/bin/env python3
"""The /gdrive picker shows the Google account, not the rclone remote name.

Every button read "gdrive", "gdrive_2" -- the internal label the OPERATOR
picked when connecting each account, meaningless to whoever is tapping the
button in a group chat. rclone itself has no command for the account's own
identity: confirmed against `rclone about --help` (quota fields only --
total/used/free/trashed/other/objects) and `rclone backend help drive`
(get/set/shortcut/drives/untrash/copyid/moveid/exportformats/importformats/
query/rescue -- files and backend config, nothing about who the account is).
The Drive API itself answers in one call, `about?fields=user`, under the same
"drive" scope this bot already requires for shared-drive detection.

The lookup is cached and best-effort throughout: a failure anywhere in it
must fall back to the raw remote name, never to an error shown in the picker.
"""
import atexit
import importlib.util
import json
import os
import shutil as _shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_gdrivelabel_"))
atexit.register(_shutil.rmtree, str(scratch), ignore_errors=True)
os.environ["HOME"] = str(scratch)
os.environ["USERPROFILE"] = str(scratch)  # Path.home() reads this on Windows
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["la"] = mod
spec.loader.exec_module(mod)
mod.LEDGER_FILE = scratch / "spend.jsonl"
mod.GDRIVE_LABELS_FILE = scratch / "gdrive_labels.json"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


def rc(stdout="", stderr="", code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


# --- gdrive_account_label: cache hit, and the fallback that matters most ---
def test_label_lookup():
    mod.GDRIVE_LABELS_FILE.write_text(json.dumps({"gdrive": "ops@example.com"}),
                                      encoding="utf-8")
    check("a cached account shows its real email",
          mod.gdrive_account_label("gdrive") == "ops@example.com")
    check("an account never looked up shows the raw remote name, not blank "
          "or an error", mod.gdrive_account_label("gdrive_2") == "gdrive_2")

    mod.GDRIVE_LABELS_FILE.unlink()
    check("no cache file at all is the same as an empty one -- still the "
          "raw name, not a crash", mod.gdrive_account_label("gdrive") == "gdrive")


test_label_lookup()


# --- _rclone_token_for: pulled straight out of rclone.conf's own format ----
def test_token_parsing():
    conf_dir = scratch / ".config" / "rclone"
    conf_dir.mkdir(parents=True)
    conf = conf_dir / "rclone.conf"
    with patch.object(mod, "RCLONE_CONF", conf):
        conf.write_text(
            '[gdrive]\n'
            'type = drive\n'
            'scope = drive\n'
            'token = {"access_token":"AAA","token_type":"Bearer",'
            '"refresh_token":"RRR","expiry":"2026-01-01T00:00:00Z"}\n'
            '\n'
            '[gdrive_2]\n'
            'type = drive\n'
            'token = {"access_token":"BBB"}\n',
            encoding="utf-8",
        )
        tok = mod._rclone_token_for("gdrive")
        check("the right account's token comes back, parsed as JSON",
              tok is not None and tok.get("access_token") == "AAA")
        tok2 = mod._rclone_token_for("gdrive_2")
        check("...and a second account in the same file gets its OWN token, "
              "not the first one's", tok2 is not None and tok2.get("access_token") == "BBB")
        check("a name with no section in the file is just absent, not a crash",
              mod._rclone_token_for("gdrive_missing") is None)

    with patch.object(mod, "RCLONE_CONF", scratch / "does-not-exist.conf"):
        check("no rclone.conf at all: absent, not a crash",
              mod._rclone_token_for("gdrive") is None)


test_token_parsing()


# --- _fetch_gdrive_account_email: the real round trip, all mocked ----------
def test_fetch_email():
    # 1. the happy path: remote reachable, token readable, Google answers.
    class FakeResp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode("utf-8")
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    calls = {"urls": [], "headers": []}
    def fake_urlopen(req, timeout=None):
        calls["urls"].append(req.full_url)
        calls["headers"].append(req.get_header("Authorization"))
        return FakeResp({"user": {"emailAddress": "boss@example.com",
                                   "displayName": "Boss"}})

    with patch.object(mod, "_rclone_run", return_value=rc(code=0)), \
         patch.object(mod, "_rclone_token_for", return_value={"access_token": "TOK123"}), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        mod.GDRIVE_LABELS_FILE.unlink(missing_ok=True)
        email = mod._fetch_gdrive_account_email("gdrive")
    check("a reachable account with a real token gets its email back",
          email == "boss@example.com")
    check("...asks the Drive API's OWN about-user endpoint, not something "
          "invented", calls["urls"] == ["https://www.googleapis.com/drive/v3/about?fields=user"])
    check("...carries the access token as a Bearer header, not in the URL "
          "where it would end up in a log line", calls["headers"] == ["Bearer TOK123"])
    check("...and the result is cached, so the next render is instant",
          json.loads(mod.GDRIVE_LABELS_FILE.read_text(encoding="utf-8"))
              .get("gdrive") == "boss@example.com")

    # 2. the remote can't even be listed right now -- no email lookup at all,
    # and nothing crashes or overwrites a cache that might still be good.
    mod.GDRIVE_LABELS_FILE.write_text(json.dumps({"gdrive": "boss@example.com"}),
                                      encoding="utf-8")
    with patch.object(mod, "_rclone_run", return_value=rc(code=1, stderr="account suspended")):
        result = mod._fetch_gdrive_account_email("gdrive")
    check("an unreachable remote: no lookup attempted, reported as no answer",
          result is None)
    check("...and the existing cache is left alone, not wiped",
          json.loads(mod.GDRIVE_LABELS_FILE.read_text(encoding="utf-8"))
              .get("gdrive") == "boss@example.com")

    # 3. remote reachable, but no token on file (e.g. config hand-edited) --
    # still just "no answer", never an exception surfacing to the caller.
    with patch.object(mod, "_rclone_run", return_value=rc(code=0)), \
         patch.object(mod, "_rclone_token_for", return_value=None):
        check("no token on file: reported as no answer, not a crash",
              mod._fetch_gdrive_account_email("gdrive") is None)

    # 4. Google itself errors (revoked scope, expired token rclone couldn't
    # refresh, a network hiccup) -- swallowed, same as every other best-effort
    # step in this codebase.
    def boom(req, timeout=None):
        raise OSError("connection reset")
    with patch.object(mod, "_rclone_run", return_value=rc(code=0)), \
         patch.object(mod, "_rclone_token_for", return_value={"access_token": "TOK"}), \
         patch("urllib.request.urlopen", side_effect=boom):
        check("the Drive API call itself failing is swallowed, not raised",
              mod._fetch_gdrive_account_email("gdrive") is None)

    # 5. Google answers, but with no user field at all -- reported as no
    # answer rather than caching an empty string that would out-rank the
    # real remote name in gdrive_account_label().
    def empty_about(req, timeout=None):
        return FakeResp({})
    mod.GDRIVE_LABELS_FILE.unlink(missing_ok=True)
    with patch.object(mod, "_rclone_run", return_value=rc(code=0)), \
         patch.object(mod, "_rclone_token_for", return_value={"access_token": "TOK"}), \
         patch("urllib.request.urlopen", side_effect=empty_about):
        result = mod._fetch_gdrive_account_email("gdrive")
    check("a response with no user field: no answer, and nothing cached",
          result is None and not mod.GDRIVE_LABELS_FILE.exists())


test_fetch_email()


# --- wiring: the lookup actually runs where it needs to ---------------------
src = Path(SRC).read_text(encoding="utf-8")

connect_body = src.split("def connect_gdrive_account")[1].split("\ndef ")[0]
check("connect_gdrive_account looks the label up once, right after a fresh "
      "connect verifies -- the token's freshest chance",
      "_fetch_gdrive_account_email(name)" in connect_body)
check("...and a failure there cannot turn a successful connect into a "
      "failed one", "except Exception:\n        logger.warning(\"gdrive: "
      "account-label lookup failed" in connect_body)

gdrive_body = src.split("async def cmd_gdrive(")[1].split("\nasync def ")[0]
check("cmd_gdrive shows the label on the status line and every button, not "
      "the raw remote name", gdrive_body.count("gdrive_account_label(") >= 4)
check("...backfills any account connected before this feature existed, "
      "instead of showing the raw name forever",
      "if a not in cached:" in gdrive_body and
      "loop.run_in_executor(None, _fetch_gdrive_account_email, a)" in gdrive_body)
check("...but callback_data still carries the REAL remote name -- the label "
      "is display-only, and the button handler keys off the rclone remote",
      'callback_data=f"gdrv:use:{a}"' in gdrive_body and
      'callback_data=f"gdrv:rm:{a}"' in gdrive_body)

picker_body = src.split("def _gdrive_picker_text(")[1].split("\ndef ")[0]
check("the folder picker's header shows the label too, not just /gdrive's "
      "own list", "gdrive_account_label(state[\"account\"])" in picker_body)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
