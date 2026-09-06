#!/usr/bin/env python3
"""Every message the bot speaks exists in both languages.

`audit_lang.py` reads well but proves nothing: it flags 38 call sites, most of
them false positives where the text was built from `_t()` a few lines earlier.
Fine as a review aid, useless as a gate -- and the operator has had to remind us
about this more than once, which is the signal that it should not depend on
anyone remembering.

Two checks here, and the second is the one that lasts:

  1. The messages added in v0.2b.78-82, pinned by fragment. A regression list.
  2. EVERY `_t()` call in the file, structurally: two arguments, both non-empty.
     A new message that only speaks English fails here on the day it is written,
     not when someone with `/lang id` runs into it.

The fragments in (1) are matched after joining adjacent string literals. Python
concatenates "a " "b" into "a b"; a plain substring search does not, and three
of these first reported MISSING on messages that were present and correct --
the same brittleness that broke two assertions elsewhere the same day.
"""
import ast
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
raw = SRC.read_text(encoding="utf-8")
joined = re.sub(r'"\s*\n\s*(?:f)?"', "", raw)

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


# --- 1. the messages this run added ----------------------------------------
PAIRS = [
    ("unknown-host card", "is not in the inventory yet", "belum ada di inventaris"),
    ("unknown-host: no access", "I have no key there", "belum punya kunci di sana"),
    ("button: register", "Register it", "Daftarkan"),
    ("button: just answer", "Just answer", "Jawab saja"),
    ("just-answer reply", "Answering without registering", "Dijawab tanpa didaftarkan"),
    ("password warning", "That looked like a password", "terlihat seperti password"),
    ("password: treat as exposed", "Treat it as exposed", "Anggap sudah bocor"),
    ("password: never send one", "You never need to send me one",
     "tidak pernah perlu mengirimkannya"),
    ("group needs delete right", "make me an admin with <b>Delete",
     "jadikan saya admin dengan "),
    ("password prompt", "as your next message", "sebagai pesan berikutnya"),
    ("password prompt: caveat", "It still passes through Telegram",
     "ia tetap melewati Telegram"),
    ("refuse where undeletable", "I can only take a password where",
     "Saya hanya mau menerima password"),
    ("key installed", "Key installed and verified", "Kunci terpasang dan terverifikasi"),
    ("key installed: change it", "Change that password now",
     "Ganti password itu sekarang"),
    ("bootstrap running", "Placing my key on", "Memasang kunci saya di"),
    ("hardening advice", "still accepts password logins", "masih menerima login password"),
    ("hardening: keep session", "Keep this SSH session open",
     "Biarkan sesi SSH ini tetap terbuka"),
    ("hardening: why 00", "sshd takes the first value", "sshd memakai nilai pertama"),
    ("unlock already open", "Write mode is already open", "Write mode masih terbuka"),
    ("extend button", "Extend", "Perpanjang"),
    ("extended without PIN", "No PIN needed: same session",
     "Tanpa PIN: masih sesi yang sama"),
    ("session ceiling spent", "used its full window", "sudah memakai jatah penuhnya"),
    ("window closed mid-turn", "closed while this was still running",
     "tertutup saat ini masih berjalan"),
    ("progress heartbeat", "Still working", "Masih dikerjakan"),
    ("heartbeat: write ending", "Write access ends in about", "Akses tulis habis sekitar"),
    ("drive rate-limited", "Drive is rate-limited", "Drive sedang kena batas laju"),
    ("drive: no share link", "share link unavailable", "link berbagi belum bisa diambil"),
]
missing = [(n, e in joined, i in joined) for n, e, i in PAIRS
           if not (e in joined and i in joined)]
for n, has_e, has_i in missing:
    print(f"       {n}: english={'ok' if has_e else 'MISSING'} "
          f"indonesian={'ok' if has_i else 'MISSING'}")
check(f"all {len(PAIRS)} messages added in v0.2b.78-82 speak both languages",
      not missing)


# --- 2. every _t() call, structurally --------------------------------------
tree = ast.parse(raw)
one_sided = []
total = 0
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_t"):
        continue
    total += 1
    # _t(lang, english, indonesian)
    if len(node.args) < 3:
        one_sided.append((node.lineno, f"only {len(node.args)} argument(s)"))
        continue
    for which, arg in (("english", node.args[1]), ("indonesian", node.args[2])):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                and not arg.value.strip():
            one_sided.append((node.lineno, f"{which} half is empty"))

for lineno, why in one_sided[:10]:
    print(f"       L{lineno}: {why}")
check(f"every one of the {total} _t() calls carries both halves", not one_sided)
check("the file actually uses _t() widely -- a passing check on zero calls "
      "would prove nothing", total > 200)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
