#!/usr/bin/env python3
"""Tests that /setbrief REPLACES the role instead of accumulating it.

Measured on a live deployment, not imagined. `set_brief_role()` patched the
opening line with:

    ^(.*?assistant for )(.+?)(\\.)

`(.+?)(\\.)` stops at the FIRST full stop. That is correct for the role this
command is documented for -- "a 7-node Proxmox cluster" -- and it has to stop
somewhere, because the template's own prose continues on that same line
("...assistant for X. You are helpful, knowledgeable, and direct.").

An operator who writes a role containing full stops gets something else: only
sentence one of the previous role is replaced, and sentences two onward stay.
Every edit leaves another layer. After three, the real brief's opening line
held "Lingkup kerja" three times, "Untuk riset" three times, and the
template's "You are helpful," wedged in the middle of them -- 19KB the model
paid for on every conversation, none of it intended.

The fix rebuilds the opening line from the template rather than patching part
of it, so it is idempotent whatever the role contains, and an already-damaged
line is repaired by the next run rather than needing a separate command.
"""
import atexit
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile

SRC = pathlib.Path(sys.argv[1]).resolve()

HOME = tempfile.mkdtemp(prefix="isla_brief_")
atexit.register(shutil.rmtree, str(HOME), ignore_errors=True)
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME     # Path.home() reads this one on Windows
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la_brief", str(SRC))
mod = importlib.util.module_from_spec(spec)
sys.modules["la_brief"] = mod
spec.loader.exec_module(mod)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


TEMPLATE_HEAD = ("You are an infrastructure assistant for "
                 "[YOUR ORGANIZATION / PROJECT NAME]. You are helpful, "
                 "knowledgeable, and direct.\n")
BODY = ("You assist with checking system status.\n"
        "\n"
        "## Environment: [YOUR ORGANIZATION / PROJECT NAME]\n"
        "\n"
        "## HARD BOUNDARIES\n"
        "- never touch the payroll VM\n"
        "\n"
        "<!-- LEARNED -->\n"
        "- the cluster has 3 nodes\n")

scratch = pathlib.Path(tempfile.mkdtemp(prefix="isla_brief_s_"))
atexit.register(shutil.rmtree, str(scratch), ignore_errors=True)

def fresh(first_line=None):
    """A brief + its template, wired into the module. Returns the brief path."""
    for name in ("SOUL.md.template", "GEMINI.md.template"):
        (scratch / name).write_text(TEMPLATE_HEAD + BODY, encoding="utf-8")
    soul = scratch / "SOUL.md"
    gem = scratch / "GEMINI.md"
    text = (first_line if first_line is not None else TEMPLATE_HEAD) + BODY
    soul.write_text(text, encoding="utf-8")
    gem.write_text(text, encoding="utf-8")
    mod.BASE_DIR = scratch
    mod.SYSTEM_PROMPT_FILE = soul
    mod.GEMINI_PROMPT_FILE = gem
    return soul, gem


# --- 1. a plain role, the case that always worked --------------------------
soul, gem = fresh()
mod.set_brief_role("a 7-node Proxmox cluster")
first = soul.read_text(encoding="utf-8").split("\n")[0]
check("the role lands in the opening sentence",
      first.startswith("You are an infrastructure assistant for a 7-node Proxmox cluster."))
check("...and the template's own prose after it survives",
      "You are helpful, knowledgeable, and direct." in first)
check("...and the Environment heading is set too",
      "## Environment: a 7-node Proxmox cluster" in soul.read_text(encoding="utf-8"))
check("both briefs are written, not just one",
      "a 7-node Proxmox cluster" in gem.read_text(encoding="utf-8"))

full = soul.read_text(encoding="utf-8")
check("the hard boundaries are passed through untouched",
      "- never touch the payroll VM" in full)
check("...and so is the learned zone", "- the cluster has 3 nodes" in full)


# --- 2. THE BUG: a role containing full stops ------------------------------
R1 = ("Simpan laporan ke folder Laporan. Jangan buat folder baru. "
      "Lingkup kerja: Proxmox dan Mikrotik.")
R2 = ("Simpan backup ke folder BACKUP. Jangan buat folder baru. "
      "Lingkup kerja: Proxmox saja.")

soul, gem = fresh()
mod.set_brief_role(R1)
after_one = soul.read_text(encoding="utf-8").split("\n")[0]
check("a multi-sentence role lands whole", R1 in after_one)
check("...without swallowing the template's prose",
      "You are helpful, knowledgeable, and direct." in after_one)

