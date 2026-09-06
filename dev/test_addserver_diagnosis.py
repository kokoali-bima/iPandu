#!/usr/bin/env python3
"""Tests for what /addserver says when the connection test fails.

This encodes a real round of wasted effort, 2026-09-06. /addserver against a
Proxmox node at 10.10.95.3 returned:

    ssh: connect to host 10.10.95.3 port 22: Network is unreachable

    Biasanya public key belum terpasang, atau user/port-nya salah.

The advice was wrong in the most expensive way available: "Network is
unreachable" is a ROUTING failure, so the key was never even offered. The
operator reinstalled the key, failed again, and asked whether the bot's key
differed from the one they had been given. It did not. Nothing about a key was
ever involved -- the host sat behind a VPN whose subnet was not routed.

Two things are tested here, and both are about not sending someone down the
wrong path:

  1. The message names the layer that actually failed. ssh already says which
     one in its own error text; guessing over the top of it is a choice.
  2. When keys ARE plausibly the problem, BOTH are shown. The connection test
     presents agent_keypair() -- the READ-ONLY key whenever write mode is set
     up -- while install_node_guard() needs a key that can already WRITE. The
     bot used to name neither, so the operator had to go find them on the box,
     and authorising only one leaves /addserver stuck at the other step.
"""
import atexit
import shutil as _shutil
import importlib.util, os, sys, tempfile
from pathlib import Path

SRC = sys.argv[1]
sys.path.insert(0, str(Path(SRC).resolve().parent / "tools"))
HOME = tempfile.mkdtemp(prefix="isla_diag_")
atexit.register(_shutil.rmtree, str(HOME), ignore_errors=True)
os.environ["HOME"] = HOME
# Path.home() reads USERPROFILE on Windows; setting only HOME points the whole
# suite at the developer's real home and generates keys into their own ~/.ssh.
os.environ["USERPROFILE"] = HOME
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["la"] = mod
spec.loader.exec_module(mod)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


for fn in ("ssh_failure_hint", "bootstrap_key_block"):
    check(f"{fn}() exists in this source", callable(getattr(mod, fn, None)))
if not all(ok for _, ok in results):
    print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
    print("FAILED: the diagnosis helpers are absent from this source -- every "
          "check below needs them")
    sys.exit(1)

# --- 1. routing is not a key problem, and must not be described as one -----
THE_ERROR = "ssh: connect to host 10.10.95.3 port 22: Network is unreachable"
en, id_ = mod.ssh_failure_hint(THE_ERROR)
check("a 'Network is unreachable' failure is diagnosed at all", bool(en) and bool(id_))
check("...named as routing (THE 10.10.95.3 round-trip)",
      "routing" in en.lower() and "routing" in id_.lower())
check("...and says the key was never offered, so nobody reinstalls one",
      "never" in en.lower() and ("belum sempat" in id_.lower() or "bahkan" in id_.lower()))
check("...and mentions the VPN case, which is what it actually was",
      "vpn" in en.lower() and "vpn" in id_.lower())
check("'No route to host' is diagnosed the same way",
      "routing" in mod.ssh_failure_hint("ssh: connect to host x: No route to host")[0].lower())

# --- 2. the other layers are distinguished from each other -----------------
for text, needle_en, why in (
    ("ssh: connect to host x port 22: Connection timed out", "firewall",
     "a timeout is a filtered packet or a wrong port"),
    ("ssh: connect to host x port 22: Connection refused", "listening",
     "a refusal means nothing is on that port"),
    ("ssh: Could not resolve hostname nope", "resolve",
     "a name that does not resolve is not a key problem either"),
    ("Host key verification failed.", "identity",
     "a changed host key is its own thing"),
):
    hint = mod.ssh_failure_hint(text)[0].lower()
    check(f"{why}", needle_en in hint)
check("a timeout is explicitly NOT blamed on the key, since a wrong key fails fast",
      "not a key problem" in mod.ssh_failure_hint("Connection timed out")[0].lower())

# --- 3. auth failures fall through to the key advice -----------------------
# The empty hint is the signal for "keys are worth showing"; the caller supplies
# the wording. Anything unrecognised lands here too, which is the safe default.
check("a publickey refusal produces NO routing-style hint, so the caller shows keys",
      mod.ssh_failure_hint("Permission denied (publickey).") == ("", ""))
check("...and so does an error nobody has classified yet",
      mod.ssh_failure_hint("some brand new ssh error") == ("", ""))
check("an empty detail does not crash the classifier",
      mod.ssh_failure_hint("") == ("", "") and mod.ssh_failure_hint(None) == ("", ""))

# --- 4. BOTH keys are offered, which is the operator's actual complaint ----
if mod.ensure_write_mode_keys():
    ro_pub = mod.agent_keypair()[1].read_text().strip()
    rw_pub = mod.SSH_RW_KEY.with_suffix(".pub").read_text().strip()
    block = mod.bootstrap_key_block("id")
    check("the key block is produced once the keypair exists", bool(block))
    check("it contains the key the CONNECTION TEST presents "
          "(agent_keypair(), i.e. the read-only one)",
          ro_pub.split()[1] in block)
    check("it ALSO contains the write key, which install_node_guard() needs "
          "before it can install the guarded one",
          rw_pub.split()[1] in block)
    check("...and says why both are needed, rather than listing two blobs",
          "guard" in block.lower())
    check("it tells the operator to paste them unrestricted, since the bot "
          "replaces the first with a guarded version itself",
          "apa adanya" in block or "as-is" in block)
    check("the English block carries both keys too",
          ro_pub.split()[1] in mod.bootstrap_key_block("en")
          and rw_pub.split()[1] in mod.bootstrap_key_block("en"))
    # The two keys are genuinely different -- the operator asked whether the
    # key the bot uses differs from the one they were handed. It does.
    check("the two keys really are different, which is why one is not enough",
          ro_pub.split()[1] != rw_pub.split()[1])
else:
    print("SKIP - ssh-keygen unavailable, key-block cases skipped")

# --- 5. the message the operator sees is assembled the way it is described --
src = Path(SRC).read_text(encoding="utf-8")
check("the /addserver failure path calls the classifier rather than always "
      "blaming the key",
      "ssh_failure_hint(detail)" in src)
check("...and only appends the keys when the classifier had nothing to say",
      "bootstrap_key_block(\"id\")" in src and "bootstrap_key_block(\"en\")" in src)
check("the old always-blame-the-key wording is gone",
      "Usually the public key isn't in place yet" not in src
      and "Biasanya public key belum terpasang" not in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
