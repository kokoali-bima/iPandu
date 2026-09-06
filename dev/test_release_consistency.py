#!/usr/bin/env python3
"""The version the bot ANNOUNCES must be the version the docs claim.

Written after shipping v0.2b.71 and having the operator ask why /update still
said v0.2b.70. Everything about the release was correct except one invisible
step: the commit was pushed, README and CHANGELOG both said v0.2b.71, and no
git tag was ever created. `current_version()` is `git describe --tags`, so the
bot kept reporting the previous release while running the new code. Nothing
was broken -- the Drive fix was live -- but the operator had no way to tell,
and "did my update work?" is not a question anyone should have to investigate.

The three places a version lives, and how they can disagree:

  README.md status line   -- what a reader is told
  CHANGELOG.md top heading -- what the release notes call it
  git tag                  -- the ONLY one the running bot actually reads

The gate below is deliberately narrow, because a check that fires during
ordinary development gets ignored, and an ignored check is worse than none.
It fails only in states that are genuinely wrong:

  * README and CHANGELOG disagree with each other -- always a mistake.
  * The release notes for version X are already COMMITTED, and no tag X
    exists. That is exactly the shipped-without-a-tag state, and it cannot
    happen mid-development, when the CHANGELOG edit is still unstaged.

Writing notes for the next version locally, before tagging, stays green.
"""
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(sys.argv[1]).resolve()
REPO = SRC.parent

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(("PASS - " if ok else "FAIL - ") + name)


def git(*args: str) -> tuple[bool, str]:
    try:
        # encoding is explicit, not the platform default. Without it this
        # decodes as cp1252 on Windows, and the first non-Latin-1 character in
        # the CHANGELOG -- an em dash is enough -- makes `git show` come back
        # as None. The tag check then failed on a release that was perfectly
        # fine, on the machine where it is run before pushing, while passing on
        # Linux. A gate that is wrong on the machine you check from is worse
        # than no gate: it teaches you to push past it.
        p = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30)
        return p.returncode == 0, (p.stdout or "").strip()
    except Exception:
        return False, ""


VERSION_RE = re.compile(r"v(\d+\.\d+[A-Za-z]*\.\d+)")


def changelog_version(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("## "):
            m = VERSION_RE.search(line)
            return m.group(1) if m else ""
    return ""


def readme_version(text: str) -> str:
    m = re.search(r"Status:\s*v(\d+\.\d+[A-Za-z]*\.\d+)", text)
    return m.group(1) if m else ""


changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
readme = (REPO / "README.md").read_text(encoding="utf-8")

declared = changelog_version(changelog)
shown = readme_version(readme)

# Committed while still holding merge conflict markers. This happened for real
# in the iPandu fork: a resolution script died on a Windows encoding error
# before it wrote the file, and the `git add -A && git commit` that followed ran
# anyway. Nothing caught it -- the version check below still parsed fine, with
# `<<<<<<< HEAD` sitting directly above the heading it was reading.
for doc, body in (("CHANGELOG.md", changelog), ("README.md", readme)):
    markers = [mk for mk in ("<<<<<<< ", ">>>>>>> ") if mk in body]
    check(f"{doc} carries no unresolved merge conflict", not markers)

check("CHANGELOG.md has a parseable version heading", bool(declared))
check("README.md has a parseable status version", bool(shown))
check(f"README and CHANGELOG agree (README={shown or '?'}, "
      f"CHANGELOG={declared or '?'})", bool(declared) and declared == shown)

is_repo, _ = git("rev-parse", "--git-dir")
if not is_repo:
    print("\nnot a git checkout -- tag checks skipped")
else:
    # What the CHANGELOG said at the LAST COMMIT, not in the working tree.
    # That distinction is the whole point: an unstaged bump is work in
    # progress, a committed one is a release that needs a tag.
    ok_show, committed_changelog = git("show", "HEAD:CHANGELOG.md")
    committed_declared = changelog_version(committed_changelog) if ok_show else ""

    ok_tag, _ = git("rev-parse", "--verify", f"refs/tags/v{declared}")

    notes_are_committed = bool(declared) and committed_declared == declared
    check(f"release notes for v{declared} are tagged, or not yet committed "
          f"(committed={notes_are_committed}, tag={ok_tag})",
          ok_tag or not notes_are_committed)

    # The bot reports git describe verbatim, so this is literally what an
    # operator sees after /update.
    ok_desc, described = git("describe", "--tags", "--always")
    ok_head, head_sha = git("rev-parse", "HEAD")
    ok_tsha, tag_sha = git("rev-list", "-n", "1", f"v{declared}")
    at_the_release = ok_head and ok_tsha and head_sha == tag_sha

    if ok_desc and notes_are_committed and ok_tag and at_the_release:
        check(f"git describe would announce v{declared} (says: {described})",
              described == f"v{declared}")
    elif ok_desc and notes_are_committed and ok_tag:
        # HEAD has moved past the release. That is ordinary development, and
        # demanding an exact match here is what forced every single commit in
        # this repository to be a release -- 86 tags across 123 commits, and
        # not one pull request, because an unreleased commit failed the suite.
        # What still has to hold is that the work sits ON TOP of the declared
        # version rather than beside it or behind it.
        check(f"unreleased work sits on top of v{declared} ({described})",
              described.startswith(f"v{declared}-"))
    else:
        print(f"INFO  - git describe currently says: {described or '(none)'}")

    # A tag must never point somewhere that predates its own release notes.
    if ok_tag:
        ok_at, at_tag = git("show", f"v{declared}:CHANGELOG.md")
        check(f"the v{declared} tag points at a commit whose CHANGELOG "
              f"already announces it",
              ok_at and changelog_version(at_tag) == declared)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
