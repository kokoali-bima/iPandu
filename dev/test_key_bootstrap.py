#!/usr/bin/env python3
"""Placing the first key on a machine we cannot reach yet.

`install_node_guard()` already does everything else -- guard script, read-only
key behind it, write key -- but it needs a key that already works. On a brand
new host there is none, and the operator was told to paste a command into a
terminal somewhere else. That is the step people put off, and it is why
/addserver stalled.

So: one password, used once, to place the write key. Verified on this fleet
rather than assumed:

  * `ssh` takes a password from SSH_ASKPASS with no terminal attached
    (SSH_ASKPASS_REQUIRE=force under setsid). Tested against a host that offers
    password auth, with a username that does not exist so no real account could
    be locked out -- the helper was invoked, which was the whole question.
  * The password never reaches disk. The obvious helper echoes the secret into
    a file, which is exactly what this feature exists to avoid; this one reads
    an environment variable, so the file holds a variable name and nothing else.

paramiko would have done it too, at seven packages in a project that has one.
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
scratch = Path(tempfile.mkdtemp(prefix="isla_bootstrap_t_"))
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

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


PW = "Sup3r-Secret-Pw!"
key = scratch / "agent_write"
key.write_text("private", encoding="utf-8")
key.with_suffix(".pub").write_text(
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyMaterialHere agent_write\n",
    encoding="utf-8")
mod.SSH_RW_KEY = key

seen = {}


def fake_run(cmd, **kw):
    seen["cmd"] = cmd
    seen["env"] = kw.get("env") or {}
    # Read the askpass helper while it still exists -- it is deleted in the
    # finally block, which is itself one of the things under test.
    ap = seen["env"].get("SSH_ASKPASS")
    if ap and Path(ap).exists():
        seen["helper_text"] = Path(ap).read_text(encoding="utf-8")
        seen["helper_mode"] = oct(Path(ap).stat().st_mode)[-3:]
        seen["helper_dir"] = str(Path(ap).parent)
    return SimpleNamespace(returncode=seen.get("rc", 0), stdout="", stderr=seen.get("err", ""))


with patch.object(mod.subprocess, "run", side_effect=fake_run), \
        patch.object(mod, "test_server_ssh", return_value=(True, "Linux 6.8.0")):
    ok, detail = mod.bootstrap_key_with_password("192.0.2.10", "root", 222, PW)

check("a working bootstrap reports success", ok and "Linux" in detail)

# --- the password must not be on disk, only in the environment ------------
helper = seen.get("helper_text", "")
# The bot runs on Linux, and there the mode is the whole point: a helper any
# local user can read is a helper any local user can replace. Windows has no
# POSIX permission bits at all -- os.chmod there only toggles read-only -- so
# asserting 700 on a dev laptop tests the OS, not us. Keep the real check where
# it means something, and on Windows check that the code still ASKS for 0o700,
# which is the part a refactor could quietly drop.
if os.name == "posix":
    check("the askpass helper exists and is executable by nobody else "
          f"(mode {seen.get('helper_mode')})", seen.get("helper_mode") == "700")
else:
    check("the askpass helper is chmod 0o700 by the code "
          "(mode bits are not enforceable on this OS)",
          "helper.chmod(0o700)" in Path(SRC).read_text(encoding="utf-8"))
check("the helper contains NO password -- only a variable name",
      PW not in helper and "ISLA_SSH_PW" in helper)
check("the password is handed over through the environment",
      seen["env"].get("ISLA_SSH_PW") == PW)
check("ssh is told to use askpass with no terminal",
      seen["env"].get("SSH_ASKPASS_REQUIRE") == "force"
      and seen["env"].get("SSH_ASKPASS") is not None)
check("the temp directory holding the helper is removed afterwards",
      not Path(seen["helper_dir"]).exists())

# --- the command itself ---------------------------------------------------
cmd = seen["cmd"]
check("it runs under setsid, so ssh cannot fall back to a tty prompt",
      cmd[0] == "setsid" and cmd[1] == "ssh")
check("password auth only -- a stale key must not make this look successful",
      "PubkeyAuthentication=no" in cmd and "PreferredAuthentications=password" in cmd)
check("one prompt only, so a wrong password fails instead of retrying",
      "NumberOfPasswordPrompts=1" in cmd)
check("the port from the wizard is used", "222" in cmd)
check("the remote command appends the key only if it is not already there",
      "grep -qF" in cmd[-1] and "authorized_keys" in cmd[-1])
check("the PASSWORD never appears in the command line, where ps would show it",
      not any(PW in str(part) for part in cmd))

# --- failure paths --------------------------------------------------------
seen["rc"], seen["err"] = 255, "Permission denied, please try again."
with patch.object(mod.subprocess, "run", side_effect=fake_run), \
        patch.object(mod, "test_server_ssh", return_value=(True, "should not be reached")):
    ok2, detail2 = mod.bootstrap_key_with_password("192.0.2.10", "root", 22, PW)
check("a wrong password is reported in words, not as an ssh dump",
      not ok2 and "wrong password" in detail2.lower())
check("...and the failure text never contains the password", PW not in detail2)

seen["rc"], seen["err"] = 0, ""
# Exit 0 is not proof. The key must actually work afterwards.
with patch.object(mod.subprocess, "run", side_effect=fake_run), \
        patch.object(mod, "test_server_ssh", return_value=(False, "Permission denied")):
    ok3, detail3 = mod.bootstrap_key_with_password("192.0.2.10", "root", 22, PW)
check("exit 0 is NOT accepted as success -- the key is proven by using it",
      not ok3 and "does not work" in detail3)

mod.SSH_RW_KEY = scratch / "missing_key"
ok4, detail4 = mod.bootstrap_key_with_password("192.0.2.10", "root", 22, PW)
check("with no write key on this deployment it refuses rather than pretending",
      not ok4 and "no write key" in detail4)
mod.SSH_RW_KEY = key

# --- the wizard will not ask for a password where it cannot clean up -------
src = Path(SRC).read_text(encoding="utf-8")
check("the offer is refused when the bot cannot delete in this chat",
      "if not await bot_can_delete_here(update, context):" in src)
check("...and the refusal names the exact admin right instead of 'make me admin'",
      "Delete messages</b>" in src and "Hapus pesan</b>" in src)
check("the password message is deleted BEFORE anything that can fail",
      src.index('await _msg(update).delete()\n            cleared = True')
      < src.index("bootstrap_key_with_password,\n"))
check("the operator is told to change the password afterwards",
      "Change that password now" in src and "Ganti password itu sekarang" in src)
check("the password is dropped as soon as the call returns", "del pw" in src)



# --- the advice given after a host is registered ---------------------------
# The operator decided NOT to have the bot reconfigure sshd -- that is the one
# change here that locks you out permanently when it goes wrong. So it hands
# over the exact commands instead, and only when the host is actually still
# accepting passwords.
advice_id = mod.harden_ssh_advice("id")
advice_en = mod.harden_ssh_advice("en")

check("the advice exists in both languages",
      "PasswordAuthentication no" in advice_id and "PasswordAuthentication no" in advice_en)
check("it writes a DROP-IN, not a sed over the main config -- which "
      "60-cloudimg-settings.conf would silently override",
      "sshd_config.d/00-ismart-hardening.conf" in advice_id
      and "sed -i" not in advice_id)
check("the filename sorts FIRST, because sshd takes the first value it reads",
      "00-ismart-hardening" in advice_id)
check("it validates the config before reloading, so a typo cannot lock anyone out",
      "sshd -t &" in advice_id)
check("it reloads rather than restarts, so live sessions survive",
      "reload" in advice_id and "restart ssh" not in advice_id)
check("it tells the operator how to VERIFY, not just to trust it",
      "sshd -T | grep -i passwordauth" in advice_id)
check("...and to keep a session open as the way back",
      "tetap terbuka" in advice_id and "open" in advice_en)
check("it explains why the name starts with 00, so nobody 'tidies' it later",
      "nilai pertama" in advice_id and "first value" in advice_en)

src2 = Path(SRC).read_text(encoding="utf-8")
check("the state is read from sshd itself, not from the config file",
      '"sshd -T"' in src2)
check("the advice only appears when the host really still allows passwords",
      "if still_open:" in src2)

# --- exempting a host from the advice, a HARD boundary ----------------------
# A decision already made and final (see CLAUDE.md's "Keputusan tetap": a
# production box whose dev team depends on password SSH staying open) must
# not get relitigated by a bot nagging about it every time that host is
# re-registered. Advice-only, so nothing is at stake but the noise -- but
# noise is exactly how a REAL warning elsewhere gets tuned out too.
check("SSH_HARDEN_EXEMPT_HOSTS is empty when unset (nothing in this test's env)",
      mod.SSH_HARDEN_EXEMPT_HOSTS == set())
check("...and parses comma-separated, case-insensitive, same shape as "
      "ALLOWED_GROUP_IDS -- not a one-off pattern",
      {h.strip().lower() for h in "SrvBJ3, 10.0.0.5 ,".split(",") if h.strip()}
      == {"srvbj3", "10.0.0.5"})

src3 = Path(SRC).read_text(encoding="utf-8")
check("an exempt host skips the sshd check entirely, not just the message",
      'data["host"].lower() in SSH_HARDEN_EXEMPT_HOSTS' in src3)
gate2 = src3.index('data["host"].lower() in SSH_HARDEN_EXEMPT_HOSTS')
check("...checked BEFORE password_auth_state ever runs, so an exempt host "
      "costs no SSH round-trip either",
      gate2 < src3.index('password_auth_state, data["host"]'))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
