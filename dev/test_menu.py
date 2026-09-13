#!/usr/bin/env python3
"""The native "/" command menu and the button-based /menu, kept honest.

Three findings drove this, 2026-09-12/13:

1. The itbutler deployment showed Telegram's "Menu" button; the bscloud
   deployment, running the exact same code, did not. Nothing in this file
   had ever called set_my_commands() -- whichever bot had the menu got it
   from a one-time manual @BotFather step, not from the code. A new
   deployment would silently miss it forever unless someone remembered.
   _build_bot_commands() is checked here against HELP_TEXT_ID directly (not
   a second, hand-maintained list) so the two can't drift apart.

2. /menu is a second way to reach the same handful of commands, as ordinary
   inline buttons -- unlike the native menu, these work the same in a group
   as in a DM. Each button must replay the REAL /command handler, not a
   second description of what it does, or the two could answer differently
   for the same tap.

3. Asked for directly: an Exit button, and a way for a room to pick its own
   subset of buttons rather than the fixed eight. Customizing is gated the
   same as any other room-wide setting (owner anywhere, or a registered
   group's own admin) -- it changes what everyone in the room sees, so it
   is not a per-person preference.
"""
import asyncio
import atexit
import importlib.util
import json
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
mod.MENU_PREFS_FILE = scratch / "menu_prefs.json"

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


# --- test fixtures -----------------------------------------------------
def make_update(callback_data=None, chat_id=1):
    sent = SimpleNamespace(kwargs=None)

    async def reply_text(*a, **kw):
        sent.kwargs = kw
        return None

    msg = SimpleNamespace(reply_text=reply_text)
    if callback_data is None:
        return SimpleNamespace(
            message=msg, callback_query=None, effective_message=msg,
            effective_chat=SimpleNamespace(id=chat_id, type="private"),
            effective_user=SimpleNamespace(id=111),
        ), sent
    query = SimpleNamespace(
        data=callback_data, message=msg,
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        message=None, callback_query=query, effective_message=msg,
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_user=SimpleNamespace(id=111),
    ), sent


def markup_actions(markup):
    return [b.callback_data.split(":", 1)[1]
            for row in markup.inline_keyboard for b in row]


ALL_KNOWN = set(mod._MENU_CANDIDATE_LABELS) | {"customize", "exit"}


# --- /menu: sends the panel, default set, always with exit+customize -------
async def menu_command_case():
    u, sent = make_update()
    await mod.cmd_menu(u, SimpleNamespace())
    markup = (sent.kwargs or {}).get("reply_markup")
    check("/menu replies with an inline keyboard",
          isinstance(markup, mod.InlineKeyboardMarkup) and len(markup.inline_keyboard) > 0)
    actions = markup_actions(markup)
    check("every button's callback_data resolves to something cmd_menu_button "
          "or cmd_menu_edit_button actually knows about",
          all(a in ALL_KNOWN for a in actions))
    check("a fresh room gets the default 8 commands",
          set(actions) - {"customize", "exit"} == set(mod._MENU_DEFAULT_ACTIONS))
    check("Customize is always offered", "customize" in actions)
    check("Exit is always offered", "exit" in actions)


asyncio.run(menu_command_case())


# --- /menu buttons: replay the REAL handler, not a second copy -------------
async def menu_button_dispatch_case():
    for action, target in [
        ("status", "cmd_status"), ("servers", "cmd_servers"),
        ("addserver", "cmd_addserver"), ("gdrive", "cmd_gdrive"),
        ("gdrivestatus", "cmd_gdrivestatus"), ("unlock", "cmd_unlock"),
        ("boundaries", "cmd_boundaries"), ("spend", "cmd_spend"),
        ("providers", "cmd_providers"), ("agentstatus", "cmd_agentstatus"),
        ("tools", "cmd_tools"), ("mode", "cmd_mode"), ("learned", "cmd_learned"),
        ("mcpservers", "cmd_mcpservers"), ("schedules", "cmd_schedules"),
        ("snapshots", "cmd_snapshots"), ("memory", "cmd_memory"),
        ("help", "cmd_help"),
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


# --- Exit: closes without crashing, and (via _safe_edit's own default)
# leaves no keyboard behind --------------------------------------------
async def menu_exit_case():
    u, _ = make_update(callback_data="menu:exit")
    await mod.cmd_menu_button(u, SimpleNamespace())
    check("Exit acknowledges the tap", u.callback_query.answer.await_count >= 1)
    edit_kwargs = u.callback_query.edit_message_text.call_args
    check("Exit edits the message (closing it) rather than leaving it as-is",
          u.callback_query.edit_message_text.await_count == 1)
    check("...and does not carry an active menu keyboard forward (relies on "
          "_safe_edit's own empty-keyboard default -- not passing one here "
          "IS the fix, not an oversight)",
          "reply_markup" not in (edit_kwargs.kwargs if edit_kwargs else {})
          or not (edit_kwargs.kwargs.get("reply_markup") or SimpleNamespace(inline_keyboard=())).inline_keyboard)


asyncio.run(menu_exit_case())


# --- Customize: gated like any other room-wide setting ---------------------
async def customize_permission_case():
    u, _ = make_update(callback_data="menu:customize")
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=False)):
        await mod.cmd_menu_button(u, SimpleNamespace())
    check("customize refuses someone who isn't the owner or a registered "
          "group admin", u.callback_query.answer.await_count >= 1)
    check("...and does not open the editor for them",
          u.callback_query.edit_message_text.await_count == 0)
    check("...refusal is not just silence -- an alert explains it",
          u.callback_query.answer.call_args.kwargs.get("show_alert") is True)


