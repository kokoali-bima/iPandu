#!/usr/bin/env python3
"""Post-execution auto-registration -- the output side of v0.2b.91.

v0.2b.91 fixed the input side (a subnet is not a server, a password message is
not deleted unasked) and named what was left: "post-execution auto-registration
still to come." The scenario: the operator had the bot clone two VMs on a
Proxmox cluster entirely through chat. Once the VMs existed and were reachable,
there was still no way into /servers except re-typing name/host/user/port into
the manual wizard for information the model already had.

The model now ends a reply with one line per finished host:

    SERVER: name=<slug> | host=<ip> | user=<user> | port=<port>

extract_server_proposals() strips these and returns them, the same way
SCHEDULE: already works. Each becomes a card -> a hypervisor/VM choice -> a
PIN, the same gate /addserver has always required -- nothing is written until
the PIN checks out. The write itself (_register_server) is shared with the
manual wizard's own last step, lifted out of the old _finish_addserver rather
than duplicated, so there is exactly one place that performs it.
"""
import asyncio
import atexit
import importlib.util
import json
import os
import shutil as _shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_autoreg_"))
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
mod.SERVERS_FILE = scratch / "servers.json"
# Neither exists, so _brief_files() returns [] and append_learned() is a safe
# no-op -- this must never write into the repo's own SOUL.md/GEMINI.md.
mod.SYSTEM_PROMPT_FILE = scratch / "no-such-soul.md"
mod.GEMINI_PROMPT_FILE = scratch / "no-such-gemini.md"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


# ============================================================================
# parse_server_proposal / extract_server_proposals
# ============================================================================
VALID = "name=elastic-frozen-06 | host=10.10.59.53 | user=ubuntu | port=22"
got = mod.parse_server_proposal(VALID)
check("a well-formed SERVER: line parses",
      got == {"name": "elastic-frozen-06", "host": "10.10.59.53",
              "user": "ubuntu", "port": 22})

check("a missing field is rejected",
      mod.parse_server_proposal("name=x | host=10.0.0.1 | user=ubuntu") is None)
check("an uppercase name is rejected (matches the manual wizard's own _NAME_RE)",
      mod.parse_server_proposal("name=Elastic | host=10.0.0.1 | user=ubuntu | port=22") is None)
check("a username starting with a digit is rejected",
      mod.parse_server_proposal("name=x | host=10.0.0.1 | user=9ubuntu | port=22") is None)
check("a non-numeric port is rejected",
      mod.parse_server_proposal("name=x | host=10.0.0.1 | user=ubuntu | port=ssh") is None)
check("port 0 is rejected", mod.parse_server_proposal(
    "name=x | host=10.0.0.1 | user=ubuntu | port=0") is None)
check("port 70000 is rejected (out of range)", mod.parse_server_proposal(
    "name=x | host=10.0.0.1 | user=ubuntu | port=70000") is None)
check("a chunk with no '=' makes the whole line malformed",
      mod.parse_server_proposal("name=x | justwords | user=ubuntu | port=22") is None)

reply = (
    "Done. Both VMs are up.\n\n"
    f"SERVER: {VALID}\n"
    "SERVER: name=elastic-frozen-07 | host=10.10.59.58 | user=ubuntu | port=22\n"
    "SERVER: this one is garbage\n"
)
clean, proposals = mod.extract_server_proposals(reply)
check("both well-formed SERVER: lines are extracted", len(proposals) == 2)
check("...and the malformed third one is dropped, not raised",
      all(p["name"].startswith("elastic-frozen") for p in proposals))
check("the SERVER: lines are stripped from what gets shown",
      "SERVER:" not in clean and "Done. Both VMs are up." in clean)


# ============================================================================
# offer_server_registration: one card per host
# ============================================================================
def upd(chat_id=555, chat_type="private", uid=111):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=uid),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        message=None, callback_query=None,
    )


