# CLAUDE.md

> **Project-wide working rules and cross-repo context live in the FCM umbrella repo's `CLAUDE.md`**,
> loaded automatically when you work from the umbrella root. If you opened this repo directly instead
> of via the umbrella, relaunch from the umbrella root for the full context. This file holds only this
> repo's specific instructions.

Instructions for Claude (and other LLM agents) working in this repository.

## What this project is

`evennia-archive` maintains a second [Evennia](https://www.evennia.com/) database — same schema,
migrated alongside the game, never run as a game — holding accounts and characters, so a world can be
rebuilt from source without losing its players. Tagline: **"Rebuild your Evennia world without losing
your players."**

For the big-picture overview, read [README.md](README.md).
For the design wiki, read [docs/INDEX.md](docs/INDEX.md).

## Project status

**Working, unpublished, unused.** All four calls — `archive()`, `find()`, `restore()`, `delete()` —
are built and tested, and the round trip closes for both objects and accounts. References between
objects are dropped rather than translated, and nothing has run on PostgreSQL. For current state read
[docs/progress.md](docs/progress.md).

## Where to read first

1. [README.md](README.md) — what the library is and the problem it solves.
2. [docs/INDEX.md](docs/INDEX.md) — map of the design docs.
3. [docs/progress.md](docs/progress.md) — what actually exists right now.

## Load-bearing architectural principles

Agreed in the design conversation of 2026-08-23. Every implementation decision must respect them.

1. **The library does not own game concepts.** Rooms, items, zones, economies and quest state belong
   to the consumer game. The library provides the archival mechanism.
2. **No FCM-specific assumptions.** This library is being extracted from work on FullCircleMUD.
   Wallet addresses, NFTs, banks, FCM typeclass names — all stay in FCM. Default to "consumer
   concern" when uncertain.
3. **Mechanism here, policy in the consumer.** The library supplies row copy, reference discovery,
   the disposition framework and the restore primitives. It ships no scheduler, no hooks and no
   triggers. The consumer decides what to archive, what each reference's disposition is, when any of
   it runs, and where a restored object goes.
4. **Test the object, never the library.** Optional capabilities are detected by asking the object
   whether it exposes what is needed — never by checking whether a sibling library is installed.
   Detecting a library is a hidden dependency wearing an optional one's clothes.
5. **Identity is a contract, not an implementation.** The library needs a stable identifier to match
   rows across two databases, so it states what it looks for and ships a default way to provide it.
   How a consumer mints one is theirs to decide.
6. **Vanilla first.** The library must be fully useful with no optional integration present. Every
   enhancement degrades to the plain behaviour rather than becoming a requirement.

## Out of scope

Decided as questions arise — the project is too young for a settled list. Rulings so far:

- **Database backups.** This is not `pg_dump` and is not a substitute for backing up a database.
- **Minting policy.** The library does not decide how a consumer generates stable identifiers.

## Working conventions

- **Editing design docs.** Update or add design documents whenever an architectural decision is made
  or refined. Capture the *why*, not just the *what*. Index new docs in [docs/INDEX.md](docs/INDEX.md).
- **Don't put implementation detail in this file or README.** Link out to `docs/` instead. Keep
  `CLAUDE.md` and `README.md` stable; let `docs/` churn.
- **License.** BSD 3-Clause. Source files carry an SPDX header on the first line
  (`# SPDX-License-Identifier: BSD-3-Clause`).

## Documentation discipline (load-bearing)

Design documents in `docs/` must reflect decisions **actually discussed and agreed on with the project
owner**. They are not a place to forward-design the system from first principles or extrapolate
"reasonable defaults" from a starting point.

**Rules:**

1. **Only capture what was discussed and agreed.** If the conversation establishes a principle, do not
   extrapolate it into specifics that were not raised — API shapes, naming conventions, adoption
   checklists.
2. **Flag open questions explicitly.** Write `[TBD — needs discussion: <what is open>]` so a future
   session picks the topic up deliberately rather than inheriting an unagreed assumption.
3. **Smaller is better.** Three discussed points captured faithfully beat three discussed points plus
   seven invented ones. Resist filling out sections "for completeness".

This matters more here than in a mature library: much is still open, so anything written confidently
about an undecided part would be invention.

## Repository layout

```
evennia-archive/
├── CLAUDE.md                  # this file
├── README.md
├── LICENSE                    # BSD 3-Clause
├── pyproject.toml
├── runtests.py                # standalone test runner (no consumer gamedir needed)
├── docs/                      # design wiki (humans + LLMs)
├── examples/
│   └── demo_game/             # gamedir installed per docs/archive-settings.md
├── src/
│   └── evennia_archive/       # library code (src layout)
│       ├── api.py             # archive() / restore()
│       ├── mixins.py          # ArchivableMixin
│       ├── models.py          # ArchiveRecord
│       ├── db_router.py       # ArchiveRouter
│       └── tests.py           # unit tests (run via runtests.py)
└── tests/                     # standalone test settings (test_settings.py, urls.py)
```

## Tools and environment

- Python 3.10+ (pinned via `pyproject.toml`).
- Evennia is the only runtime dependency.
- Tests run through Django's test runner via `python runtests.py` — not pytest.
- Development uses a dedicated venv at `venv/` (gitignored), independent of any consumer game.
