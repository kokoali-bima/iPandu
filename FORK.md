# iPandu and iSmart-LA — how the two repos relate

iPandu is an **experimental fork** of
[iSmart-LA](https://github.com/kokoali-bima/iSmart-LA). The two share roughly
**79% of the code**: Telegram plumbing, model failover, sessions, memory, media,
Drive, scheduling, the update mechanism, bilingual replies, cost control.

Fork point: **v0.2b.76** (`95578c7`), 5 September 2026.

| | iSmart-LA | iPandu |
|---|---|---|
| Role | **production** — infrastructure agent | **experiment** — AI assistant |
| Stability | guarded, released carefully | free to break |
| Infra capability | yes | yes, deliberately kept |
| Deployment | production VM | its own VM |
| SSH keys | cluster key | **its own keypair** |

The split is not because the code differs. It is because the two have
incompatible goals: iSmart-LA is being stabilised toward a production release,
while this one needs to be experimented on. Those cannot share a repo without
one of them losing.

## Merge rule: ONE DIRECTION

```
iSmart-LA (production)  ──merge──▶  iPandu (experiment)
                        ◀── NEVER ───
```

Substrate fixes are made **once**, in iSmart-LA, and pulled in here:

```bash
git fetch upstream
git merge upstream/master
```

Nothing flows back automatically. If something proven here belongs in
production, raise it as its own change in the iSmart-LA repo — never by merging
backwards.

The `upstream` remote is **push-disabled** on purpose:

```
upstream  https://github.com/kokoali-bima/iSmart-LA.git  (fetch)
upstream  DISABLED-push-to-production-forbidden          (push)
```

If a normal push URL ever appears there, that is not a convenience someone
added. It is a safety rail someone removed.

## The discipline that keeps this working

**Add files. Do not restructure `lite_agent.py`.**

This is the one rule that decides whether the scheme survives. While the shape
of that shared 79% holds, `git merge upstream/master` stays clean. Once iPandu
starts rearranging the same file, every merge becomes a fight, and within a
month people stop doing them — at which point the repos have quietly separated
and every substrate bug has to be fixed twice.

For scale: in **two days**, 4–5 September, ten bugs that had reached production
were found and fixed in this shared substrate. If merges had already been
broken, all ten would have been fixed twice.

In practice:

- New capabilities (email, WhatsApp, calendar) go in **new files**, wired in
  through the existing marker protocol rather than by opening up old functions.
- Need to change `lite_agent.py`? First ask whether the change actually belongs
  to iSmart-LA. If it does, make it there and pull it in.
- If it genuinely belongs here, keep it small and in one place rather than
  spread across the file.

## Versioning

iPandu has its own version line, starting at `v0.1.0`. iSmart-LA's tags were
deliberately **not** carried over, so `current_version()` — which reads
`git describe --tags` — can never report a production version on the assistant's
machine. No code change was needed for that. The 114 commits of history are
intact, so provenance is not lost.

## Why the SSH keys must be separate

iPandu keeps its infrastructure capability — that is the point of it. But a
different VM does **not** separate the risk if the key is the same: an
experimental agent holding the cluster key still has full production access.

So iPandu is registered with **its own keypair**, on only the servers it is
meant to touch. The mechanism already exists: `/addserver` generates its own key
and only ever shows the public half for installation.

One more thing applies here and not in production. The moment iPandu can read
email, there is for the first time an input an **attacker can write** — anyone
can send an email. Email content must be treated as data, never as
instructions: markers inside it are ignored, no `LEARN:` lines are honoured,
and above all no `NEEDS_WRITE:` may originate from the body of an email.

## Language

Repository documentation, code comments and commit messages are in **English**,
the same as iSmart-LA. What the bot says **in Telegram stays bilingual**
(English and Indonesian) — that is a product behaviour, not a repo convention,
and it does not change here.

## Merging in practice — what the first real merge showed

The first pull from upstream (v0.2b.77) is worth recording, because it settled
what the theory could not.

**`lite_agent.py` merged with zero conflicts** and came out byte-identical to
production. The discipline works: while iPandu only adds files, the shared 79%
flows in for free.

**Two files conflicted, and they will conflict again:** `README.md` and
`CHANGELOG.md` — the only two iPandu deliberately rewrote. That is expected and
cheap, but one of the two was self-inflicted:

- `CHANGELOG.md` conflicts because both sides insert at the top. Resolution:
  keep iPandu's `v0.1.0` entry above, let upstream's entries follow. Mechanical,
  same every time.
- `README.md` conflicted because the fork **deleted** the inherited
  `Status: v0.2b.xx` paragraph, and upstream then edited that same line — git
  cannot resolve a delete against a modify. Fixed by restoring that paragraph
  below iPandu's own header. Both now coexist: iPandu's status is read first (it
  is higher up), and upstream's version bumps land in the inherited block
  without touching anything of ours.

The lesson generalises: **do not delete what upstream still maintains.** Add
above it, or leave it alone. Deleting an upstream line converts every future
edit to it into a conflict.

Recipe when the merge stops:

```bash
git fetch upstream
git merge upstream/master        # expect CHANGELOG.md to conflict
# keep iPandu's v0.1.0 entry on top, take upstream's entries below
git add CHANGELOG.md && git commit
python3 dev/run_all.py lite_agent.py    # must still be green
```

## Two more things the merges taught

**`git fetch upstream` brings upstream's tags.** The second merge pulled in 83
of iSmart-LA's tags, which quietly undid the whole reason they were dropped at
fork time: `current_version()` reads `git describe --tags`, so the assistant
would have started reporting a production version on its own machine. The
release gate caught it; nobody would have caught it by eye.

Fixed permanently:

```bash
git config remote.upstream.tagOpt --no-tags
```

A fetch cannot bring them back now. If `git tag` here ever lists something that
is not on iPandu's own `v0.1.x` line, that setting has been lost.

**A fork's HEAD is always ahead of its own last tag.** It keeps taking upstream
commits, so `git describe` drifts to `v0.1.x-N-g…` after every merge. That is
normal, not a fault — but it means iPandu has to cut its own release now and
then, or the release gate has nothing meaningful to check. Bump the README
status line, add a short CHANGELOG entry, tag it. The entry does not have to be
long; "upstream merged through vX" is enough.
