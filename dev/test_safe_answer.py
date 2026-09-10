#!/usr/bin/env python3
"""A flaky link to Telegram must not abort a callback handler.

Real incident on the bscloud agent, 2026-09-06 23:11. The operator was
registering the Bima Kota Proxmox through the chat auto-add flow. At the PIN
keypad, every digit tap calls query.answer() -- a cosmetic ack that stops the
spinner on the tapped button. One call hit a transient httpx.ReadTimeout, it
raised telegram.error.TimedOut, and that aborted cmd_pin_key mid-handler. The
PIN never completed, so the registration never reached the key step -- and the
key was fine the whole time; it already authenticated to that Proxmox.

The reproduction that matters is the last block: cmd_pin_key driven with a
query whose answer() ALWAYS times out, asserting the digit still registers and
the handler does not raise. Everything above it guards the pieces.
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
scratch = Path(tempfile.mkdtemp(prefix="isla_safeanswer_"))
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


# --- the helper swallows exactly the network faults, and nothing else -------
class FakeQuery:
    def __init__(self, boom=None):
        self.boom = boom
        self.calls = 0

    async def answer(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise self.boom


async def helper_cases():
    q = FakeQuery(mod.TimedOut("Timed out"))
    await mod._safe_answer(q)                     # must NOT raise
    check("a TimedOut from answer() is swallowed", q.calls == 1)

    q = FakeQuery(mod.NetworkError("boom"))
    await mod._safe_answer(q)
    check("a NetworkError from answer() is swallowed", True)

    q = FakeQuery(mod.BadRequest("Query is too old"))
    await mod._safe_answer(q, "some text", show_alert=True)
    check("a BadRequest (query too old) is swallowed, args passed through",
          q.calls == 1)

    # A programming error is NOT a network fault and must still surface.
    q = FakeQuery(ValueError("a real bug"))
    raised = False
    try:
        await mod._safe_answer(q)
    except ValueError:
        raised = True
    check("a non-network error is NOT swallowed -- real bugs still surface",
          raised)

    # The happy path still acknowledges.
    q = FakeQuery()
    await mod._safe_answer(q, text="ok")
    check("with a healthy link the ack still goes out", q.calls == 1)


asyncio.run(helper_cases())


# --- THE incident: a PIN digit must register even when the ack times out ----
async def pin_case():
    token = mod._new_pin_session("addserver", {"host": "103.152.36.66"}, 555)

    edited = []

    class TimingOutQuery:
        """answer() always times out -- the bscloud link on the night it broke."""
        def __init__(self, data):
            self.data = data
            self.message = SimpleNamespace(text="Confirm adding a server")
            self.answer_calls = 0

        async def answer(self, *a, **k):
            self.answer_calls += 1
            raise mod.TimedOut("Timed out")

        async def edit_message_text(self, *a, **k):
            edited.append(a[0] if a else k.get("text", ""))

    q = TimingOutQuery(f"pin:{token}:5")
    update = SimpleNamespace(
        callback_query=q,
        effective_chat=SimpleNamespace(id=555, type="private"),
        effective_user=SimpleNamespace(id=111),
        effective_message=q.message,
        message=None,
    )
    ctx = SimpleNamespace(bot=SimpleNamespace())

    with patch.object(mod, "_chat_lang", return_value="id"), \
            patch.object(mod, "_is_owner", return_value=True), \
            patch.object(mod, "_is_trusted_origin", return_value=True):
        crashed = None
        try:
            await mod.cmd_pin_key(update, ctx)
        except Exception as exc:          # noqa: BLE001 -- that is the point
            crashed = exc

    check("cmd_pin_key does not crash when the ack times out (the actual bug)",
          crashed is None)
    check("...answer() really was attempted (and really did time out)",
          q.answer_calls >= 1)
    check("...and the digit was registered despite the timeout",
          mod._pin_sessions.get(token, {}).get("digits") == "5")
    check("...and the keypad was still redrawn for the operator",
          len(edited) >= 1)


asyncio.run(pin_case())


# --- no cosmetic ack is left able to crash a handler ------------------------
src = Path(SRC).read_text(encoding="utf-8")
# The only surviving `await query.answer(` must be inside the helper itself.
stray = src.count("await query.answer(")
check("every awaited answer() goes through _safe_answer -- the only direct one "
      f"left is the helper's own ({stray} direct)", stray == 1)
hstart = src.index("async def _safe_answer")
hend = src.index("\nasync def ", hstart + 10)
check("...and that one lives inside _safe_answer",
      hstart < src.index("await query.answer(") < hend)
check("_safe_answer catches the three Telegram faults and no more",
      "except (TimedOut, NetworkError, BadRequest):" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
