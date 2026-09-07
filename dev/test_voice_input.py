#!/usr/bin/env python3
"""A voice message in gets transcribed and answered, on a model that can hear.

The bot answered text and could reply with voice (Gemini generates the audio
itself), but a voice message IN got no response: the handler only registered
TEXT, PHOTO and Document, so a voice note matched nothing and vanished.

agy (Gemini) decodes audio natively from a local path -- verified live on the
bscloud host, a 440 Hz tone identified as A4 -- so a voice note is handled the
way an image already is: saved, its path named in the prompt, read by the model.
The one difference is that Claude cannot hear, so a voice turn is pinned to an
agy tier. That pin is the piece with real branching, so it is tested hardest.
"""
import asyncio
import atexit
import importlib.util
import os
import shutil as _shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SRC = sys.argv[1]
scratch = Path(tempfile.mkdtemp(prefix="isla_voice_"))
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
mod.INCOMING_MEDIA_DIR = scratch / "incoming"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


# --- the pin: a voice turn must land on a model that can hear ---------------
agy_tiers = [t for t in mod.ALL_TIERS if t.get("provider") == "agy"]
claude_tiers = [t for t in mod.ALL_TIERS if t.get("provider") == "claude"]
check(f"there is at least one agy tier to pin a voice turn to ({len(agy_tiers)})",
      len(agy_tiers) >= 1)

# no user override, voice turn -> pinned to agy
pinned = mod._pin_agy_for_voice(None, True)
check("a voice turn with no model override is pinned to an agy tier",
      pinned is not None and pinned.get("provider") == "agy")

# user chose a Claude model, voice turn -> overridden to agy (Claude can't hear)
if claude_tiers:
    got = mod._pin_agy_for_voice(dict(claude_tiers[0]), True)
    check("a voice turn overrides a Claude /usemodel choice -- it cannot hear",
          got.get("provider") == "agy")
else:
    check("(no claude tier configured to test the override against)", True)

# user chose an agy model -> left exactly as they asked
if len(agy_tiers) >= 1:
    choice = dict(agy_tiers[-1])
    got = mod._pin_agy_for_voice(choice, True)
    check("an explicit agy choice is respected, not replaced",
          got["model"] == choice["model"])

# a NON-voice turn is never touched, whatever the model
sentinel = {"provider": "claude", "model": "claude-opus-5"}
check("a text/image turn (force_agy=False) keeps the user's model untouched",
      mod._pin_agy_for_voice(dict(sentinel), False) == sentinel)
check("...and a text turn with no override stays None",
      mod._pin_agy_for_voice(None, False) is None)


# --- _save_incoming_voice: download + convert to 16k mono wav ---------------
class FakeFile:
    def __init__(self, path):
        self.file_path = path

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(b"OggS-fake-opus-bytes")


def make_ctx():
    async def get_file(file_id):
        return FakeFile("voice/file_123.oga")
    return SimpleNamespace(bot=SimpleNamespace(get_file=get_file))


def voice_msg(**kw):
    base = dict(voice=None, audio=None, document=None, reply_to_message=None)
    base.update(kw)
    return SimpleNamespace(**base)


def upd(msg):
    return SimpleNamespace(effective_message=msg,
                           effective_chat=SimpleNamespace(id=1, type="private"))


def fake_ffmpeg_run(argv, **kw):
    # write the output wav (last arg) so the helper sees a real file
    Path(argv[-1]).write_bytes(b"RIFF-fake-wav")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


async def voice_cases():
    with patch.object(mod, "_ffmpeg", return_value="/usr/bin/ffmpeg"), \
            patch.object(mod.subprocess, "run", side_effect=fake_ffmpeg_run):
        # a plain voice note
        m = voice_msg(voice=SimpleNamespace(file_id="V123456789", file_size=5000))
        got = await mod._save_incoming_voice(upd(m), make_ctx())
        check("a voice note is downloaded and returned as a .wav",
              got is not None and got.suffix == ".wav" and got.exists())

        # an audio FILE (not a voice note)
        m = voice_msg(audio=SimpleNamespace(file_id="A987654321", file_size=8000))
        got = await mod._save_incoming_voice(upd(m), make_ctx())
        check("an audio file is handled too, not only a voice note",
              got is not None and got.exists())

        # the group case: voice is in the message being replied to
        inner = voice_msg(voice=SimpleNamespace(file_id="R111222333", file_size=4000))
        m = voice_msg(reply_to_message=inner)
        got = await mod._save_incoming_voice(upd(m), make_ctx())
        check("a voice note in the replied-to message is picked up (the group case)",
              got is not None)

        # nothing audio at all
        got = await mod._save_incoming_voice(upd(voice_msg()), make_ctx())
        check("a message with no audio returns None, quietly", got is None)

        # too large is refused
        big = voice_msg(voice=SimpleNamespace(
            file_id="B1", file_size=mod.MAX_INCOMING_MEDIA_BYTES + 1))
        got = await mod._save_incoming_voice(upd(big), make_ctx())
        check("a voice note over the size cap is skipped", got is None)

    # ffmpeg missing: fall back to the raw file rather than failing
    with patch.object(mod, "_ffmpeg", return_value=None):
        m = voice_msg(voice=SimpleNamespace(file_id="N1", file_size=3000))
        got = await mod._save_incoming_voice(upd(m), make_ctx())
        check("with no ffmpeg the raw audio is used, not dropped",
              got is not None and got.exists())


asyncio.run(voice_cases())


# --- the wiring is actually in place ----------------------------------------
src = Path(SRC).read_text(encoding="utf-8")
check("the message handler now registers VOICE and AUDIO",
      "filters.VOICE" in src and "filters.AUDIO" in src)
check("force_agy is set ONLY on the voice branch, nowhere else",
      src.count("force_agy = True") == 1)
voice_branch = src.split("elif voice_path is not None:")[1].split("    elif ")[0]
check("...and the voice branch is what sets it",
      "force_agy = True" in voice_branch)
check("the voice prompt tells the model to transcribe and answer",
      "transcribe what they actually said" in src)
check("_run_turn threads force_agy down to the inner turn",
      "force_agy=force_agy" in src)
check("a voice turn is pinned through the helper, not an inline branch",
      "_pin_agy_for_voice(forced_tier, force_agy)" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