mod.set_brief_role(R2)
after_two = soul.read_text(encoding="utf-8").split("\n")[0]
check("setting a second one REPLACES the first", R2 in after_two and R1 not in after_two)
check("...leaving no orphaned sentence from it -- the accumulation bug",
      "Laporan" not in after_two)
check("...and not two copies of the shared sentence either",
      after_two.count("Jangan buat folder baru.") == 1)
check("...nor of the template's prose", after_two.count("You are helpful,") == 1)

for _ in range(4):
    mod.set_brief_role(R2)
stable = soul.read_text(encoding="utf-8").split("\n")[0]
check("running it six times gives the same line as running it twice -- "
      "idempotent, which is the property that was missing",
      stable == after_two)
check("...and the line does not grow", len(stable) == len(after_two))


# --- 3. it REPAIRS a line already damaged by the old behaviour -------------
# Reproduced from the live file: the role duplicated three deep, with the
# template's own sentence stranded in the middle of it.
DAMAGED = ("You are an infrastructure assistant for Lingkup kerja: A. Untuk riset: B. "
           "Lingkup kerja: A. Untuk riset: B. Lingkup kerja: A. You are helpful, "
           "knowledgeable, and direct.\n")
soul, gem = fresh(DAMAGED)
before = soul.read_text(encoding="utf-8").split("\n")[0]
check("the damaged fixture really is damaged", before.count("Lingkup kerja") == 3)

mod.set_brief_role("infrastruktur UIN, Bimajaya, ITEC")
fixed = soul.read_text(encoding="utf-8").split("\n")[0]
check("one /setbrief cleans it up -- no separate repair command needed",
      fixed.count("Lingkup kerja") == 0)
check("...leaving exactly the new role", "assistant for infrastruktur UIN, Bimajaya, ITEC." in fixed)
check("...and one copy of the template's prose",
      fixed.count("You are helpful,") == 1)
check("...and nothing below line one is disturbed",
      "- never touch the payroll VM" in soul.read_text(encoding="utf-8"))


# --- 4. the scope set by /setscope is preserved ----------------------------
soul, gem = fresh("You are a network engineering assistant for something. "
                  "You are helpful, knowledgeable, and direct.\n")
mod.set_brief_role("the UIN estate")
line = soul.read_text(encoding="utf-8").split("\n")[0]
check("a scope changed by /setscope survives a later /setbrief",
      line.startswith("You are a network engineering assistant for the UIN estate."))
check("...with the right article for a consonant", line.startswith("You are a network"))

soul, gem = fresh("You are an infrastructure assistant for something. "
                  "You are helpful, knowledgeable, and direct.\n")
mod.set_brief_role("the UIN estate")
check("...and for a vowel",
      soul.read_text(encoding="utf-8").startswith("You are an infrastructure assistant"))


# --- 5. inputs that would break a naive implementation --------------------
soul, gem = fresh()
mod.set_brief_role(r"paths like C:\temp and a backref \1 and \g<0>")
line = soul.read_text(encoding="utf-8").split("\n")[0]
env = [l for l in soul.read_text(encoding="utf-8").split("\n")
       if l.startswith("## Environment:")][0]
check("a role containing regex backreferences is stored literally, not "
      "expanded -- re.sub treats \\1 in a replacement STRING as a group",
      r"\1" in line and r"\g<0>" in line)
check("...in the Environment heading too", r"\1" in env)

soul, gem = fresh()
mod.set_brief_role("   spaced   out\n\nrole   ")
line = soul.read_text(encoding="utf-8").split("\n")[0]
check("whitespace is collapsed, so a pasted multi-line role cannot break the "
      "opening line in two", "spaced out role" in line and len(soul.read_text(
          encoding="utf-8").split("\n")[0].split(". ")) >= 2)

soul, gem = fresh()
mod.set_brief_role("ends with a stop.")
check("a trailing full stop is not doubled",
      "assistant for ends with a stop. You are helpful," in
      soul.read_text(encoding="utf-8"))


# --- 6. no template beside the brief: degrade, do not fail ----------------
soul, gem = fresh()
for name in ("SOUL.md.template", "GEMINI.md.template"):
    (scratch / name).unlink()
mod.set_brief_role("fallback role")
check("with no template to rebuild from, the role is still set rather than "
      "the command failing", "fallback role" in soul.read_text(encoding="utf-8"))
check("...and the boundaries still survive that path",
      "- never touch the payroll VM" in soul.read_text(encoding="utf-8"))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
