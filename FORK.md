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
