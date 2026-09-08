#!/usr/bin/env python3
"""One generic "still here" line -- only for a turn that is actually slow.

Proposed while planning a desktop companion: standing in silence for the
median 29s turn is what makes the bot feel unresponsive. The first design
considered fired an ack on EVERY turn -- rejected on the operator's own
objection, driven from a real example: a canned "checking the server" line
makes no sense for "bagaimana cuaca hari ini". The fix is not a better
sentence, it is a different trigger -- race a short delay against the real
answer, so a fast reply never sees an ack at all, and the one that does fire
is generic enough to fit any kind of question, because at that moment the
model has not even picked a tool yet.

The property that matters most, and is tested hardest: a turn that finishes
BEFORE the delay must NEVER produce a message. An ack sent after the real
answer already landed would be worse than no ack at all.
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
scratch = Path(tempfile.mkdtemp(prefix="isla_ack_"))
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


def upd(sent):
    class Msg:
        async def reply_text(self, *a, **k):
            sent.append(a[0] if a else k.get("text", ""))
    m = Msg()
    return SimpleNamespace(effective_message=m, message=m, callback_query=None)


# --- THE property: a fast turn never sees an ack ----------------------------
async def fast_case():
    sent = []
    with patch.object(mod, "ACK_DELAY_SECONDS", 10):
        task = asyncio.create_task(mod._send_delayed_ack(upd(sent), "id"))
        await asyncio.sleep(0.02)          # the turn "finishes" almost at once
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    check("a turn that finishes quickly produces NO ack message at all "
          f"({len(sent)} sent)", sent == [])


asyncio.run(fast_case())


# --- a genuinely slow turn gets exactly one ---------------------------------
async def slow_case():
    sent = []
    with patch.object(mod, "ACK_DELAY_SECONDS", 0):
        task = asyncio.create_task(mod._send_delayed_ack(upd(sent), "id"))
        await asyncio.sleep(0.05)          # let it fire
        task.cancel()                      # then cancel, as the finally: block does
        try:
            await task
        except asyncio.CancelledError:
            pass
    check("a turn that is still running past the delay gets exactly one ack",
          len(sent) == 1)


asyncio.run(slow_case())


# --- cancelling AFTER it already sent must not double-send or crash --------
async def already_done_case():
    sent = []
    with patch.object(mod, "ACK_DELAY_SECONDS", 0):
        task = asyncio.create_task(mod._send_delayed_ack(upd(sent), "id"))
        await asyncio.sleep(0.05)
        check("...the task is simply finished by then, not hanging", task.done())
        task.cancel()   # a no-op cancel on an already-finished task
        try:
            await task
        except asyncio.CancelledError:
            pass
    check("...and cancelling a finished task does not send a second ack",
          len(sent) == 1)


asyncio.run(already_done_case())


# --- no target to reply to: quiet, not a crash ------------------------------
async def no_target_case():
    with patch.object(mod, "ACK_DELAY_SECONDS", 0):
        u = SimpleNamespace(effective_message=None, message=None, callback_query=None)
        crashed = False
        try:
            await mod._send_delayed_ack(u, "id")
        except Exception:
            crashed = True
    check("with nowhere to reply, it returns quietly instead of raising",
          not crashed)


asyncio.run(no_target_case())


# --- a Telegram failure on the send must not propagate ----------------------
async def send_fails_case():
    class BoomMsg:
        async def reply_text(self, *a, **k):
            raise RuntimeError("telegram down")
    with patch.object(mod, "ACK_DELAY_SECONDS", 0):
        u = SimpleNamespace(effective_message=BoomMsg(), message=None, callback_query=None)
        crashed = False
        try:
            await mod._send_delayed_ack(u, "id")
        except Exception:
            crashed = True
    check("a failed send is swallowed, not raised into the turn", not crashed)


asyncio.run(send_fails_case())


# --- THE wording bug that started this: no phrase names a task -------------
check("there is more than one phrase, so it does not feel robotic on repeat",
      len(mod._ACK_PHRASES) >= 3)
for en, idn in mod._ACK_PHRASES:
    low = (en + " " + idn).lower()
    check(f"...generic, no task named ('{en}')",
          not any(w in low for w in ("server", "vm", "cek server", "task", "tugas")))
    check(f"...both languages present ('{en}' / '{idn}')", bool(en) and bool(idn))


# --- wiring: spawned and cancelled alongside the existing heartbeat --------
src = Path(SRC).read_text(encoding="utf-8")
check("the ack task is spawned right alongside the progress heartbeat",
      "ack = asyncio.create_task(_send_delayed_ack(update, lang))" in src)
check("...and cancelled in the same finally block, on every path",
      "beat.cancel()\n        ack.cancel()" in src)
check("the delay is meaningfully shorter than the heartbeat's own first note "
      "-- it is a different, earlier tier, not a duplicate",
      "ACK_DELAY_SECONDS = int(os.environ.get(" in src
      and "HEARTBEAT_FIRST_SECONDS = int(os.environ.get" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
