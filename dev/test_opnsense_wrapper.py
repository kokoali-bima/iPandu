#!/usr/bin/env python3
"""Tests for tools/opn -- OPNsense API calls, with writes tied to /unlock.

The operator chose ONE API key for both reading and writing, and they were
right to: OPNsense grants privileges per PAGE, not per verb, so a key that can
read the firewall page can post to it too. A second "read-only" key would be a
second credential to rotate for a boundary OPNsense cannot enforce anyway.

That decision moves the boundary into this wrapper, which is why these tests
matter more than usual. They check the three things it exists for:

  1. Reads never ask permission. Most of what this bot is asked about a
     firewall is a read, and gating those would be friction with nothing
     behind it.
  2. A write is refused unless write mode is OPEN -- checked against the very
     file /unlock writes, expiry and all, not a copy of the rule.
  3. A write is never made without a rollback point. If the config backup
     cannot be downloaded, the change does not happen: "the backup failed" is
     precisely when having one matters.

Every case runs the real script against a real fixture, with `curl` stubbed on
PATH so nothing reaches a firewall.
"""
import atexit
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

SRC = pathlib.Path(sys.argv[1]).resolve()
ROOT = SRC.parent
OPN = ROOT / "tools" / "opn"

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


check("tools/opn exists", OPN.is_file())
if not OPN.is_file():
    print("\n0/1 passed")
    print("FAILED: ['tools/opn exists']")
    print("the wrapper is absent from this source -- every check below needs it")
    sys.exit(1)

BASH = shutil.which("bash")
if not BASH:
    print("SKIP - bash unavailable, the wrapper cannot be exercised here")
    print("\n0/0 passed")
    sys.exit(0)

syntax = subprocess.run([BASH, "-n", str(OPN)], capture_output=True, text=True)
check("the wrapper is syntactically valid", syntax.returncode == 0)
if syntax.returncode != 0:
    print(syntax.stderr[:400])

TMP = pathlib.Path(tempfile.mkdtemp(prefix="isla_opn_"))
atexit.register(shutil.rmtree, str(TMP), ignore_errors=True)


def bashpath(p):
    """Git Bash wants /c/... rather than C:\\..., and silently misbehaves
    when handed the latter."""
    p = str(p)
    if os.name == "nt":
        p = p.replace("\\", "/")
        if len(p) > 1 and p[1] == ":":
            p = "/" + p[0].lower() + p[2:]
    return p


