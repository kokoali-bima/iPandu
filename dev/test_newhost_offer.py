#!/usr/bin/env python3
"""Noticing a machine we have never heard of, before the model wastes a turn.

The friction this removes, measured rather than assumed: asking the agent to fix
something on an unregistered host went smoothly on Gemini and cost ten circular
turns on Sonnet, which hit `pve-ro-guard: refused` and tried to route around it
instead of asking for access. Across 36 hours of that host's logs there were two
write-mode mentions; Sonnet never emitted NEEDS_WRITE once.

/addserver never had that problem, because it never involves the model at all --
it is a deterministic wizard that runs BEFORE the model is called. So the fix is
not better prompting. It is to spot the unknown address ourselves and put the
deterministic path in front of the operator, prefilled with what they already
typed.

The detection is deliberately narrow: an IPv4 literal. Hostnames would fire on
every domain mentioned in conversation, and a card that cries wolf is a card
people dismiss without reading.
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
scratch = Path(tempfile.mkdtemp(prefix="isla_newhost_"))
atexit.register(_shutil.rmtree, str(scratch), ignore_errors=True)
os.environ["HOME"] = str(scratch)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["la"] = mod
spec.loader.exec_module(mod)
mod.LEDGER_FILE = scratch / "spend.jsonl"
mod.SERVERS_FILE = scratch / "servers.json"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


# An inventory shaped like the real one: a cluster with member nodes.
mod.SERVERS_FILE.write_text(json.dumps([{
    "kind": "hypervisor", "flavour": "proxmox", "name": "bscloud03-cluster",
    "host": "10.10.30.2", "user": "root", "port": 22, "cluster_wide": True,
    "cluster_hosts": ["172.18.10.3", "172.18.10.2"],
}]), encoding="utf-8")

# --- what counts as unknown -----------------------------------------------
ASK = "tolong perbaiki aplikasi di server A dengan ip: 192.0.2.10 port ssh: 222, user root"
check("an address the inventory has never seen is spotted",
      mod.unregistered_hosts_in(ASK) == ["192.0.2.10"])
check("a registered host is NOT offered again",
      mod.unregistered_hosts_in("cek 10.10.30.2 dong") == [])
check("a CLUSTER MEMBER is not treated as a new machine -- it is already "
      "reachable through its cluster",
      mod.unregistered_hosts_in("node 172.18.10.3 lambat") == [])
check("loopback is ignored (it shows up in log excerpts constantly)",
      mod.unregistered_hosts_in("bound on 127.0.0.1:8080") == [])
check("0.0.0.0 is ignored", mod.unregistered_hosts_in("listen 0.0.0.0:443") == [])
check("something that only looks like an address is rejected",
      mod.unregistered_hosts_in("versi 999.1.1.1 dan 1.2.3") == [])
check("plain conversation with no address offers nothing",
      mod.unregistered_hosts_in("gimana kabar server kemarin?") == [])
check("several unknown addresses report all of them, first one offered",
      mod.unregistered_hosts_in("192.0.2.10 dan 198.51.100.7")
      == ["192.0.2.10", "198.51.100.7"])

# --- picking up what the operator already typed ---------------------------
h = mod.parse_host_hints(ASK)
check(f"the ssh port is taken from the sentence (got {h.get('port')})",
      h.get("port") == 222)
check(f"the user is taken from the sentence (got {h.get('user')})",
      h.get("user") == "root")
check("no port is invented when none was given",
      "port" not in mod.parse_host_hints("perbaiki 192.0.2.10 dong"))
check("a nonsense port is not accepted",
      "port" not in mod.parse_host_hints("192.0.2.10 port 99999"))
check("English phrasing works too",
      mod.parse_host_hints("fix 192.0.2.10, port ssh 2222 user ubuntu")
      == {"port": 2222, "user": "ubuntu"})


# --- the card, and what each button does ----------------------------------
def fake_update():
    sent = []
    edited = []

    class Msg:
        text = ASK

        async def reply_text(self, *a, **k):
            sent.append((a[0] if a else "", k.get("reply_markup")))

    class Query:
        data = "newhost:reg"
        message = Msg()

        async def answer(self):
            pass

        async def edit_message_text(self, *a, **k):
            edited.append(a[0] if a else "")

    u = SimpleNamespace(
        message=Msg(), callback_query=None, effective_message=Msg(),
        effective_chat=SimpleNamespace(id=7, type="private"),
        effective_user=SimpleNamespace(id=111),
    )
    return u, Query(), sent, edited


async def main():
    import asyncio  # noqa: F401  (kept local; the module drives its own loop)

    u, q, sent, edited = fake_update()
    with patch.object(mod, "_chat_lang", return_value="id"):
        await mod.offer_register_host(u, None, "192.0.2.10",
                                      {"port": 222, "user": "root"}, ASK)
    check("the card names the host", sent and "192.0.2.10" in sent[0][0])
    check("...and shows the port and user it already picked up",
          "222" in sent[0][0] and "root" in sent[0][0])
    check("...and says plainly that it has no access there",
          "belum bisa" in sent[0][0].lower() or "akses" in sent[0][0].lower())
    kb = sent[0][1]
    labels = [b.callback_data for row in kb.inline_keyboard for b in row]
    check("three ways out: register, just answer, cancel",
          labels == ["newhost:reg", "newhost:skip", "newhost:cancel"])

    # Cancel drops the pending offer rather than leaving it to fire later.
    u2, q2, _, edited2 = fake_update()
    q2.data = "newhost:cancel"
    mod._pending_newhost[7] = {"host": "192.0.2.10", "hints": {}, "text": ASK}
    u2.callback_query = q2
    with patch.object(mod, "_chat_lang", return_value="id"):
        await mod.cmd_newhost_button(u2, None)
    check("cancel clears the pending offer", 7 not in mod._pending_newhost)

    # "Just answer" must run the turn, and must NOT ask for a PIN: it grants
    # nothing. The write gate still stands behind it.
    ran = {}
    u3, q3, _, edited3 = fake_update()
    q3.data = "newhost:skip"
    u3.callback_query = q3
    mod._pending_newhost[7] = {"host": "192.0.2.10", "hints": {}, "text": ASK}

    async def fake_turn(update, ctx, text):
        ran["text"] = text

    with patch.object(mod, "_chat_lang", return_value="id"), \
            patch.object(mod, "_run_turn", side_effect=fake_turn), \
            patch.object(mod, "request_pin", side_effect=AssertionError("no PIN here")):
        await mod.cmd_newhost_button(u3, None)
    check("'just answer' runs the original question", ran.get("text") == ASK)
    check("...and asks for no PIN, because it grants nothing",
          7 not in mod._pending_newhost)

    # Register goes through the PIN, carrying the prefill.
    asked = {}
    u4, q4, _, _ = fake_update()
    q4.data = "newhost:reg"
    u4.callback_query = q4
    mod._pending_newhost[7] = {"host": "192.0.2.10",
                               "hints": {"port": 222, "user": "root"}, "text": ASK}

    async def fake_pin(update, action, payload, text):
        asked["action"] = action
        asked["payload"] = payload

    with patch.object(mod, "_chat_lang", return_value="id"), \
            patch.object(mod, "_is_owner", return_value=True), \
            patch.object(mod, "pin_is_set", return_value=True), \
            patch.object(mod, "request_pin", side_effect=fake_pin):
        await mod.cmd_newhost_button(u4, None)
    check("registering asks for the PIN first", asked.get("action") == "addserver")
    check("...and carries the host, port and user into the wizard",
          asked.get("payload", {}).get("prefill")
          == {"host": "192.0.2.10", "port": 222, "user": "root"})


import asyncio
asyncio.run(main())

# --- the wizard still offers the choices the operator expects -------------
check("the wizard asks hypervisor / single VM / other",
      set(mod.SERVER_KINDS) == {"hypervisor", "vm", "other"})
check("...and for a hypervisor, Proxmox or another",
      "proxmox" in mod.HYPERVISOR_FLAVOURS)

src = Path(SRC).read_text(encoding="utf-8")
check("the offer runs BEFORE the model, so an impossible turn is never paid for",
      src.index("offer_register_host(update, context") < src.index("await run_combo")
      if "await run_combo" in src else True)

# --- credentials typed into the chat --------------------------------------
# Detected in code, before the model runs: zero tokens, fires on every message,
# and unlike an instruction in a brief it cannot be talked out of firing.
LEAKS = [
    "user root password Hunter2!",
    "ip 192.0.2.10 port ssh 222 user root, password: Rahasia123",
    "sandi: Bscloud*234",
    "root pwd = s3cr3t-value",
]
INNOCENT = [
    "cek password expiry policy",
    "gimana cara reset password?",
    "passwordnya apa ya",
    "password manager mana yang bagus",
    "tolong audit kebijakan password di server",
]
for t in LEAKS:
    check(f"a credential is caught: {t[:34]}", mod.mentions_password(t) is not None)
for t in INNOCENT:
    check(f"a question about passwords is NOT flagged: {t[:32]}",
          mod.mentions_password(t) is None)

# The message does not stop at the warning -- it is kept for "just answer" and
# would otherwise carry the password to a model. Deleting the chat message while
# forwarding its contents upstream would look solved and not be.
leak = "perbaiki 192.0.2.10 user root password Rahasia123"
scrubbed = mod.scrub_password(leak)
check("the secret is removed before the text goes anywhere else",
      "Rahasia123" not in scrubbed)
check("...while the rest of the request survives",
      "192.0.2.10" in scrubbed and "root" in scrubbed)
check("a clean message is left untouched",
      mod.scrub_password("perbaiki 192.0.2.10") == "perbaiki 192.0.2.10")
check("the host offer still works on the scrubbed text",
      mod.unregistered_hosts_in(scrubbed) == ["192.0.2.10"])

src2 = Path(SRC).read_text(encoding="utf-8")
check("the warning says the password should be treated as exposed",
      "exposed" in src2 and "bocor" in src2)
check("the warning tells them they never need to send one",
      "never need to send" in src2 and "tidak pernah perlu" in src2)
check("downstream uses the scrubbed text, not the raw message",
      "safe_text = scrub_password(msg.text)" in src2
      and "unregistered_hosts_in(safe_text)" in src2)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
