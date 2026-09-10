# Security Policy

iSmart-LA holds real credentials for real infrastructure — a Telegram bot
token, SSH keys with write access to managed hosts, a PIN's salt and hash, and
whatever a chat's own conversations and briefs contain. Report anything found
wrong here the same way you would for a production host, not a typical
open-source issue tracker.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security finding.** Email
**mu@infrasoft.id** instead, with:

- what you found, and what it lets an attacker do
- the version (`git describe --tags`, or the bot's own `/status`) and,
  if relevant, which deployment
- steps to reproduce, if you have them

You'll get an acknowledgement within a few days. Fixes ship as a normal
tagged release once verified; the release notes describe the fix in the same
plain terms as every other entry in `CHANGELOG.md`, without necessarily
naming the exact exploit path until it's been out long enough not to hand
anyone a map.

## What's in scope

- Anything that lets a user bypass the PIN gate, the write-mode window, or
  the origin checks that separate a group chat from an owner's own DM
- Anything that exposes `pin.json`, `servers.json`, session history, or any
  other file `HARDEN_600`/`.gitignore` mark as never meant to leave the host
- Anything that lets a message from Telegram reach a shell, SSH command, or
  file path it shouldn't (prompt injection into a tool call counts)
- A credential — a password typed into chat, an API key, a PIN — reaching a
  model, a log line, or disk when it shouldn't have

## What's not a vulnerability here

- "The owner can do anything" — the owner is meant to; that boundary is
  `_is_owner`/`OWNER_SCOPE.md`, not a bug
- Findings that require already having the Telegram bot token, root on the
  host, or the PIN itself
- Rate limits or abuse scenarios that need control of the Telegram account
  the bot is registered under

## Supported versions

Only the latest tagged release is supported. There is no long-term-support
branch; `/update` (or a fresh `install.sh`) is the fix for every version
before it.