async def offer_case():
    mod._pending_server_props.clear()
    u = upd()
    proposals = [
        {"name": "elastic-frozen-06", "host": "10.10.59.53", "user": "ubuntu", "port": 22},
        {"name": "elastic-frozen-07", "host": "10.10.59.58", "user": "ubuntu", "port": 22},
    ]
    await mod.offer_server_registration(u, proposals)
    check("one card is sent per proposed host",
          u.effective_message.reply_text.await_count == 2)
    check("both proposals are parked, each under its own token",
          len(mod._pending_server_props) == 2)
    kb = u.effective_message.reply_text.call_args.kwargs["reply_markup"]
    labels = [b.callback_data for row in kb.inline_keyboard for b in row]
    check("the card offers register and skip, both autosrv:-prefixed",
          any(l.startswith("autosrv:reg:") for l in labels)
          and any(l.startswith("autosrv:skip:") for l in labels))


asyncio.run(offer_case())


# ============================================================================
# cmd_autosrv_button: skip / register -> kind -> [flavour] -> PIN
# ============================================================================
class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edits: list[tuple[str, object]] = []

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw.get("reply_markup")))


def btn_update(data, chat_id=555):
    q = FakeQuery(data)
    u = SimpleNamespace(
        callback_query=q,
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_user=SimpleNamespace(id=111),
        effective_message=q,          # request_pin uses effective_message.reply_text;
                                      # patched out below so this is never touched
        message=None,
    )
    return u, q


def seed(token, **over):
    item = {"name": "elastic-frozen-06", "host": "10.10.59.53",
            "user": "ubuntu", "port": 22}
    item.update(over)
    mod._pending_server_props[token] = item
    return item


async def button_cases():
    ctx = SimpleNamespace()
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)):
        # --- skip: pops, confirms, never touches request_pin -----------
        mod._pending_server_props.clear()
        seed("tok1")
        u, q = btn_update("autosrv:skip:tok1")
        with patch.object(mod, "request_pin", new=AsyncMock()) as rp:
            await mod.cmd_autosrv_button(u, ctx)
        check("skip removes the pending proposal", "tok1" not in mod._pending_server_props)
        check("...and never reaches the PIN", rp.await_count == 0)
        check("...and confirms nothing was registered",
              "not registered" in q.edits[-1][0].lower()
              or "tidak didaftarkan" in q.edits[-1][0].lower())

        # --- register: asks kind, does NOT pop yet, no PIN yet ---------
        mod._pending_server_props.clear()
        seed("tok2")
        u, q = btn_update("autosrv:reg:tok2")
        with patch.object(mod, "request_pin", new=AsyncMock()) as rp:
            await mod.cmd_autosrv_button(u, ctx)
        check("tapping Register does not write or pop yet -- kind is still unknown",
              "tok2" in mod._pending_server_props)
        check("...and shows a hypervisor/VM choice", q.edits[-1][1] is not None)
        kb = q.edits[-1][1]
        labels = [b.callback_data for row in kb.inline_keyboard for b in row]
        check("...specifically hypervisor and vm, keyed to the same token",
              f"autosrv:kind:tok2:hypervisor" in labels and f"autosrv:kind:tok2:vm" in labels)
        check("...and still no PIN requested", rp.await_count == 0)

        # --- kind=vm: pops, confirms with PIN, kind=vm, no flavour ------
        mod._pending_server_props.clear()
        seed("tok3")
        u, q = btn_update("autosrv:kind:tok3:vm")
        with patch.object(mod, "request_pin", new=AsyncMock()) as rp:
            await mod.cmd_autosrv_button(u, ctx)
        check("choosing VM pops the pending proposal", "tok3" not in mod._pending_server_props)
        check("...and asks for the PIN exactly once", rp.await_count == 1)
        pin_action = rp.call_args.args[1]
        pin_data = rp.call_args.args[2]["data"]
        check("...gated as auto_addserver -- the same PIN action as /addserver's own",
              pin_action == "auto_addserver")
        check("...carrying kind=vm and no flavour",
              pin_data["kind"] == "vm" and "flavour" not in pin_data)
        check("...and every field the model reported, unchanged",
              pin_data["host"] == "10.10.59.53" and pin_data["user"] == "ubuntu"
              and pin_data["port"] == 22)

        # --- kind=hypervisor: asks flavour, still does not pop ----------
        mod._pending_server_props.clear()
        seed("tok4")
        u, q = btn_update("autosrv:kind:tok4:hypervisor")
        with patch.object(mod, "request_pin", new=AsyncMock()) as rp:
            await mod.cmd_autosrv_button(u, ctx)
        check("choosing hypervisor does not pop yet -- flavour is still unknown",
              "tok4" in mod._pending_server_props)
        kb = q.edits[-1][1]
        labels = [b.callback_data for row in kb.inline_keyboard for b in row]
        check("...and asks which hypervisor", f"autosrv:flavour:tok4:proxmox" in labels)
        check("...with no PIN yet", rp.await_count == 0)

        # --- flavour=proxmox: pops, confirms with PIN, kind+flavour set -
        u, q = btn_update("autosrv:flavour:tok4:proxmox")
        with patch.object(mod, "request_pin", new=AsyncMock()) as rp:
            await mod.cmd_autosrv_button(u, ctx)
        check("choosing the flavour pops the proposal", "tok4" not in mod._pending_server_props)
        pin_data = rp.call_args.args[2]["data"]
        check("...and the PIN payload carries kind=hypervisor, flavour=proxmox",
              pin_data["kind"] == "hypervisor" and pin_data["flavour"] == "proxmox")

        # --- an expired token at any stage: no crash, no PIN ------------
        mod._pending_server_props.clear()
        for data in ("autosrv:reg:ghost", "autosrv:kind:ghost:vm", "autosrv:flavour:ghost:proxmox"):
            u, q = btn_update(data)
            with patch.object(mod, "request_pin", new=AsyncMock()) as rp:
                await mod.cmd_autosrv_button(u, ctx)
            check(f"an expired token at '{data.split(':')[1]}' is handled, not crashed",
                  rp.await_count == 0 and q.edits)

    # --- gating: someone not allowed to authorize gets refused, nothing moves
    mod._pending_server_props.clear()
    seed("tok5")
    u, q = btn_update("autosrv:reg:tok5")
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=False)), \
            patch.object(mod, "request_pin", new=AsyncMock()) as rp:
        await mod.cmd_autosrv_button(u, ctx)
    check("someone not allowed to authorize this cannot advance it",
          "tok5" in mod._pending_server_props and rp.await_count == 0)