def make_env(until=None, curl_ok=True):
    """A deployment tree: config dir, BASE_DIR, and a stub curl on PATH.

    `until` is what write_mode.json says, i.e. what /unlock would have left
    behind -- None writes no file at all, the locked default.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="isla_opnenv_", dir=TMP))
    conf, base, binp = root / "conf", root / "deploy", root / "bin"
    for d in (conf, base, binp):
        d.mkdir(parents=True)
    (conf / "api.key").write_text("KEYID:SECRET\n", encoding="utf-8")
    (conf / "base_url").write_text("https://10.0.0.1:1945\n", encoding="utf-8")
    if until is not None:
        (base / "write_mode.json").write_text(json.dumps({"until": until}), encoding="utf-8")
    # Stub curl: records its arguments, and writes a plausible body for the
    # backup download so the rollback step can be made to succeed or fail on
    # demand.
    (binp / "curl").write_text(
        "#!/bin/bash\n"
        f'echo "$@" >> "{bashpath(root)}/curl.log"\n'
        + ("for a in \"$@\"; do [ \"$prev\" = -o ] && printf '<opnsense/>' > \"$a\"; prev=\"$a\"; done\n"
           if curl_ok else
           "for a in \"$@\"; do [ \"$prev\" = -o ] && : > \"$a\"; prev=\"$a\"; done\nexit 22\n")
        + "exit 0\n",
        encoding="utf-8",
    )
    (binp / "curl").chmod(0o755)
    return root, conf, base, binp


def run(root, conf, base, binp, *args):
    env = dict(os.environ)
    env["PATH"] = bashpath(binp) + os.pathsep + env.get("PATH", "")
    env["OPN_CONF_DIR"] = bashpath(conf)
    env["OPN_BASE_DIR"] = bashpath(base)
    env["HOME"] = bashpath(root)
    return subprocess.run([BASH, str(OPN), *args],
                          capture_output=True, text=True, timeout=60, env=env)


FUTURE = time.time() + 600
PAST = time.time() - 600

# --- 1. reads never ask permission -----------------------------------------
root, conf, base, binp = make_env(until=None)          # write mode CLOSED
r = run(root, conf, base, binp, "/api/core/firmware/status")
check("a plain read works with write mode closed", r.returncode == 0)
check("...and it actually reached curl", (root / "curl.log").exists())
# The WHOLE url, scheme included. Asserting only "host:port/path" is what let a
# real bug through: base_url was cleaned with `tr -d '\r\n/'`, which deletes
# every slash rather than a trailing one, so https://host:1945 became
# https:host:1945 -- still containing "host:1945/api/..." and still passing,
# while curl silently produced nothing under -s.
check("...against the configured base URL and path, scheme intact",
      "https://10.0.0.1:1945/api/core/firmware/status" in (root / "curl.log").read_text(encoding="utf-8"))
check("...with no config backup taken, since nothing is being changed",
      not (base / "opnsense-backups").exists())

r = run(root, conf, base, binp, "/api/x", "-X", "GET")
check("an explicit -X GET is still a read, not a write", r.returncode == 0)

# Only ONE trailing slash is stripped, and only from the end.
root, conf, base, binp = make_env(until=None)
(conf / "base_url").write_text("https://10.0.0.1:1945/\n", encoding="utf-8")
run(root, conf, base, binp, "/api/core/firmware/status")
check("a trailing slash in base_url is stripped without mangling the scheme",
      "https://10.0.0.1:1945/api/core/firmware/status" in (root / "curl.log").read_text(encoding="utf-8"))

root, conf, base, binp = make_env(until=None)
(conf / "base_url").write_text("172.16.10.20:1945\n", encoding="utf-8")
r = run(root, conf, base, binp, "/api/core/firmware/status")
check("a base_url with no scheme is refused rather than quietly failing in curl",
      r.returncode != 0 and "http" in (r.stdout + r.stderr))

# --- 2. writes are refused while write mode is closed ----------------------
for args, why in (
    (["-X", "POST"], "-X POST"),
    (["-d", "{}"], "-d"),
    (["--data-raw", "{}"], "--data-raw"),
    (["-F", "f=@x"], "-F (multipart)"),
    (["--data=@x"], "--data= in one token"),
):
    root, conf, base, binp = make_env(until=None)
    r = run(root, conf, base, binp, "/api/firewall/filter/addRule", *args)
    check(f"a write via {why} is refused while locked", r.returncode != 0)
    check(f"...and nothing reached curl ({why})", not (root / "curl.log").exists())

root, conf, base, binp = make_env(until=None)
r = run(root, conf, base, binp, "/api/firewall/filter/addRule", "-X", "POST")
out = r.stdout + r.stderr
check("the refusal names /unlock, so the operator can act on it",
      "/unlock" in out)
check("...and says reads still work, so nobody unlocks just to look",
      "read" in out.lower())

# An expired window is closed. The expiry is the whole point of /unlock being
# time-boxed; honouring only the file's existence would silently make it
# permanent.
root, conf, base, binp = make_env(until=PAST)
r = run(root, conf, base, binp, "/api/firewall/filter/addRule", "-X", "POST")
check("an EXPIRED write-mode window is treated as closed", r.returncode != 0)

# --- 3. writes work once write mode is open, and take a rollback point -----
root, conf, base, binp = make_env(until=FUTURE)
r = run(root, conf, base, binp, "/api/firewall/filter/addRule", "-X", "POST", "-d", "{}")
log = (root / "curl.log").read_text(encoding="utf-8") if (root / "curl.log").exists() else ""
check("a write goes through once write mode is open", r.returncode == 0)
backups = sorted((base / "opnsense-backups").glob("config-*.xml")) \
    if (base / "opnsense-backups").exists() else []
check("...and a config backup was downloaded FIRST", len(backups) == 1)
check("...from the backup endpoint, not guessed at",
      "/api/core/backup/download/this" in log)
check("...saved with content, not an empty file",
      bool(backups) and backups[0].stat().st_size > 0)
check("...and the real call still happened after it",
      "/api/firewall/filter/addRule" in log)
check("the call is recorded in the deployment's own log",
      (base / "opnsense.log").exists()
      and "WRITE" in (base / "opnsense.log").read_text(encoding="utf-8"))

# One backup per unlock window, not one per call: a session of ten changes
# should leave one rollback point, taken before the first of them.
r2 = run(root, conf, base, binp, "/api/firewall/alias/set", "-X", "POST", "-d", "{}")
backups2 = sorted((base / "opnsense-backups").glob("config-*.xml"))
check("a second write in the same window reuses that rollback point",
      r2.returncode == 0 and len(backups2) == 1)

# --- 4. no rollback point, no write ----------------------------------------
root, conf, base, binp = make_env(until=FUTURE, curl_ok=False)
r = run(root, conf, base, binp, "/api/firewall/filter/addRule", "-X", "POST", "-d", "{}")
log = (root / "curl.log").read_text(encoding="utf-8") if (root / "curl.log").exists() else ""
check("when the backup download fails, the write is REFUSED", r.returncode != 0)
check("...and the firewall call was never made",
      "/api/firewall/filter/addRule" not in log)
check("...and the empty backup file is not left behind pretending to be one",
      not list((base / "opnsense-backups").glob("config-*.xml")))
check("...with a message that says what to do instead",
      "backup" in (r.stdout + r.stderr).lower())

# --- 5. refuses to be pointed somewhere it should not be -------------------
root, conf, base, binp = make_env(until=FUTURE)
check("a path outside /api/ is refused",
      run(root, conf, base, binp, "/ui/something").returncode != 0)
check("no arguments at all prints usage",
      "usage" in (lambda p: p.stdout + p.stderr)(run(root, conf, base, binp)).lower())

root, conf, base, binp = make_env(until=FUTURE)
(conf / "api.key").unlink()
r = run(root, conf, base, binp, "/api/core/firmware/status")
check("a missing API key is reported, not silently ignored",
      r.returncode != 0 and "api.key" in (r.stdout + r.stderr))

# --- 6. the agent is told about this only where it exists ------------------
# A deployment without the credentials must not hear about the tool: the model
# would offer it, the operator would ask, and the failure would surface several
# turns later as a confusing error rather than "not set up here".
import importlib.util  # noqa: E402  (deliberately after the subprocess cases)

HOME2 = pathlib.Path(tempfile.mkdtemp(prefix="isla_opnbrief_"))
atexit.register(shutil.rmtree, str(HOME2), ignore_errors=True)
os.environ["HOME"] = str(HOME2)
# Path.home() reads USERPROFILE on Windows, HOME on POSIX. Set both, or this
# picks up the developer's real home directory.
os.environ["USERPROFILE"] = str(HOME2)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""
sys.path.insert(0, str(ROOT / "tools"))

spec = importlib.util.spec_from_file_location("la_opn", str(SRC))
mod = importlib.util.module_from_spec(spec)
sys.modules["la_opn"] = mod
spec.loader.exec_module(mod)

check("capabilities_brief() exists", callable(getattr(mod, "capabilities_brief", None)))
if callable(getattr(mod, "capabilities_brief", None)):
    unconfigured = mod.capabilities_brief()
    check("an unconfigured deployment is NOT told about the OPNsense tool",
          "tools/opn" not in unconfigured)
    check("...but still gets the rest of the brief",
          unconfigured.startswith("[What you can do here"))

    conf = mod.OPNSENSE_CONF_DIR
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "api.key").write_text("K:S\n", encoding="utf-8")
    (conf / "base_url").write_text("https://x:1945\n", encoding="utf-8")
    configured = mod.capabilities_brief()
    check("once the credentials exist, the tool is described", "tools/opn" in configured)
    check("...and the model is pointed at the wrapper, not at raw curl",
          "never with a raw curl" in configured)
    check("...told that reads need no unlock", "Reads (plain GET) work at any time"
          in configured)
    check("...told to relay a refusal rather than route around it",
          "relay" in configured)
    check("...and warned that ping does not raise the tunnel, which reads as "
          "'the host is down' if nobody says so",
          "ping" in configured and "only ssh" in configured)

    # Half-configured is not configured: one file without the other means the
    # tool cannot work, so describing it would be the same trap.
    (conf / "base_url").unlink()
    check("a half-configured deployment is treated as unconfigured",
          "tools/opn" not in mod.capabilities_brief())

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