asyncio.run(customize_permission_case())


async def customize_toggle_and_save_case():
    chat_id = 42
    # Open the editor.
    u, _ = make_update(callback_data="menu:customize", chat_id=chat_id)
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)):
        await mod.cmd_menu_button(u, SimpleNamespace())
    check("customize opens the editor for someone who is allowed",
          u.callback_query.edit_message_text.await_count == 1)
    check("the working selection starts from the CURRENT (default) menu, "
          "not empty and not the full pool",
          mod._menu_edit_sessions.get(chat_id) == set(mod._MENU_DEFAULT_ACTIONS))

    # Toggle one on (memory, not in the default 8) and one off (spend).
    for action in ("menuedit:memory", "menuedit:spend"):
        u2, _ = make_update(callback_data=action, chat_id=chat_id)
        with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)):
            await mod.cmd_menu_edit_button(u2, SimpleNamespace())
    working = mod._menu_edit_sessions.get(chat_id)
    check("toggling adds a command that wasn't selected",
          "memory" in working)
    check("toggling removes one that was",
          "spend" not in working)
    check("the session survives across taps (it's the same working set, not "
          "reset each time)", mod._menu_edit_sessions.get(chat_id) is working)

    # Save.
    u3, _ = make_update(callback_data="menuedit:save", chat_id=chat_id)
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)):
        await mod.cmd_menu_edit_button(u3, SimpleNamespace())
    check("save clears the in-progress editor session",
          chat_id not in mod._menu_edit_sessions)
    saved = json.loads(mod.MENU_PREFS_FILE.read_text(encoding="utf-8"))
    check("save actually writes the file", str(chat_id) in saved)
    check("...with the new selection, not the old default",
          "memory" in saved[str(chat_id)] and "spend" not in saved[str(chat_id)])

    # /menu now reflects the saved choice.
    u4, sent4 = make_update(chat_id=chat_id)
    await mod.cmd_menu(u4, SimpleNamespace())
    actions4 = markup_actions((sent4.kwargs or {}).get("reply_markup"))
    check("a saved customization is what /menu shows afterwards, not the "
          "old default", "memory" in actions4 and "spend" not in actions4)
    check("...and the always-on pair is still there",
          "customize" in actions4 and "exit" in actions4)


asyncio.run(customize_toggle_and_save_case())


async def customize_reset_and_cancel_case():
    chat_id = 43
    u, _ = make_update(callback_data="menu:customize", chat_id=chat_id)
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)):
        await mod.cmd_menu_button(u, SimpleNamespace())
        u2, _ = make_update(callback_data="menuedit:memory", chat_id=chat_id)
        await mod.cmd_menu_edit_button(u2, SimpleNamespace())
        check("a change is visible before reset",
              "memory" in mod._menu_edit_sessions.get(chat_id, set()))

        u3, _ = make_update(callback_data="menuedit:reset", chat_id=chat_id)
        await mod.cmd_menu_edit_button(u3, SimpleNamespace())
        check("reset goes back to exactly the default set",
              mod._menu_edit_sessions.get(chat_id) == set(mod._MENU_DEFAULT_ACTIONS))

        u4, _ = make_update(callback_data="menuedit:memory", chat_id=chat_id)
        await mod.cmd_menu_edit_button(u4, SimpleNamespace())
        u5, _ = make_update(callback_data="menuedit:cancel", chat_id=chat_id)
        await mod.cmd_menu_edit_button(u5, SimpleNamespace())
    check("cancel clears the session without saving",
          chat_id not in mod._menu_edit_sessions)
    check("...and nothing was written for a room that never saved before",
          str(chat_id) not in mod._read_menu_prefs())


asyncio.run(customize_reset_and_cancel_case())


async def customize_cannot_save_empty_case():
    chat_id = 44
    mod._menu_edit_sessions[chat_id] = set()
    u, _ = make_update(callback_data="menuedit:save", chat_id=chat_id)
    with patch.object(mod, "_may_authorize_group_action", new=AsyncMock(return_value=True)):
        await mod.cmd_menu_edit_button(u, SimpleNamespace())
    check("saving an empty selection is refused with an alert, not written",
          u.callback_query.answer.call_args.kwargs.get("show_alert") is True
          and str(chat_id) not in mod._read_menu_prefs())
    check("...and the editor session is left open to fix, not silently dropped",
          chat_id in mod._menu_edit_sessions)


asyncio.run(customize_cannot_save_empty_case())


async def stale_saved_action_falls_back_case():
    """A command that existed when a room saved its menu, then got removed
    or renamed, must not make /menu come back empty."""
    chat_id = 45
    mod._write_menu_prefs({str(chat_id): ["status", "this-command-no-longer-exists"]})
    u, sent = make_update(chat_id=chat_id)
    await mod.cmd_menu(u, SimpleNamespace())
    actions = markup_actions((sent.kwargs or {}).get("reply_markup"))
    check("a saved action that no longer exists is dropped, not carried "
          "forward as a dead button", "this-command-no-longer-exists" not in actions)
    check("...and the still-valid part of the saved choice survives",
          "status" in actions)

    mod._write_menu_prefs({str(chat_id): ["this-command-no-longer-exists"]})
    u2, sent2 = make_update(chat_id=chat_id)
    await mod.cmd_menu(u2, SimpleNamespace())
    actions2 = markup_actions((sent2.kwargs or {}).get("reply_markup"))
    check("if EVERYTHING saved is gone, the default menu comes back instead "
          "of an empty one",
          set(actions2) - {"customize", "exit"} == set(mod._MENU_DEFAULT_ACTIONS))


asyncio.run(stale_saved_action_falls_back_case())

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