asyncio.run(button_cases())


# ============================================================================
# _register_server: the shared write, and the refactor that produced it
# ============================================================================
# A dummy key so _rebuild_ssh_config's default-key fallback needs no real
# keypair on disk.
(scratch / ".ssh").mkdir(exist_ok=True)
mod.SSH_ACTIVE_KEY = scratch / ".ssh" / "active"
mod.SSH_ACTIVE_KEY.write_text("dummy", encoding="utf-8")


async def register_cases():
    q = FakeQuery("")
    u = SimpleNamespace(effective_user=SimpleNamespace(id=111),
                       effective_chat=SimpleNamespace(id=1, type="private"))
    data = {"name": "elastic-frozen-06", "host": "10.10.59.53",
            "user": "ubuntu", "port": 22, "kind": "vm"}
    mod.SERVERS_FILE.write_text("[]", encoding="utf-8")
    with patch.object(mod, "password_auth_state", return_value=False), \
            patch.object(mod, "_chat_lang", return_value="en"):
        await mod._register_server(u, q, dict(data))
    written = json.loads(mod.SERVERS_FILE.read_text(encoding="utf-8"))
    check("_register_server actually writes the host into servers.json",
          len(written) == 1 and written[0]["host"] == "10.10.59.53")
    check("...tagging who added it and when",
          written[0]["added_by"] == 111 and "added_at" in written[0])
    check("...and reports success back through the query",
          "elastic-frozen-06" in q.edits[-1][0])

    # a second registration with the same name replaces, not duplicates --
    # the exact behaviour _finish_addserver already had.
    with patch.object(mod, "password_auth_state", return_value=False), \
            patch.object(mod, "_chat_lang", return_value="en"):
        await mod._register_server(u, q, {**data, "port": 2222})
    written = json.loads(mod.SERVERS_FILE.read_text(encoding="utf-8"))
    check("registering the same name again replaces it, not duplicates it",
          len(written) == 1 and written[0]["port"] == 2222)

    # ssh-config failure must abort the write -- nothing saved, error shown.
    mod.SERVERS_FILE.write_text("[]", encoding="utf-8")
    with patch.object(mod, "_rebuild_ssh_config", side_effect=OSError("disk full")), \
            patch.object(mod, "_chat_lang", return_value="en"):
        await mod._register_server(u, q, dict(data))
    check("a failed ~/.ssh/config rebuild saves nothing",
          json.loads(mod.SERVERS_FILE.read_text(encoding="utf-8")) == [])
    check("...and says so rather than claiming success",
          "saved nothing" in q.edits[-1][0].lower() or "tidak ada yang disimpan" in q.edits[-1][0].lower())


