#!/usr/bin/env python3
"""One acknowledgment, sent only if a turn is actually slow -- flavored to fit.

Proposed while planning a desktop companion: standing in silence for the
median 29s turn is what makes the bot feel unresponsive. The first design
fired a fixed line on EVERY turn -- rejected on a sharp objection: "checking
the server" makes no sense as a reply to "bagaimana cuaca hari ini". The fix
was a different trigger, not a better sentence: race a short delay against
the real answer, so a fast reply is never touched at all.

Then a second ask: more than one flavor, closer to what was actually asked.
Real understanding needs a model call, which would undo the whole point
(instant, free, no added latency) -- so this stays a keyword match against the
raw text, picking one of three pools instead of always the same one. The
property that matters most, tested hardest: a turn that finishes BEFORE the
delay must NEVER produce a message, whatever pool it would have used.
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


# --- THE property: a fast turn never sees an ack, whatever it would say ----
async def fast_case():
    sent = []
    with patch.object(mod, "ACK_DELAY_SECONDS", 10):
        task = asyncio.create_task(
            mod._send_delayed_ack(upd(sent), "id", "restart server pm5"))
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
        task = asyncio.create_task(mod._send_delayed_ack(upd(sent), "id", "halo"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    check("a turn that is still running past the delay gets exactly one ack",
          len(sent) == 1)


asyncio.run(slow_case())


# --- no target / send failure: quiet, never crashes the turn ---------------
async def edge_cases():
    with patch.object(mod, "ACK_DELAY_SECONDS", 0):
        u = SimpleNamespace(effective_message=None, message=None, callback_query=None)
        try:
            await mod._send_delayed_ack(u, "id", "test")
            ok1 = True
        except Exception:
            ok1 = False
    check("with nowhere to reply, it returns quietly instead of raising", ok1)

    class BoomMsg:
        async def reply_text(self, *a, **k):
            raise RuntimeError("telegram down")
    with patch.object(mod, "ACK_DELAY_SECONDS", 0):
        u = SimpleNamespace(effective_message=BoomMsg(), message=None, callback_query=None)
        try:
            await mod._send_delayed_ack(u, "id", "test")
            ok2 = True
        except Exception:
            ok2 = False
    check("a failed send is swallowed, not raised into the turn", ok2)


asyncio.run(edge_cases())


# --- the categorizer: the exact case that started this must not be GENERIC -
check("the weather question that started this now lands in the QUESTION "
      "pool, not the flat generic one",
      mod._ack_pool_for("bagaimana cuaca hari ini") is mod._ACK_PHRASES_QUESTION)
check("an action word routes to the ACTION pool",
      mod._ack_pool_for("restart server pm5") is mod._ACK_PHRASES_ACTION)
check("an instruction dressed as a question is still an instruction "
      "(action wins over question)",
      mod._ack_pool_for("bisa restart servernya?") is mod._ACK_PHRASES_ACTION)
check("a plain question word routes to QUESTION",
      mod._ack_pool_for("apa kabar") is mod._ACK_PHRASES_QUESTION)
check("an infra noun alone (no verb) still reads as action-shaped",
      mod._ack_pool_for("tolong cek status vm 174") is mod._ACK_PHRASES_ACTION)
check("a bare greeting, matching neither, falls back to GENERIC",
      mod._ack_pool_for("halo") is mod._ACK_PHRASES_GENERIC)
check("an empty/None message never crashes the matcher",
      mod._ack_pool_for("") is mod._ACK_PHRASES_GENERIC
      and mod._ack_pool_for(None) is mod._ACK_PHRASES_GENERIC)


# --- no pool names a SPECIFIC task, and every phrase is bilingual ----------
all_pools = (mod._ACK_PHRASES_ACTION, mod._ACK_PHRASES_QUESTION,
            mod._ACK_PHRASES_GENERIC)
check("there are three distinct pools, not one flat list",
      len({id(p) for p in all_pools}) == 3)
for pool_name, pool in zip(("ACTION", "QUESTION", "GENERIC"), all_pools):
    check(f"{pool_name} pool has real variety ({len(pool)} phrases)", len(pool) >= 3)
    for en, idn in pool:
        low = (en + " " + idn).lower()
        check(f"...{pool_name} phrase names no specific host/vm/task ('{en}')",
              not any(w in low for w in ("pm5", "vm 1", "server pm", "node "))
              and bool(en) and bool(idn))


# --- wiring: text now flows into the picker -------------------------------
src = Path(SRC).read_text(encoding="utf-8")
check("_send_delayed_ack takes the message text as a parameter",
      "async def _send_delayed_ack(update: Update, lang: str, text: str)" in src)
check("...and the call site passes it through",
      "_send_delayed_ack(update, lang, text)" in src)
check("the picker is a plain keyword match, not a model call -- no CLI/agy/"
      "claude invocation anywhere near it",
      "AGY_BIN" not in src.split("def _ack_pool_for")[1].split("\ndef ")[0]
      and "CLAUDE_BIN" not in src.split("def _ack_pool_for")[1].split("\ndef ")[0])

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
