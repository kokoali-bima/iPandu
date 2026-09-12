#!/usr/bin/env python3
"""A double-tapped button must not crash the handler behind it.

Real incident, 2026-09-08 12:05:54, on the bscloud agent. cmd_update_button
edited a callback's message to "Updating -- confirm with your PIN.", and a
second, near-simultaneous invocation of the same handler (a double-tap, or
Telegram redelivering the callback) tried to edit it to the exact same text
again. Telegram refuses an edit whose content is byte-identical to what is
already showing -- BadRequest, "Message is not modified" -- and that raised,
unhandled, out of process_update, one minute before an unrelated /update
restarted the process.

The reproduction that matters is the last one: cmd_update_button driven twice
in a row with the same tap, exactly as it happened, asserting the second call
does not raise.
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
from unittest.mock import AsyncMock, patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_safeedit_"))
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


# --- the helper itself: swallows exactly "not modified", nothing else ------
class FakeQuery:
    def __init__(self, boom=None):
        self.boom = boom
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    async def edit_message_text(self, text, **kw):
        self.calls.append(text)
        self.kwargs.append(kw)
        if self.boom:
            raise self.boom


async def helper_cases():
    q = FakeQuery(mod.BadRequest("Message is not modified: specified new message "
                                 "content and reply markup are exactly the same"))
    await mod._safe_edit(q, "same text")            # must NOT raise
    check("'Message is not modified' is swallowed", q.calls == ["same text"])

    q = FakeQuery(mod.BadRequest("Message to edit not found"))
    raised = False
    try:
        await mod._safe_edit(q, "anything")
    except mod.BadRequest:
        raised = True
    check("a DIFFERENT BadRequest -- the message genuinely gone -- still raises",
          raised)

    q = FakeQuery(ValueError("a real bug"))
    raised = False
    try:
        await mod._safe_edit(q, "x")
    except ValueError:
        raised = True
    check("a non-Telegram error is never swallowed", raised)

    q = FakeQuery()
    await mod._safe_edit(q, "ok", parse_mode="HTML")
    check("kwargs (parse_mode, reply_markup, ...) still pass through unchanged",
          q.calls == ["ok"])

    # 2026-09-12: a PIN keypad (and every other button-driven "done" message
    # -- /update, the Drive picker, MCP registration) left its own keyboard
    # attached forever, because reply_markup=None is dropped by Bot._post()
    # before the request goes out ("Telegram doesn't handle them well") --
    # identical, on the wire, to never passing it at all. The fix is not
    # "pass None", it is a real empty keyboard, which IS a value Telegram
    # acts on.
    q = FakeQuery()
    await mod._safe_edit(q, "done")
    markup = q.kwargs[0].get("reply_markup")
    check("omitting reply_markup clears the keyboard (a real empty "
          "InlineKeyboardMarkup, not None -- None is silently dropped and "
          "changes nothing)",
          isinstance(markup, mod.InlineKeyboardMarkup) and markup.inline_keyboard == ())

    q = FakeQuery()
    kb = mod.InlineKeyboardMarkup([[mod.InlineKeyboardButton("x", callback_data="x")]])
    await mod._safe_edit(q, "still choosing", reply_markup=kb)
    check("an explicit reply_markup is never overridden by that default",
          q.kwargs[0].get("reply_markup") is kb)


asyncio.run(helper_cases())


# --- THE incident: cmd_update_button double-tapped -------------------------
class DoubleTapQuery:
    """edit_message_text raises 'not modified' on the SECOND identical call --
    exactly what Telegram itself does, and exactly the shape of a real
    double-tap or a redelivered callback."""
    def __init__(self, data):
        self.data = data
        self.seen: dict[str, int] = {}
        self.calls = 0

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, **kw):
        self.calls += 1
        self.seen[text] = self.seen.get(text, 0) + 1
        if self.seen[text] > 1:
            raise mod.BadRequest(
                "Message is not modified: specified new message content and "
                "reply markup are exactly the same as a current content and "
                "reply markup of the message")


async def double_tap_case():
    q = DoubleTapQuery("upd:yes")
    u = SimpleNamespace(
        callback_query=q,
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(id=111),
        effective_message=q,
        message=None,
    )
    ctx = SimpleNamespace()
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)), \
            patch.object(mod, "request_pin", new=AsyncMock()) as rp:
        crashed = None
        try:
            await mod.cmd_update_button(u, ctx)   # the first tap
            await mod.cmd_update_button(u, ctx)   # the double-tap -- THE bug
        except Exception as exc:                  # noqa: BLE001 -- that's the point
            crashed = exc
    check("cmd_update_button survives being invoked twice in a row (the "
          "actual 2026-09-08 crash)", crashed is None)
    check("...both edits were genuinely attempted", q.calls == 2)
    check("...and the PIN was still requested twice (each tap is a real request)",
          rp.await_count == 2)


asyncio.run(double_tap_case())


# --- wiring: every button-facing edit goes through the guard ----------------
src = Path(SRC).read_text(encoding="utf-8")
stray = src.count("query.edit_message_text(")
check("every callback edit goes through _safe_edit -- the only direct call "
      f"left is the helper's own ({stray} direct)", stray == 1)
hstart = src.index("async def _safe_edit")
hend = src.index("\nasync def ", hstart + 10)
check("...and that one lives inside _safe_edit",
      hstart < src.index("query.edit_message_text(") < hend)
check("_safe_edit catches BadRequest and inspects it, rather than swallowing "
      "every BadRequest blindly",
      '"not modified" not in str(exc).lower()' in src)
check("the heartbeat's own edit (a different shape -- a timer loop, not a "
      "button tap) is left as-is, not routed through this",
      "context.bot.edit_message_text" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