asyncio.run(register_cases())


async def finish_addserver_delegates():
    """_finish_addserver is now a thin wrapper: pop the wizard state, then run
    the shared write. This is the refactor's whole point -- prove it still
    does exactly what it did before, through the new shared function."""
    chat_id = 777
    mod._server_wizard[chat_id] = {
        "step": "authorize",
        "data": {"name": "test-refactor", "host": "10.0.0.9", "user": "root", "port": 22},
    }
    q = FakeQuery("")
    u = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type="private"),
                       effective_user=SimpleNamespace(id=111))
    with patch.object(mod, "_register_server", new=AsyncMock()) as reg:
        await mod._finish_addserver(u, q, discovery="scan output")
    check("_finish_addserver pops the wizard state and delegates the write",
          reg.await_count == 1
          and reg.call_args.args[2]["name"] == "test-refactor"
          and reg.call_args.args[2]["host"] == "10.0.0.9")
    check("...passing the discovery text through unchanged",
          reg.call_args.args[3] == "scan output"
          if len(reg.call_args.args) > 3 else reg.call_args.kwargs.get("discovery") == "scan output")
    check("...and the wizard state is gone either way", chat_id not in mod._server_wizard)

    # No wizard state at all -> nothing happens, no crash.
    with patch.object(mod, "_register_server", new=AsyncMock()) as reg:
        await mod._finish_addserver(u, q)
    check("with no wizard state, _finish_addserver does nothing", reg.await_count == 0)


asyncio.run(finish_addserver_delegates())


async def pin_verified_dispatches():
    """_pin_verified routes 'auto_addserver' straight to _register_server with
    the data the PIN payload carried -- no re-derivation, no re-asking."""
    q = FakeQuery("")
    u = SimpleNamespace(effective_chat=SimpleNamespace(id=1, type="private"),
                       effective_user=SimpleNamespace(id=111))
    data = {"name": "x", "host": "10.0.0.1", "user": "root", "port": 22, "kind": "vm"}
    session = {"action": "auto_addserver", "payload": {"data": data}}
    with patch.object(mod, "_register_server", new=AsyncMock()) as reg:
        await mod._pin_verified(u, SimpleNamespace(), q, session)
    check("_pin_verified action=auto_addserver calls _register_server",
          reg.await_count == 1 and reg.call_args.args[2] == data)


asyncio.run(pin_verified_dispatches())


# ============================================================================
# wiring: static checks that the pieces are actually connected
# ============================================================================
src = Path(SRC).read_text(encoding="utf-8")

check("auto_addserver is trusted at the same level as addserver in groups",
      '"auto_addserver"' in src.split("PIN_ACTIONS_ALLOWED_IN_GROUP = frozenset({")[1].split("})")[0])
check("the autosrv: callback handler is registered",
      'CallbackQueryHandler(cmd_autosrv_button, pattern="^autosrv:")' in src)
check("server proposals are extracted in the same place schedules are",
      "extract_server_proposals(reply_text)" in src)
check("...and offered the same way, gated the same way",
      "offer_server_registration(update, server_proposals)" in src)

check("CAPABILITIES_BRIEF documents the SERVER: tag",
      "SERVER: name=" in mod.CAPABILITIES_BRIEF)
# This fork injects capabilities_brief() rather than the bare constant: the
# function appends whatever THIS deployment actually has wired up, so a box
# with no OPNsense credentials is not told about a tool it cannot use. The
# rule being guarded is unchanged -- both models must receive it -- so match
# either form rather than the spelling one of them happens to use.
check("...and is injected into BOTH models -- agy AND claude -- not just one",
      ("parts.append(CAPABILITIES_BRIEF)" in src
       or "parts.append(capabilities_brief())" in src)
      and ("extra_parts.insert(0, CAPABILITIES_BRIEF)" in src
           or "extra_parts.insert(0, capabilities_brief())" in src))

check("the manual wizard's own user check now shares _USER_RE with the parser",
      'if not _USER_RE.match(text):' in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
