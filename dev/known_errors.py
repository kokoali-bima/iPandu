#!/usr/bin/env python3
"""Every failure this project has actually shipped, and what stops it recurring.

This is a REGISTRY, not prose. Each entry names the guard test that would fail
if the bug came back, and `error_index.py` refuses to pass when an entry points
at a test that no longer exists -- because a regression quietly losing its guard
is how the same bug ships twice.

Rules for adding an entry:
  * Only failures that REACHED a user or a production host. Bugs caught in
    development are ordinary work, not history worth carrying.
  * `symptom` is what was actually seen -- the log line, the message, the
    silence. Not the diagnosis.
  * `guard` names a real file in dev/. If a bug genuinely cannot be tested,
    say so in `guard` as "none: <why>" and it will be listed as unguarded.
"""

ERRORS = [
    {
        "id": "E001",
        "date": "2026-09-04",
        "area": "Google Drive",
        "symptom": "⚠️ Upload ke Drive gagal ... timed out talking to "
                   "Google Drive -- but the report was already in Drive.",
        "cause": "_gdrive_upload ran `rclone copyto` and `rclone link` inside one "
                 "try block, so a slow share-link call reported the upload as "
                 "failed. Measured: link normally ~5s, seen at 43.3s against a "
                 "30s ceiling, roughly one run in five.",
        "fix": "Split the two calls. Once copyto returns the file is in Drive and "
               "the upload is reported successful whatever the link call does.",
        "guard": "test_gdrive_rclone_auth.py",
        "release": "v0.2b.71",
    },
    {
        "id": "E002",
        "date": "2026-09-04",
        "area": "Release process",
        "symptom": "/update reported v0.2b.70 after v0.2b.71 was pushed and running.",
        "cause": "current_version() is `git describe --tags`, and the release was "
                 "committed and pushed without ever creating the tag.",
        "fix": "dev/test_release_consistency.py fails when release notes are "
               "committed with no matching tag.",
        "guard": "test_release_consistency.py",
        "release": "v0.2b.72",
    },
    {
        "id": "E003",
        "date": "2026-09-04",
        "area": "Dev tooling",
        "symptom": "The symbol index reported nothing on Linux, where the full "
                   "suite actually runs.",
        "cause": "Its output directory is a Windows path; on Linux it took the "
                 "'unusable path' branch and skipped itself entirely, so the "
                 "duplicate-name check it exists for did nothing.",
        "fix": "Falls back into the checkout instead of skipping, run_all prints "
               "its output, and a duplicate top-level name fails the run.",
        "guard": "test_run_all.py",
        "release": "v0.2b.72",
    },
    {
        "id": "E004",
        "date": "2026-09-04",
        "area": "Model brief",
        "symptom": "Asked for a video clip, the bot answered that it \"can only "
                   "send local media files stored on disk\" and offered links.",
        "cause": "GEMINI.md/SOUL.md are written once by install.sh and never "
                 "refreshed by /update, so every capability shipped after the "
                 "install date was invisible. Measured on the live host: "
                 "GDRIVE_DELETE, GDRIVE_MOVE, yt-dlp and ffmpeg all appeared "
                 "zero times.",
        "fix": "CAPABILITIES_BRIEF lives in code and is injected at conversation "
               "start, so it ships with /update.",
        "guard": "test_capabilities_brief.py",
        "release": "v0.2b.73",
    },
    {
        "id": "E005",
        "date": "2026-09-04",
        "area": "Cost control",
        "symptom": "One conversation consumed 46.4% of a week's tokens -- 56.7M "
                   "over 11 turns -- with a single cost warning at the start.",
        "cause": "The expensive-conversation hint fired once per session and then "
                 "stayed silent through the entire expensive part.",
        "fix": "cost_hint_due() warns again on every doubling of the per-turn cost.",
        "guard": "test_cost_hint.py",
        "release": "v0.2b.74",
    },
    {
        "id": "E006",
        "date": "2026-09-04",
        "area": "Media",
        "symptom": "A 32MB AV1 clip re-encoded to H.264 came back at 67MB and was "
                   "still treated as a shrink.",
        "cause": "shrink_video_to_fit compared its output only against Telegram's "
                 "50MB ceiling, never against the file it was given.",
        "fix": "Refuse any result that is not actually smaller than the input.",
        "guard": "test_cost_hint.py",
        "release": "v0.2b.74",
    },
    {
        "id": "E007",
        "date": "2026-09-05",
        "area": "Write gate",
        "symptom": "After entering the PIN, the bot asked for the PIN again, then "
                   "crashed: AttributeError 'NoneType' object has no attribute "
                   "'reply_text' in offer_unlock. 14 unlocks in one day.",
        "cause": "Two faults compounding. The write window (10 min in a group) "
                 "expired while the turn it was opened for was still running -- "
                 "unlocked 23:06:50, turn finished 23:25:01, eighteen minutes, "
                 "because agy failed over mid-turn. The end-of-turn check then "
                 "saw a closed window and re-offered the unlock; offer_unlock "
                 "used update.message, which is always None inside a button "
                 "callback, so the whole turn died after doing the work.",
        "fix": "Default window 30 min and ceiling 6h; the end-of-turn re-offer is "
               "suppressed when the window was open at turn start; offer_unlock "
               "goes through _msg().",
        "guard": "test_unlock_window.py",
        "release": "v0.2b.75",
    },
    {
        "id": "E008",
        "date": "2026-09-05",
        "area": "Telegram plumbing",
        "symptom": "delivery to Telegram failed (attempt 1/2 and 2/2), then "
                   "\"even the failure notice couldn't be delivered\".",
        "cause": "_msg() handled typed messages and button callbacks but not "
                 "EDITED messages, where both update.message and "
                 "update.callback_query are None. _authorized() lets edited "
                 "messages through on purpose, so the turn ran and then had "
                 "nowhere to reply.",
        "fix": "_msg() falls back to update.effective_message.",
        "guard": "test_reply_target.py",
        "release": "v0.2b.75",
    },
    {
        "id": "E009",
        "date": "2026-09-05",
        "area": "Servers",
        "symptom": "/addserver failed repeatedly for the Kota Bima Proxmox with "
                   "\"pve-ro-guard: refused -- this key is read-only\", although "
                   "the SSH key was installed correctly.",
        "cause": "The probe was `echo ISMART_OK && uname -sr`, and our own "
                 "read-only guard denies any `&` before it looks at the verbs. "
                 "Reproduced on both hosts. The one server that ever registered "
                 "got in before the guard was installed.",
        "fix": "Probe with a bare `uname -sr`, which needs no sentinel and no "
               "shell operators.",
        "guard": "test_addserver_probe.py",
        "release": "v0.2b.75",
    },
    {
        "id": "E010",
        "date": "2026-09-05",
        "area": "Google Drive",
        "symptom": "CRITICAL: Failed to create file system ... googleapi: Error "
                   "403: Quota exceeded for quota metric 'Queries'.",
        "cause": "rclone's shared Google client_id is used by everyone who never "
                 "made their own, so its project-wide query quota runs out. "
                 "Nothing wrong with the account, file or config -- the same "
                 "remote listed fine five hours later.",
        "fix": "Detect the 403 quota shape and say it is temporary and "
               "self-clearing, instead of pasting rclone's CRITICAL line.",
        "guard": "test_gdrive_rclone_auth.py",
        "release": "v0.2b.75",
    },
    {
        "id": "E011",
        "date": "2026-09-06",
        "area": "Model brief",
        "symptom": "Registering the dk-solutions Proxmox went fine on Gemini and "
                   "was painful on Sonnet -- ten turns of going in circles.",
        "cause": "CAPABILITIES_BRIEF covered media and Drive but said nothing "
                 "about the write gate or /addserver. Across 36 hours of logs "
                 "there were only two write-mode mentions: Sonnet never emitted "
                 "NEEDS_WRITE at all. It hit `pve-ro-guard: refused`, read it as "
                 "a fault to work around, and never asked for the PIN. Not a "
                 "crash -- zero application errors in that window.",
        "fix": "The brief now explains the read-only guard, the NEEDS_WRITE line "
               "that asks for access, and that adding a server means telling the "
               "operator to run /addserver rather than improvising.",
        "guard": "test_capabilities_brief.py",
        "release": "v0.2b.77",
    },
    {
        "id": "E012",
        "date": "2026-09-06",
        "area": "Dev tooling",
        "symptom": "run_all reported test_newhost_offer.py at 25/25 while running "
                   "it directly gave 41/41. Sixteen checks were uncounted.",
        "cause": "The suite had grown a SECOND summary block when tests were "
                 "appended after an existing one, and run_all read the FIRST "
                 "'N/M passed' line it found. Worse than miscounting: had a "
                 "check above that first block failed, its sys.exit(1) would "
                 "have stopped the file before the later tests ran at all.",
        "fix": "run_all takes the LAST tally, which is what 'the result' means; "
               "any earlier line is a partial. Duplicate summary removed.",
        "guard": "test_run_all.py",
        "release": "v0.2b.79",
    },
    {
        "id": "E013",
        "date": "2026-09-06",
        "area": "Dev tooling",
        "symptom": "C:/Users/muali/.ssh/ held agent_readonly and agent_write, "
                   "dated three days earlier. No test was supposed to be able "
                   "to write there.",
        "cause": "Every suite redirects HOME to a scratch directory, but "
                 "Path.home() does not read HOME on Windows -- it reads "
                 "USERPROFILE. So 28 suites ran against the operator's real "
                 "home, and the two suites that noticed were reported as "
                 "platform skips rather than as the warning they were.",
        "fix": "Every suite now pins USERPROFILE alongside HOME. "
               "test_suite_hygiene.py fails if any suite sets one without the "
               "other, and also refuses read_text/write_text with no encoding, "
               "which is cp1252 on Windows and could not read what the product "
               "had just written as UTF-8.",
        "guard": "test_suite_hygiene.py",
        "release": "v0.2b.85",
    },
    {
        "id": "E014",
        "date": "2026-09-06",
        "area": "Dev tooling",
        "symptom": "run_all printed a green TOTAL and exited 0 with a suite in "
                   "the tree that could not be parsed at all.",
        "cause": "Any suite that exited non-zero without printing a tally was "
                 "filed as SKIP. That bucket is meant for 'this machine lacks "
                 "an optional dependency'; an IndentationError in our own file "
                 "landed in it too and therefore cost nothing.",
        "fix": "A SyntaxError, IndentationError or TabError is now reported as "
               "BROKEN and fails the run. A missing dependency is still a skip "
               "-- both directions are guarded, because a rule that failed "
               "everything would pass the first check for free.",
        "guard": "test_run_all.py",
        "release": "v0.2b.85",
    },
    {
        "id": "E015",
        "date": "2026-09-06",
        "area": "Dev tooling",
        "symptom": "test_node_guard.py reported 18/18 on Windows while three "
                   "of its checks never ran.",
        "cause": "Skips inside a suite were invisible. A suite that gated "
                 "checks on a platform capability still printed a spotless "
                 "N/N, so the pre-push hook -- which was written precisely to "
                 "refuse runs that prove nothing -- had nothing to threshold. "
                 "The first fix for this was worse: gating the whole block "
                 "dropped eleven checks that were perfectly able to run.",
        "fix": "Suites report 'N/M passed, K skipped'; run_all totals K and "
               "prints it; the hook refuses above ISLA_MAX_SKIP_CHECKS. Gates "
               "are placed per check, never per block.",
        "guard": "test_run_all.py",
        "release": "v0.2b.85",
    },
    {
        "id": "E016",
        "date": "2026-09-06",
        "area": "Release process",
        "symptom": "CI failed on all four Python versions for v0.2b.85 -- the "
                   "release whose own notes were about false green runs. The "
                   "release itself was fine; a re-run went green untouched.",
        "cause": "`git push origin master` fired the workflow, and "
                 "`git push origin v0.2b.85` followed seconds later. CI checks "
                 "out the commit and runs `git describe`, which cannot see a "
                 "tag still sitting on the developer's machine, so "
                 "test_release_consistency reported exactly the v0.2b.71 "
                 "defect it exists to catch. Pushing the tag did not trigger a "
                 "new run, so the red stayed.",
        "fix": "pre-push reads the refs git names on stdin. If the committed "
               "CHANGELOG announces a version whose tag exists locally but is "
               "neither on the remote nor in this push, it refuses and prints "
               "`git push origin HEAD <tag>`. Ordering is no longer something "
               "anyone has to remember.",
        "guard": "test_release_push_guard.py",
        "release": "v0.2b.86",
    },
    {
        "id": "E017",
        "date": "2026-09-06",
        "area": "Release process",
        "symptom": "86 tags across 123 commits, and not one pull request in "
                   "the repository's history.",
        "cause": "test_release_consistency required `git describe` to equal the "
                 "declared version exactly. That is only true AT the tagged "
                 "commit -- one commit later it reads v0.2b.85-1-gabc123 and "
                 "the suite went red. So every commit had to be a release, and "
                 "a branch carrying unreleased work could never be green. The "
                 "absence of pull requests was not a habit; it was enforced.",
        "fix": "Exactness is demanded only when HEAD IS the tagged commit. "
               "Beyond it, the check becomes 'unreleased work sits on top of "
               "the declared version', which still catches a wrong or missing "
               "tag. The guard that matters -- release notes committed with no "
               "tag -- is a separate check and was not touched.",
        "guard": "test_release_consistency.py",
        "release": "v0.2b.86",
    },
]
