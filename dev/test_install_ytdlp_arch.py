#!/usr/bin/env python3
"""install.sh's yt-dlp fetch picks the right binary for the machine's own
architecture, and actually verifies it runs before calling it installed.

Found reading the script, not reported by a user: it always fetched
`yt-dlp_linux` (an x86_64-only standalone binary -- confirmed against the
real GitHub release asset list, not assumed) regardless of host architecture,
and the line that was supposed to report the result was
`ok "yt-dlp $(yt-dlp --version 2>/dev/null || echo installed)"` -- the `||`
swallows an exec-format error on the wrong architecture and prints "installed"
anyway. A real failure was reported as a success.

This runs the actual case-statement logic (extracted, not re-typed by hand)
under a fake `uname`, so the test would have caught the original bug -- a
string search for "yt-dlp" in the file, which is all the existing media-tools
test does, would not have.
"""
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(sys.argv[1]).parent / "install.sh"
text = SRC.read_text(encoding="utf-8")

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


m = re.search(r'case "\$\(uname -m\)" in\n(.*?)\n\s*esac', text, re.DOTALL)
check("the yt-dlp fetch branches on uname -m rather than assuming one "
      "architecture", m is not None)

# Run the extracted case-statement for real, under a handful of `uname -m`
# answers, rather than trusting that the regex above found the right thing.
case_block = m.group(0) if m else ""
for machine, want in (("x86_64", "yt-dlp_linux"),
                      ("amd64", "yt-dlp_linux"),
                      ("aarch64", "yt-dlp_linux_aarch64"),
                      ("arm64", "yt-dlp_linux_aarch64"),
                      ("mips", "")):
    script = f'uname() {{ echo "{machine}"; }}\n{case_block}\necho "$_YTDLP_ASSET"'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         timeout=10).stdout.strip()
    check(f"uname -m={machine!r} picks {want or '(nothing -- unsupported)'!r}",
          out == want)

check("a wrong-architecture download is never reported as installed by an "
      "`||` that swallows the failure",
      # the exact buggy command substitution, not merely the phrase -- this
      # script's own comment now explains the old bug using these words, and
      # a bare substring check would flag its own fix note as the bug.
      '--version 2>/dev/null || echo installed)' not in text)
check("the real gate is capturing `yt-dlp --version`'s own success, not a "
      "fallback string", '_YTDLP_VER="$(yt-dlp --version 2>/dev/null)"' in text)
check("a binary that downloaded but can't run is removed, not left behind "
      "looking installed", "sudo rm -f /usr/local/bin/yt-dlp" in text)
check("an architecture with no known build warns and skips, rather than "
      "fetching a binary that can only fail to run",
      'No prebuilt yt-dlp for this architecture' in text)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
