#!/usr/bin/env python3
"""Tests for picking the right guest, on the right node, with the right tool.

All of this comes from one live failure. The operator's standing rule is
"always snapshot before changing a VM", the bot offered to do it, and it failed
every time with:

    bash: line 1: qm: command not found

Which was true, and had nothing to do with the problem. Four things were wrong
at once, and each one hid the next:

  1. `_VMID_RE` accepted TWO digits. Proxmox ids start at 100, so a match like
     "VM 20" could never name a guest -- but it was taken as one, and the
     snapshot went looking for it.

  2. Every failure logged, only the LAST returned. The loop rebound `err` each
     time round, so whatever the final host said was the whole diagnosis.

  3. The final host was a Proxmox BACKUP server. It answers ssh, runs the
     command, and has no `qm` -- so it overwrote three copies of the real
     error ("vmid: invalid format") with a shell message about a missing
     binary. That is the message the operator saw, every time.

  4. The node hint was dead. `find_vm_node()` returned a NAME ("node2") and
     `_snapshot_hosts()` compared it against ADDRESSES, so it never matched
     and the ordering it exists to provide never happened.

A fifth, found while fixing: a container needs `pct snapshot`, not `qm`.
"""
import atexit
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

SRC = pathlib.Path(sys.argv[1]).resolve()

HOME = tempfile.mkdtemp(prefix="isla_snap_")
atexit.register(shutil.rmtree, str(HOME), ignore_errors=True)
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME     # Path.home() reads this one on Windows
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
os.environ.setdefault("ALLOWED_USER_IDS", "111")
os.environ["ALLOWED_GROUP_IDS"] = ""

spec = importlib.util.spec_from_file_location("la_snap", str(SRC))
mod = importlib.util.module_from_spec(spec)
sys.modules["la_snap"] = mod
spec.loader.exec_module(mod)

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


# The cluster as this deployment actually has it registered: one Proxmox entry
# covering three nodes, plus a backup server that is NOT a hypervisor.
SERVERS = [
    {"kind": "hypervisor", "flavour": "proxmox", "name": "uin-pve1",
     "host": "10.10.95.1", "cluster_hosts": ["10.10.95.3", "10.10.95.2"]},
    {"kind": "vm", "flavour": "vm", "name": "uin-pbs1", "host": "10.10.95.4"},
]


# --- 1. what counts as a VM id --------------------------------------------
check("a real id is picked up", mod.guess_vmid("snapshot vm 105 dulu") == "105")
check("...with the vmid= spelling too", mod.guess_vmid("vmid=1042") == "1042")
check("a two-digit number is NOT a VM id -- Proxmox starts at 100, so this "
      "could only ever have been a port or a size",
      mod.guess_vmid("restart vm 20") is None)
check("...nor a three-digit one below 100 written with a leading zero",
      mod.guess_vmid("vm 099") is None)
check("a real id later in the same text still wins over an early false one",
      mod.guess_vmid("port vm 22, then vm 110") == "110")
check("nothing to find means nothing returned", mod.guess_vmid("restart it") is None)


# --- 2. which hosts are even candidates ------------------------------------
with patch.object(mod, "_read_servers", return_value=SERVERS):
    hosts = mod._snapshot_hosts(None)
check("all three cluster nodes are candidates",
      set(hosts) == {"10.10.95.1", "10.10.95.2", "10.10.95.3"})
check("the BACKUP server is not -- it has no `qm`, can never succeed, and its "
      "error is what buried the real one", "10.10.95.4" not in hosts)

with patch.object(mod, "_read_servers", return_value=SERVERS):
    ordered = mod._snapshot_hosts("10.10.95.2")
check("the node holding the guest is tried first", ordered[0] == "10.10.95.2")
check("...and the others stay behind it, in case the lookup was stale",
      set(ordered) == {"10.10.95.1", "10.10.95.2", "10.10.95.3"})
check("no host is listed twice", len(ordered) == len(set(ordered)))

with patch.object(mod, "_read_servers", return_value=[SERVERS[1]]):
    check("with no hypervisor registered at all, there is nothing to try -- "
          "and take_snapshot says so rather than shelling out",
          mod._snapshot_hosts(None) == [])


# --- 3. the snapshot call itself -------------------------------------------
def run_snapshot(vmid, target, results_by_host=None, reason="resize RAM"):
    """Capture the ssh commands one take_snapshot() would send."""
    sent = []
    def fake_run(argv, **kw):
        sent.append(argv)
        host, command = argv[-2], argv[-1]
        rc = (results_by_host or {}).get(host, 0)
        return SimpleNamespace(
            returncode=rc, stdout="",
            stderr="" if rc == 0 else f"boom on {host}\nsecond line")
    with patch.object(mod, "_read_servers", return_value=SERVERS), \
         patch.object(mod.subprocess, "run", side_effect=fake_run), \
         patch.object(mod, "register_snapshot") as reg:
        ok, detail = mod.take_snapshot(vmid, target, reason)
    return ok, detail, [a[-1] for a in sent], [a[-2] for a in sent], reg

