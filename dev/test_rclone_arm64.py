#!/usr/bin/env python3
"""ensure_rclone() fetches rclone for arm64 hosts too, not just amd64.

rclone has shipped an official linux-arm64 static binary all along --
confirmed against the real listing at downloads.rclone.org, not assumed --
but ensure_rclone() refused outright on anything except x86_64/amd64, which
is exactly the architecture a growing share of real VMs (Ampere, Graviton,
a Pi) are NOT.
"""
import atexit
import importlib.util
import os
import shutil as _shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_rclonearm_"))
atexit.register(_shutil.rmtree, str(scratch), ignore_errors=True)
os.environ["HOME"] = str(scratch)
os.environ["USERPROFILE"] = str(scratch)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["la"] = mod
spec.loader.exec_module(mod)
mod.LEDGER_FILE = scratch / "spend.jsonl"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


# --- the URL resolver: both real architectures, and everything else --------
with patch.object(mod.platform, "machine", return_value="x86_64"):
    check("x86_64 resolves to rclone's own amd64 build",
          mod._rclone_download_url() ==
          "https://downloads.rclone.org/rclone-current-linux-amd64.zip")

with patch.object(mod.platform, "machine", return_value="aarch64"):
    check("aarch64 (what Linux itself reports on an arm64 VM) resolves to "
          "rclone's own arm64 build",
          mod._rclone_download_url() ==
          "https://downloads.rclone.org/rclone-current-linux-arm64.zip")

with patch.object(mod.platform, "machine", return_value="arm64"):
    check("the 'arm64' spelling (macOS's own uname -m, seen if this ever runs "
          "in a Linux container reporting the host's arch) resolves the same",
          mod._rclone_download_url() ==
          "https://downloads.rclone.org/rclone-current-linux-arm64.zip")

with patch.object(mod.platform, "machine", return_value="mips"):
    check("an architecture rclone was never asked to support here resolves "
          "to nothing, not a URL that 404s at fetch time",
          mod._rclone_download_url() is None)


# --- ensure_rclone(): arm64 now actually gets a fetch attempt ---------------
with patch.object(mod, "_rclone_path", return_value=None), \
     patch.object(mod.sys, "platform", "linux"), \
     patch.object(mod.platform, "machine", return_value="aarch64"), \
     patch.object(mod.urllib.request, "urlopen",
                  side_effect=OSError("network unreachable in this test")):
    ok, msg = mod.ensure_rclone()
check("an arm64 Linux host is no longer refused outright -- it reaches the "
      "actual download attempt (which this test lets fail on purpose)",
      not ok and msg == "rclone_download_failed")

# an architecture with no known build is still refused UP FRONT, before ever
# touching the network -- not sent into a download that can only 404.
with patch.object(mod, "_rclone_path", return_value=None), \
     patch.object(mod.sys, "platform", "linux"), \
     patch.object(mod.platform, "machine", return_value="mips"), \
     patch.object(mod.urllib.request, "urlopen",
                  side_effect=AssertionError("must not be called")):
    ok, msg = mod.ensure_rclone()
check("an unmapped architecture is refused before any network call, not "
      "after a download that was doomed to 404",
      not ok and msg == "rclone_missing_unsupported")

# non-Linux stays refused regardless of architecture, same as before.
with patch.object(mod, "_rclone_path", return_value=None), \
     patch.object(mod.sys, "platform", "darwin"), \
     patch.object(mod.platform, "machine", return_value="arm64"):
    ok, msg = mod.ensure_rclone()
check("a non-Linux host (an arm64 Mac included) is still refused -- this "
      "only ever fetches the Linux static binary",
      not ok and msg == "rclone_missing_unsupported")


# --- the user-facing message no longer claims amd64 is the only option -----
src = Path(SRC).read_text(encoding="utf-8")
check("the unsupported-architecture message mentions arm64 too, in both "
      "languages -- it stopped being true the moment this shipped",
      "amd64/arm64" in src)
check("...and doesn't still claim amd64 is the ONLY build this can fetch",
      "I only know how to fetch the linux-amd64 build" not in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
