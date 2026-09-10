#!/usr/bin/env python3
"""Document attachments -- PDF, HTML, anything -- are read, not refused.

A PDF or HTML file sent to the bot got "I can read images, but not this kind of
attachment": images had a handler and audio now does, but every other document
fell through to that refusal and was never saved. Both CLIs read a file from
disk when the prompt names its path -- verified live on itbutler, agy AND claude
each read a test PDF and a test HTML and returned the marker string inside -- so
the fix is to save the file and name its path, the way an image already is, with
no forced model: whichever tier answers can read it.
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
scratch = Path(tempfile.mkdtemp(prefix="isla_doc_"))
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


class FakeFile:
    def __init__(self, path):
        self.file_path = path

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(b"%PDF-1.4 or <html> -- pretend bytes")


def make_ctx(remote_name="stored/file"):
    async def get_file(file_id):
        return FakeFile(remote_name)
    return SimpleNamespace(bot=SimpleNamespace(get_file=get_file))


def doc(file_id, size, file_name):
    return SimpleNamespace(file_id=file_id, file_size=size, file_name=file_name)


def msg_with(document=None, reply_to=None):
    return SimpleNamespace(document=document, reply_to_message=reply_to)


def upd(msg):
    return SimpleNamespace(effective_message=msg,
                           effective_chat=SimpleNamespace(id=1, type="private"))


async def doc_cases():
    # a PDF
    m = msg_with(doc("PDF11111111", 5000, "quarterly_report.pdf"))
    got = await mod._save_incoming_document(upd(m), make_ctx())
    check("a PDF is downloaded and returned with its name",
          got is not None and got[1] == "quarterly_report.pdf")
    check("...and the .pdf extension is preserved, so the reader opens it right",
          got is not None and got[0].suffix == ".pdf" and got[0].exists())

    # an HTML file
    m = msg_with(doc("HTML2222222", 4000, "page.html"))
    got = await mod._save_incoming_document(upd(m), make_ctx())
    check("an HTML file is handled too, extension kept",
          got is not None and got[0].suffix == ".html")

    # some other type -- csv -- must work as well ("maupun file lainnya")
    m = msg_with(doc("CSV33333333", 2000, "hosts.csv"))
    got = await mod._save_incoming_document(upd(m), make_ctx())
    check("any other document type is accepted (a csv here)",
          got is not None and got[0].suffix == ".csv")

    # a document with no filename still gets a sane extension from the remote path
    m = msg_with(doc("NONAME44444", 1000, ""))
    got = await mod._save_incoming_document(upd(m), make_ctx("remote/thing.pdf"))
    check("a nameless document falls back to the remote extension",
          got is not None and got[0].suffix == ".pdf")

    # the group case: the document is in the replied-to message
    inner = msg_with(doc("REPLY555555", 3000, "shared.pdf"))
    m = msg_with(reply_to=inner)
    got = await mod._save_incoming_document(upd(m), make_ctx())
    check("a document in the replied-to message is picked up (the group case)",
          got is not None and got[1] == "shared.pdf")

    # nothing attached
    got = await mod._save_incoming_document(upd(msg_with()), make_ctx())
    check("a message with no document returns None, quietly", got is None)

    # over the cap
    big = msg_with(doc("BIG66666666", mod.MAX_INCOMING_MEDIA_BYTES + 1, "huge.pdf"))
    got = await mod._save_incoming_document(upd(big), make_ctx())
    check("a document over the size cap is skipped", got is None)


asyncio.run(doc_cases())


# --- the wiring, and the promise that BOTH models read it -------------------
src = Path(SRC).read_text(encoding="utf-8")
check("handle_message has a document branch",
      "elif doc_info is not None:" in src)
check("...that names the file path in the prompt for the model to read",
      "path before answering: {doc_path}" in src)
check("...and carries the original filename, so the model knows the type",
      "attached a file named {fname!r}" in src)

# force_agy must NOT be set by the document branch -- both models can read files,
# so the user's model choice (Claude included) still applies.
doc_branch = src.split("elif doc_info is not None:")[1].split("elif msg.photo")[0]
check("a document turn does NOT force a model -- both agy and claude can read",
      "force_agy = True" not in doc_branch)

check("the old 'I can only read images' refusal is gone",
      "but not this kind of" not in src)
check("...replaced by an honest download-failure message in both languages",
      "could not read that attachment" in src
      and "tidak berhasil membaca lampiran" in src)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