TARGET = {"node": "node2", "host": "10.10.95.2", "type": "qemu"}

ok, detail, cmds, hosts_tried, reg = run_snapshot("105", TARGET)
check("a good snapshot succeeds", ok)
check("...on the first host, because the right node was known",
      hosts_tried == ["10.10.95.2"])
check("...with qm, and the id as a bare number", cmds[0].startswith("qm snapshot 105 ismart-"))
check("...carrying a description that names the reason",
      "resize RAM" in cmds[0] and "--description" in cmds[0])
check("...and it is registered so /snapshots can list it later", reg.called)
check("the registered node is the NAME, which is what a human reads",
      reg.call_args[0][0]["node"] == "node2")

ok, detail, cmds, hosts_tried, _ = run_snapshot(
    "105", {"node": "node9", "host": "10.10.95.9", "type": "qemu"})
check("a node outside the registered list is still tried first rather than "
      "dropped -- the cluster can have grown since /addserver",
      hosts_tried[0] == "10.10.95.9")

ok, detail, cmds, _, _ = run_snapshot("105", dict(TARGET, type="lxc"))
check("a CONTAINER is snapshotted with pct, not qm", cmds[0].startswith("pct snapshot 105"))

ok, detail, cmds, hosts_tried, reg = run_snapshot("20", TARGET)
check("a two-digit id is refused outright", not ok)
check("...without a single ssh -- Proxmox would have said the same thing four "
      "round trips later", hosts_tried == [])
check("...saying what is wrong with it, not just that it failed",
      "100" in detail and "20" in detail)
check("...and nothing is registered", not reg.called)

ok, detail, _, _, _ = run_snapshot("not-a-number", TARGET)
check("a non-numeric id is refused the same way, not crashed on", not ok)


# --- 4. every node's error survives, not just the last one -----------------
ok, detail, cmds, hosts_tried, _ = run_snapshot(
    "105", TARGET, results_by_host={"10.10.95.1": 1, "10.10.95.2": 1, "10.10.95.3": 1})
check("with every node failing, the whole thing fails", not ok)
check("...having tried all three", len(hosts_tried) == 3)
for h in ("10.10.95.1", "10.10.95.2", "10.10.95.3"):
    check(f"...and {h}'s own error is in the report, rather than being "
          f"overwritten by whichever host came last", h in detail)

ok, detail, _, hosts_tried, _ = run_snapshot(
    "105", TARGET, results_by_host={"10.10.95.2": 1, "10.10.95.3": 1})
check("a failure on the first node falls through to the one that works", ok)
check("...and stops there, leaving the third node untouched",
      hosts_tried == ["10.10.95.2", "10.10.95.1"])

with patch.object(mod, "_read_servers", return_value=[SERVERS[1]]):
    with patch.object(mod.subprocess, "run") as never:
        ok, detail = mod.take_snapshot("105", None, "x")
check("with no hypervisor registered, it says so and shells out to nothing",
      not ok and "addserver" in detail.lower() and not never.called)


# --- 5. the lookup returns all three things the caller needs ---------------
CLUSTER = json.dumps([
    {"vmid": 105, "node": "node2", "type": "qemu"},
    {"vmid": 300, "node": "node3", "type": "lxc"},
]) + "\n@@\n" + json.dumps([
    {"type": "cluster", "name": "uin"},
    {"type": "node", "name": "node2", "ip": "10.10.95.2"},
    {"type": "node", "name": "node3", "ip": "10.10.95.3"},
])

def lookup(vmid, stdout=CLUSTER):
    asked = []
    def fake_run(argv, **kw):
        asked.append(argv[-2])
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    with patch.object(mod, "_read_servers", return_value=SERVERS), \
         patch.object(mod.subprocess, "run", side_effect=fake_run):
        return mod.find_vm_target(vmid), asked

found, asked = lookup("105")
check("the guest's node is found", found["node"] == "node2")
check("...resolved to an ADDRESS, which is what _snapshot_hosts compares "
      "against -- a name matched nothing and the hint was dead",
      found["host"] == "10.10.95.2")
check("...and its type comes back too, so pct vs qm is decided from fact",
      found["type"] == "qemu")
check("one ssh, not two: both pvesh calls go out together", len(asked) == 1)
check("the backup server is never asked -- it has no pvesh either",
      "10.10.95.4" not in asked)

found, _ = lookup("300")
check("a container is reported as one", found["type"] == "lxc")

found, _ = lookup("999")
check("a guest that is not in the cluster returns nothing, rather than a "
      "half-filled target", found is None)

found, _ = lookup("105", stdout=json.dumps([{"vmid": 105, "node": "node2",
                                             "type": "qemu"}]) + "\n@@\nnot json")
check("if the address map is unreadable, the node still comes back -- with "
      "the host that answered, which is at least a node of this cluster",
      found and found["node"] == "node2" and found["host"] in
      {"10.10.95.1", "10.10.95.2", "10.10.95.3"})

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
