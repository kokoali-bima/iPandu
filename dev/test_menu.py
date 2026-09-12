#!/usr/bin/env python3
"""The native "/" command menu and the button-based /menu, kept honest.

Two separate findings on 2026-09-12 drove this:

1. The itbutler deployment showed Telegram's "Menu" button; the bscloud
   deployment, running the exact same code, did not. Nothing in this file
   had ever called set_my_commands() -- whichever bot had the menu got it
   from a one-time manual @BotFather step, not from the code. A new
   deployment would silently miss it forever unless someone remembered.
   _build_bot_commands() is checked here against HELP_TEXT_ID directly (not
   a second, hand-maintained list) so the two can't drift apart, and
   test_capabilities_brief.py-style: this test fails loudly if the parse
   comes back empty or malformed, rather than the menu just being blank.

2. /menu is a second way to reach the same handful of commands, as ordinary
   inline buttons -- unlike the native menu, these work the same in a group
   as in a DM. It must replay the REAL /command handler, not a second
   description of what it does, or the two could answer differently for the
   same tap.
"""
import asyncio
import atexit
import importlib.util
import os
import re
import shutil as _shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_menu_"))
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


# --- _build_bot_commands(): one source, not two lists to keep in sync ------
cmds = mod._build_bot_commands()

# An independent count, deliberately not mod._HELP_COMMAND_LINE_RE itself --
# a bug in that regex should not be able to certify its own output. This one
# only requires the leading "/word" and an em dash somewhere on the line.
independent_count = sum(
    1 for line in mod.HELP_TEXT_ID.splitlines()
    if re.match(r"^/[a-zA-Z][a-zA-Z0-9_]*", line.strip()) and "—" in line
)
check(f"parsed a command for every /word — line in HELP_TEXT_ID "
      f"({len(cmds)} vs {independent_count} counted independently)",
      len(cmds) == independent_count)
check("found a realistic number of commands (not an empty or truncated parse)",
      len(cmds) > 40)
check("/menu documents itself -- the menu that lists commands is one of them",
      "menu" in [c.command for c in cmds])
check("/help is in there too (sanity: the very first documented command "
      "alphabetically adjacent commands parse correctly)",
      "help" in [c.command for c in cmds])
check("no duplicate command names", len({c.command for c in cmds}) == len(cmds))
check("every command name is lowercase, Bot API's own charset (a leading "
      "'/' or an argument placeholder like <name> leaking through would "
      "break set_my_commands wholesale, not just look ugly)",
      all(re.fullmatch(r"[a-z0-9_]{1,32}", c.command) for c in cmds))
check("every description fits Telegram's own limit",
      all(1 <= len(c.description) <= 256 for c in cmds))

# --- startup wiring: both scopes, so it shows in groups too -----------------
src = Path(SRC).read_text(encoding="utf-8")
check("startup registers the command list for private chats",
      "BotCommandScopeAllPrivateChats" in src)
check("...and for group chats -- the itbutler/bscloud gap was specifically "
      "about whether it works outside a DM",
      "BotCommandScopeAllGroupChats" in src)
check("set_my_commands is actually called (not just imported)",
      src.count("set_my_commands(") >= 2)


# --- /menu: sends the panel ---------------------------------------------
def make_update(callback_data=None):
    sent = SimpleNamespace(kwargs=None)

    async def reply_text(*a, **kw):
        sent.kwargs = kw
        return None

    msg = SimpleNamespace(reply_text=reply_text)
    if callback_data is None:
        return SimpleNamespace(
            message=msg, callback_query=None, effective_message=msg,
            effective_chat=SimpleNamespace(id=1, type="private"),
            effective_user=SimpleNamespace(id=111),
        ), sent
    query = SimpleNamespace(
        data=callback_data, message=msg,
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        message=None, callback_query=query, effective_message=msg,
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(id=111),
    ), sent


async def menu_command_case():
    u, sent = make_update()
    await mod.cmd_menu(u, SimpleNamespace())
    markup = (sent.kwargs or {}).get("reply_markup")
    check("/menu replies with an inline keyboard",
          isinstance(markup, mod.InlineKeyboardMarkup) and len(markup.inline_keyboard) > 0)
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    check("every button's callback_data resolves to a real handler in "
          "cmd_menu_button's own dispatch table",
          all(b.split(":", 1)[1] in
              {"status", "servers", "addserver", "gdrive", "unlock",
               "boundaries", "spend", "help"}
              for b in buttons))


asyncio.run(menu_command_case())


# --- /menu buttons: replay the REAL handler, not a second copy -------------
async def menu_button_dispatch_case():
    for action, target in [
        ("status", "cmd_status"), ("servers", "cmd_servers"),
        ("addserver", "cmd_addserver"), ("gdrive", "cmd_gdrive"),
        ("unlock", "cmd_unlock"), ("boundaries", "cmd_boundaries"),
        ("spend", "cmd_spend"), ("help", "cmd_help"),
    ]:
        u, _ = make_update(callback_data=f"menu:{action}")
        with patch.object(mod, target, new=AsyncMock()) as stub:
            await mod.cmd_menu_button(u, SimpleNamespace())
        check(f"menu:{action} calls the real {target}(), not a reimplementation",
              stub.await_count == 1)
        check(f"menu:{action} acknowledges the tap",
              u.callback_query.answer.await_count >= 1)


asyncio.run(menu_button_dispatch_case())


async def menu_button_unknown_action_case():
    u, _ = make_update(callback_data="menu:doesnotexist")
    crashed = None
    try:
        await mod.cmd_menu_button(u, SimpleNamespace())
    except Exception as exc:  # noqa: BLE001 -- exactly what must not happen
        crashed = exc
    check("an unrecognised menu: action is answered, not a crash",
          crashed is None and u.callback_query.answer.await_count >= 1)


asyncio.run(menu_button_unknown_action_case())

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
