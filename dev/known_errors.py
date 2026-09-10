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
        "cause": "The probe was `echo IPANDU_OK && uname -sr`, and our own "
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
    {
        "id": "E018",
        "date": "2026-09-06",
        "area": "Add server",
        "symptom": "The operator placed a key on 10.10.59.75, saw the 'Key "
                   "installed and verified' card, tapped the final button, and "
                   "got 'That form expired'. The host never reached /servers.",
        "cause": "The /addserver wizard lived only in memory with a 15-minute "
                 "TTL. The service restarted for the v0.2b.86 /update while the "
                 "wizard was open, wiping it. /unlock already persists its "
                 "state across restarts for this exact reason; the server "
                 "wizard did not.",
        "fix": "The wizard is written to server_wizard.json after every step "
               "and reloaded at startup, dropping any that expired while the "
               "process was down. The password is never part of that state -- "
               "it is a local, del'd the moment bootstrap returns -- so the "
               "persistence adds no secret to disk. File is chmod 600 and "
               "gitignored.",
        "guard": "test_server_wizard_persist.py",
        "release": "v0.2b.87",
    },
    {
        "id": "E019",
        "date": "2026-09-06",
        "area": "Add server",
        "symptom": "18,128 lines of journal and not one mention of "
                   "10.10.59.75, though the wizard had demonstrably connected "
                   "and placed a key there. Diagnosis needed reading "
                   "~/.ssh/known_hosts by hand for a timestamp.",
        "cause": "bootstrap_key_with_password had zero logger calls. Placing a "
                 "credential on a machine -- the single most consequential "
                 "thing the wizard does -- left no trace unless ssh itself "
                 "errored, and even then only in the reply, not the log.",
        "fix": "Five log points: placing the key, a refused password, an ssh "
               "failure, a key that writes but will not authenticate (the "
               ".75 shape -- an appliance that does not persist ~/.ssh), and a "
               "verified success. None ever logs the password.",
        "guard": "test_server_wizard_persist.py",
        "release": "v0.2b.87",
    },
    {
        "id": "E020",
        "date": "2026-09-06",
        "area": "Add server",
        "symptom": "Registering the Bima Kota Proxmox (103.152.36.66) through "
                   "the chat auto-add flow failed at the PIN keypad. The key "
                   "was fine -- agent_write already authenticated to that "
                   "Proxmox; the flow never reached the key step.",
        "cause": "cmd_pin_key calls query.answer() on every digit -- a cosmetic "
                 "ack that only stops the button spinner. On the bscloud agent, "
                 "which has a slow path to Telegram, one ack hit an "
                 "httpx.ReadTimeout, raised telegram.error.TimedOut, and aborted "
                 "the handler mid-entry. The PIN never completed, so the "
                 "registration never ran.",
        "fix": "_safe_answer() wraps query.answer() and swallows TimedOut, "
               "NetworkError and BadRequest (query-too-old) while re-raising "
               "everything else. All 31 answer() call sites route through it, so "
               "a flaky link can drop an ack but can no longer take a handler "
               "down with it.",
        "guard": "test_safe_answer.py",
        "release": "v0.2b.88",
    },
    {
        "id": "E021",
        "date": "2026-09-07",
        "area": "Voice input",
        "symptom": "On the bscloud agent, a voice message got no response. Text "
                   "worked, and the bot could even reply with voice (Gemini "
                   "generates the audio), but speaking to it did nothing.",
        "cause": "The message handler registered TEXT, PHOTO and Document only. "
                 "A voice note (or audio file) matched no handler and was "
                 "dropped silently -- the worst failure shape here, since the "
                 "sender has no idea it arrived.",
        "fix": "VOICE and AUDIO are registered too. A voice message is saved, "
               "converted to 16 kHz mono WAV, and its path named in the prompt "
               "the way an image already is; agy (Gemini) decodes the audio "
               "natively -- no STT service, no API key, no new dependency. "
               "Because Claude cannot hear, a voice turn is pinned to an agy "
               "tier, and only a voice turn is. Verified end to end on the "
               "bscloud host: a spoken 'restart the web server on node three "
               "and check disk usage' was transcribed and acted on.",
        "guard": "test_voice_input.py",
        "release": "v0.2b.89",
    },
    {
        "id": "E022",
        "date": "2026-09-07",
        "area": "Document input",
        "symptom": "On the itbutler agent, sending a PDF or an HTML file got "
                   "'I can read images, but not this kind of attachment'. The "
                   "file was never read.",
        "cause": "Images had their own handler and audio had just gotten one, "
                 "but every other document fell through to a refusal branch and "
                 "was never saved or passed to a model -- despite both CLIs "
                 "being perfectly able to read a file from a path.",
        "fix": "_save_incoming_document downloads any document (keeping its "
               "extension) and names its path in the prompt, the way an image "
               "already is; whichever tier answers reads it -- no forced model, "
               "no per-format extraction, no new dependency. Verified live on "
               "itbutler: agy AND claude each read a test PDF and a test HTML "
               "and returned the marker string inside. The old refusal now only "
               "fires on a genuine download failure, and says so honestly.",
        "guard": "test_document_input.py",
        "release": "v0.2b.90",
    },
    {
        "id": "E023",
        "date": "2026-09-07",
        "area": "Add server",
        "symptom": "'tolong carikan 2 ip dari subnet 10.10.59.0/24' got "
                   "'10.10.59.0 belum ada di inventaris -- daftarkan?'. A "
                   "create-VM prompt did the same with its gateway and DNS, and "
                   "that offer hijacked the whole task.",
        "cause": "unregistered_hosts_in swept every IPv4 in the text and treated "
                 "any it did not know as a host to register -- including a "
                 "subnet's network address (the .0 in 10.10.59.0/24), a "
                 "broadcast address, a gateway, and a DNS server.",
        "fix": "It now walks matches with position and excludes CIDR notation "
               "(IP followed by /digits), network/broadcast addresses (last "
               "octet 0 or 255), and IPs introduced by gateway/DNS/subnet/"
               "netmask/nameserver words just before them. A genuine unknown "
               "host is still detected.",
        "guard": "test_subnet_and_pw_consent.py",
        "release": "v0.2b.91",
    },
    {
        "id": "E024",
        "date": "2026-09-07",
        "area": "Credential safety",
        "symptom": "A create-VM prompt that carried the VM's own credential had "
                   "its ENTIRE message deleted the instant a password was seen, "
                   "without asking -- and the task was then dropped and never "
                   "ran.",
        "cause": "The credential guard called msg.delete() automatically and "
                 "returned, on the assumption a typed password is always an "
                 "accident. On a message that IS the task, both were wrong: it "
                 "destroyed the operator's whole prompt and refused to do the "
                 "work.",
        "fix": "No auto-delete. The warning now offers a '🗑 Delete the message' "
               "button (cmd_pwdelete_button) so removal is the operator's "
               "choice, and the turn is no longer dropped -- the task runs, its "
               "credential reaching the model because the task needs it, with "
               "the PIN still gating the writes.",
        "guard": "test_subnet_and_pw_consent.py",
        "release": "v0.2b.91",
    },
    {
        "id": "E025",
        "date": "2026-09-08",
        "area": "Add server",
        "symptom": "The operator had the bot clone two VMs on Proxmox, entirely "
                   "through chat. Once they existed and were reachable, getting "
                   "them into /servers still meant re-typing name/host/user/port "
                   "into the manual wizard, one message at a time, for "
                   "information the model already had and had just reported.",
        "cause": "v0.2b.91 fixed the input side of this (a subnet is not a "
                 "server, a password is not deleted unasked) and named what was "
                 "left undone: post-execution auto-registration. There was no "
                 "path from the model just finishing provisioning a host to "
                 "/servers except the same wizard built for a human typing one "
                 "field at a time.",
        "fix": "The model ends a reply with one SERVER: name=|host=|user=|port= "
               "line per finished host -- taught in CAPABILITIES_BRIEF, so it "
               "reaches Claude and agy the same way every other capability "
               "does, on every /update. Each proposal becomes a card, a "
               "hypervisor/VM choice, and the same PIN /addserver has always "
               "required -- no re-typing. The write itself (_register_server) "
               "is shared with the manual wizard's own last step rather than "
               "duplicated, lifted out of the old _finish_addserver.",
        "guard": "test_server_autoregister.py",
        "release": "v0.2b.92",
    },
    {
        "id": "E026",
        "date": "2026-09-08",
        "area": "Telegram delivery",
        "symptom": "cmd_update_button crashed with an unhandled "
                   "telegram.error.BadRequest: Message is not modified, on the "
                   "bscloud agent, one minute before an unrelated /update "
                   "restarted the process.",
        "cause": "A double-tap on the same inline button (or Telegram "
                 "redelivering the same callback) ran the handler twice. The "
                 "first call edited the message to the confirm-with-PIN text; "
                 "the second tried to edit it to the exact same text again, "
                 "and Telegram refuses an edit whose content and reply_markup "
                 "are byte-identical to what is already displayed. _safe_answer "
                 "(E020) had already made the ANSWER half of a button tap "
                 "fault-tolerant; the EDIT half was not.",
        "fix": "_safe_edit wraps query.edit_message_text and swallows only "
               "BadRequest whose message says 'not modified' -- a message or "
               "chat genuinely gone still raises, so a real problem is never "
               "hidden. All 90 query.edit_message_text( call sites route "
               "through it.",
        "guard": "test_safe_edit.py",
        "release": "v0.2b.93",
    },
]
