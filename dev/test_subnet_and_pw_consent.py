#!/usr/bin/env python3
"""Two fixes from one screenshot: a subnet is not a host, and a password is not
deleted without consent.

"tolong carikan 2 ip dari subnet 10.10.59.0/24" got "10.10.59.0 belum ada di
inventaris -- daftarkan?": the bot read the subnet's network address as a host.
And a create-VM prompt that carried the VM credential had its whole message
deleted the instant a password was seen -- destroying the rest of it, without
asking -- and then the turn was dropped so the task never ran.

So: an IP written as CIDR, a network/broadcast address, or one introduced as a
gateway/DNS is no longer offered for registration; and a password is warned
about with a delete BUTTON, never an automatic deletion, while the task runs.
"""
import asyncio
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
scratch = Path(tempfile.mkdtemp(prefix="isla_subnetpw_"))
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


# --- a subnet, a gateway, a DNS server: never a host to register -----------
with patch.object(mod, "_known_hosts", return_value=set()):
    check("the exact screenshot: 'subnet 10.10.59.0/24' offers no host",
          mod.unregistered_hosts_in(
              "tolong carikan 2 ip dari subnet 10.10.59.0/24 yang kosong") == [])
    check("a CIDR block is a subnet, not a host (10.0.0.0/8)",
          mod.unregistered_hosts_in("range 10.0.0.0/8") == [])
    check("a gateway address is not offered",
          mod.unregistered_hosts_in("gateway: 10.10.59.254") == [])
    check("a DNS address is not offered",
          mod.unregistered_hosts_in("dns 10.10.59.77 nameserver") == [])
    check("a network address (.0) is not offered",
          mod.unregistered_hosts_in("net 10.10.59.0 here") == [])
    check("a broadcast address (.255) is not offered",
          mod.unregistered_hosts_in("bcast 10.10.59.255") == [])
    check("the whole create-VM prompt offers nothing to register",
          mod.unregistered_hosts_in(
              "buat vm, subnet 10.10.59.0/24 gateway 10.10.59.254 dns 10.10.59.77")
          == [])

    # ...but a genuine unknown host is STILL offered -- the guard must not go blind
    check("a real unknown host is still detected",
          mod.unregistered_hosts_in("fix the app on 192.0.2.10") == ["192.0.2.10"])
    check("...even next to a DNS address, only the DNS is dropped",
          mod.unregistered_hosts_in("host 203.0.113.9 uses dns 10.10.59.77")
          == ["203.0.113.9"])


# --- the password warning offers a button, never an auto-delete ------------
src = Path(SRC).read_text(encoding="utf-8")
check("the password path no longer auto-deletes (its old delete log is gone)",
      "could not delete a message containing a credential" not in src)
check("the credential no longer ends the turn -- the task runs",
      "do not hand the credential to a\n" not in src)
check("warn_password_in_chat offers a delete button",
      'callback_data=f"pwdel:{orig_message_id}"' in src)
check("there is a handler for the delete button",
      "async def cmd_pwdelete_button" in src)
check("...and it is registered",
      'pattern="^pwdel:"' in src)
check("the warning says the task still runs, in both languages",
      "The task still runs" in src and "Tugasnya tetap dijalankan" in src)


# --- drive the warning: a button appears, and NOTHING is deleted -----------
async def warn_case():
    replies = []

    class Msg:
        def __init__(self):
            self.message_id = 4242
            self.delete = _forbidden

        async def reply_text(self, *a, **k):
            replies.append((a[0] if a else "", k.get("reply_markup")))

    async def _forbidden(*a, **k):
        raise AssertionError("must NOT delete without consent")

    m = Msg()
    upd = SimpleNamespace(effective_message=m, message=m, callback_query=None,
                          effective_chat=SimpleNamespace(id=1, type="private"))
    ctx = SimpleNamespace(bot=SimpleNamespace())

    async def _yes(*a, **k):
        return True

    with patch.object(mod, "_chat_lang", return_value="id"), \
            patch.object(mod, "bot_can_delete_here", new=_yes):
        await mod.warn_password_in_chat(upd, ctx, m.message_id)

    check("a warning is sent", len(replies) == 1)
    text, markup = replies[0]
    check("...carrying a delete button, not deleting anything", markup is not None)
    check("...that targets the exact message flagged",
          markup is not None
          and "pwdel:4242" in markup.inline_keyboard[0][0].callback_data)



asyncio.run(warn_case())


# --- the button, when tapped, deletes and confirms -------------------------
async def delete_case():
    deleted = []
    edited = []

    class Query:
        data = "pwdel:4242"

        async def answer(self, *a, **k):
            pass

        async def edit_message_text(self, *a, **k):
            edited.append(a[0] if a else "")

    async def delete_message(chat_id, message_id):
        deleted.append((chat_id, message_id))

    upd = SimpleNamespace(callback_query=Query(),
                          effective_chat=SimpleNamespace(id=1, type="private"))
    ctx = SimpleNamespace(bot=SimpleNamespace(delete_message=delete_message))
    with patch.object(mod, "_chat_lang", return_value="id"):
        await mod.cmd_pwdelete_button(upd, ctx)
    check("tapping the button deletes exactly the flagged message",
          deleted == [(1, 4242)])
    check("...and confirms it", len(edited) == 1)


asyncio.run(delete_case())


# --- the suppression has to speak the language the room speaks --------------
# `\W*$` allows nothing word-shaped between the keyword and the address, which
# is right for "gateway 10.17.17.1" and wrong for how half this deployment
# writes: Indonesian attaches the possessive clitic -nya. "dns nya 8.8.8.8"
# offered to register a nameserver. Allowing that one clitic only ever
# suppresses more, never less.
for phrase in ("dns nya 8.8.8.8 ya", "dnsnya 8.8.8.8", "dns-nya 8.8.8.8",
               "gateway-nya 10.17.17.1", "gatewaynya 10.17.17.1"):
    check(f"not a host to register: {phrase!r}",
          mod.unregistered_hosts_in(phrase) == [])

# The other direction, which is the half that would make this a bad change:
# -nya on an unrelated word must not suppress a genuine unknown host.
check("a real unknown host is still detected when -nya appears elsewhere",
      mod.unregistered_hosts_in("server barunya 192.0.2.11") == ["192.0.2.11"])
check("...and with no keyword at all", 
      mod.unregistered_hosts_in("perbaiki aplikasi di 192.0.2.10") == ["192.0.2.10"])

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
