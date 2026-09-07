#!/usr/bin/env python3
"""Tests for dev/run_all.py's own correctness -- specifically a blind spot
found while wiring it into CI, proven live before being fixed:

A module that fails to even PARSE makes every suite exit non-zero with no
"N/M passed" tally -- the exact same shape run_all.py already treats as a
harmless SKIP ("a missing optional dependency, say"). With a genuinely broken
lite_agent.py in place, 19 of 20 real suites SKIPped with "SyntaxError", the
one suite that never imports the module (test_cli_login.py, which only
touches tools/cli_login.py) reported a clean 15/15, and run_all.py printed
"TOTAL 15/15" at exit code 0 -- green, while the product could not run at
all. That is precisely the failure mode a Python-version CI matrix exists to
catch (this project shipped a real f-string fix after an external review
found lite_agent.py failed to import on 3.10/3.11), so the one case that
matters most could not be allowed to hide behind "SKIP".

Fixed with a compile check up front: `python3 -m py_compile <module>`, run
once before any suite, treated as a hard failure distinct from a per-suite
skip.

Every case here runs run_all.py as a real subprocess against an ISOLATED
scratch dev/ directory containing only a copy of run_all.py plus small fake
test_*.py suites this file controls -- never the real dev/ directory. Running
it against the real one would make the inner run_all.py discover and execute
THIS file again, which would do the same thing again: unbounded recursive
process spawning.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER_SRC = HERE / "run_all.py"

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def isolated_project(module_text: str, fake_suites: dict[str, str]) -> Path:
    """<tmp>/proj/lite_agent.py + <tmp>/proj/dev/{run_all.py, fake suites}.
    Returns the project root. fake_suites maps filename -> full script text."""
    root = Path(tempfile.mkdtemp(prefix="isla_runall_proj_"))
    (root / "lite_agent.py").write_text(module_text, encoding="utf-8")
    dev = root / "dev"
    dev.mkdir()
    shutil.copy(RUNNER_SRC, dev / "run_all.py")
    for name, text in fake_suites.items():
        (dev / name).write_text(text, encoding="utf-8")
    return root


def run_all(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "dev" / "run_all.py"), str(root / "lite_agent.py")],
        capture_output=True, text=True, cwd=str(root),
    )


FAKE_OK = '''import sys
print("PASS - a trivial check")
print("1/1 passed")
'''
BROKEN_MODULE = "def main(: -> None:\n    pass\n"          # a real SyntaxError
VALID_EMPTY_MODULE = "# syntactically valid, nothing useful in it\n"

# --- 1. a module that cannot even parse: hard failure, before any suite ----
root1 = isolated_project(BROKEN_MODULE, {"test_fake.py": FAKE_OK})
proc1 = run_all(root1)
out1 = proc1.stdout + proc1.stderr
shutil.rmtree(root1, ignore_errors=True)

check("a module that fails to compile makes run_all.py exit non-zero "
      "(THE bug: this used to exit 0)", proc1.returncode != 0)
check("...and it says so BEFORE running any suite, not after silently "
      "skipping all of them", "does not even compile" in out1)
check("...and shows the real SyntaxError, not a generic message",
      "SyntaxError" in out1)
check("...the fake suite never actually ran",
      "1/1 passed" not in out1 and "a trivial check" not in out1)
check("...and it does NOT print a misleadingly clean tally the way it did "
      "before the fix (TOTAL 15/15 at exit 0)",
      "TOTAL" not in out1)

# --- 2. a syntactically valid module passes the gate and suites DO run ----
root2 = isolated_project(VALID_EMPTY_MODULE, {"test_fake.py": FAKE_OK})
proc2 = run_all(root2)
out2 = proc2.stdout + proc2.stderr
shutil.rmtree(root2, ignore_errors=True)

check("a syntactically VALID (if useless) module passes the compile gate "
      "-- it checks syntax only, not whether suites find what they need",
      "does not even compile" not in out2)
check("...and the runner proceeds to actually run the suite",
      "1/1" in out2 and "TOTAL" in out2)
check("...reporting success when the (fake) suite passes", proc2.returncode == 0)

# --- 3. a suite that fails still fails the run, same as always ------------
FAKE_FAIL = 'print("FAIL - something real broke")\nprint("0/1 passed")\n'
root3 = isolated_project(VALID_EMPTY_MODULE, {"test_fake.py": FAKE_FAIL})
proc3 = run_all(root3)
shutil.rmtree(root3, ignore_errors=True)
check("a genuinely failing suite still fails the overall run "
      "(the compile-gate fix did not loosen this)", proc3.returncode != 0)

# --- 4. the real module in this repo passes the gate ----------------------
real_src = HERE.parent / "lite_agent.py"
if real_src.exists():
    root4 = isolated_project(real_src.read_text(encoding="utf-8"),
                             {"test_fake.py": FAKE_OK})
    proc4 = run_all(root4)
    out4 = proc4.stdout + proc4.stderr
    shutil.rmtree(root4, ignore_errors=True)
    check("the real lite_agent.py in this repo passes the compile gate",
          "does not even compile" not in out4)
else:
    print("SKIP - real lite_agent.py not found next to dev/ (unexpected layout)")

# --- 5. the symbol index: fatal on a collision, harmless when absent ------
# Both halves matter, and the second one was learned the hard way. Wiring the
# index to fail the run was written as "any non-zero exit from code_index.py",
# which also covers "code_index.py is not there" -- and it is deliberately not
# copied into these isolated projects. Every scenario above went red for a
# reason that had nothing to do with the code under test. So: exit 3 means a
# duplicate name, anything else means the tool did not run.
DUP_MODULE = 'def thing():\n    return 1\n\n\ndef thing():\n    return 2\n'

check("a project WITHOUT code_index.py still passes -- a missing index tool "
      "says nothing about the code and must not fail the run",
      proc2.returncode == 0)

root5 = isolated_project(DUP_MODULE, {"test_fake.py": FAKE_OK})
shutil.copy(HERE / "code_index.py", root5 / "dev" / "code_index.py")
proc5 = run_all(root5)
out5 = proc5.stdout + proc5.stderr
shutil.rmtree(root5, ignore_errors=True)
check("a duplicate top-level name FAILS the run", proc5.returncode != 0)
check("...and names the offender rather than just failing",
      "thing" in out5 and "duplicate" in out5.lower())
check("...and the suites themselves still ran and passed, so the failure is "
      "clearly the collision and not a broken suite", "1/1" in out5)

# The same project with no duplicate must be green, or the check above would
# be indistinguishable from "the index always fails".
root6 = isolated_project(VALID_EMPTY_MODULE, {"test_fake.py": FAKE_OK})
shutil.copy(HERE / "code_index.py", root6 / "dev" / "code_index.py")
proc6 = run_all(root6)
out6 = proc6.stdout + proc6.stderr
shutil.rmtree(root6, ignore_errors=True)
check("...while the same setup with no duplicate passes",
      proc6.returncode == 0)
check("...and the index actually ran and reported, rather than being skipped "
      "into silence the way it was on Linux",
      "0 duplicate" in out6)


# --- 7. a suite that will not PARSE is broken, not skipped ------------------
# Found the hard way: a bulk edit left an IndentationError in a suite, run_all
# filed it in the same bucket as "python-telegram-bot is not installed", and
# the run exited 0. A missing optional dependency is a fact about the machine.
# A SyntaxError is a fact about our own code and must cost the run.
BROKEN_SUITE = "x = 1\n  y = 2\n"          # IndentationError on import

root7 = isolated_project(VALID_EMPTY_MODULE,
                         {"test_fake.py": FAKE_OK,
                          "test_bad.py": BROKEN_SUITE})
proc7 = run_all(root7)
out7 = proc7.stdout + proc7.stderr
shutil.rmtree(root7, ignore_errors=True)
check("a suite that cannot be parsed FAILS the run", proc7.returncode != 0)
check("...and is labelled BROKEN, not SKIP", "BROKEN" in out7)
check("...and is named, so it can be fixed rather than hunted",
      "test_bad.py" in out7)
check("...while the healthy suite beside it still ran", "1/1" in out7)

# A missing dependency must STILL be a skip -- the distinction is the point,
# and a rule that fails everything would pass the checks above for free.
IMPORT_SKIP = ("import no_such_module_anywhere  # noqa\n"
               'print("1/1 passed")\n')
root8 = isolated_project(VALID_EMPTY_MODULE,
                         {"test_fake.py": FAKE_OK,
                          "test_dep.py": IMPORT_SKIP})
proc8 = run_all(root8)
out8 = proc8.stdout + proc8.stderr
shutil.rmtree(root8, ignore_errors=True)
check("a MISSING DEPENDENCY is still only a skip", "SKIP" in out8)
check("...and does not fail the run on its own", proc8.returncode == 0)

# --- 8. skips INSIDE a suite are counted, not swallowed ---------------------
# A suite could drop half its checks on a platform and still print a spotless
# N/N. The tally now carries them and run_all totals them, so the pre-push
# threshold can see them.
PARTIAL = ('print("PASS - one that ran")\n'
           'print("SKIP - 4 check(s): no symlinks on this OS")\n'
           'print("1/1 passed, 4 skipped")\n')
root9 = isolated_project(VALID_EMPTY_MODULE, {"test_partial.py": PARTIAL})
proc9 = run_all(root9)
out9 = proc9.stdout + proc9.stderr
shutil.rmtree(root9, ignore_errors=True)
check("a suite reporting its own skipped checks is still counted as passing",
      proc9.returncode == 0 and "1/1" in out9)
check("...and the skipped checks reach the TOTAL line instead of vanishing",
      "4 check(s) skipped" in out9)


# --- 9. a suite that skips WITHOUT saying so is still counted ---------------
# Three suites had been doing exactly this since they were written: print a
# SKIP note, then a spotless tally. Requiring every author to remember the
# count is the bet that already lost, so the notes themselves are counted.
SILENT = ('print("PASS - one that ran")\n'
          'print("SKIP - POSIX file modes are not meaningful here")\n'
          'print("1/1 passed")\n')
root10 = isolated_project(VALID_EMPTY_MODULE, {"test_silent.py": SILENT})
proc10 = run_all(root10)
out10 = proc10.stdout + proc10.stderr
shutil.rmtree(root10, ignore_errors=True)
check("a skip note with no declared count is still counted",
      "1 check(s) skipped" in out10)
check("...and the note itself is echoed, so the count can be judged",
      "POSIX file modes" in out10)
check("...without the suite being treated as failing", proc10.returncode == 0)

# A declared count must not be added to the notes as well.
BOTH = ('print("PASS - one that ran")\n'
        'print("SKIP - 5 check(s): a whole block")\n'
        'print("1/1 passed, 5 skipped")\n')
root11 = isolated_project(VALID_EMPTY_MODULE, {"test_both.py": BOTH})
proc11 = run_all(root11)
out11 = proc11.stdout + proc11.stderr
shutil.rmtree(root11, ignore_errors=True)
check("a declared count wins over the note count, never both",
      "5 check(s) skipped" in out11 and "6 check(s) skipped" not in out11)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
