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

**Working, published, not yet in production.** For what exists, what proves it and what does not
exist yet, read [docs/progress.md](docs/progress.md) — it is the only place that state is kept, so
this section stays a pointer rather than a second copy that ages.

## Where to read first

1. [README.md](README.md) — what the library is and the problem it solves.
2. [docs/test-plan.md](docs/test-plan.md) — **where a behavioural change starts.** A case lands here
   before the test, and the test before the code.
3. [docs/INDEX.md](docs/INDEX.md) — map of the design docs.
4. [docs/progress.md](docs/progress.md) — what actually exists right now.

## Load-bearing architectural principles

Agreed in the design conversation of 2026-08-23. Every implementation decision must respect them.

1. **The library does not own game concepts.** Rooms, items, zones, economies and quest state belong
   to the consumer game. The library provides the archival mechanism.
2. **No FCM-specific assumptions.** This library is being extracted from work on FullCircleMUD.
   Wallet addresses, NFTs, banks, FCM typeclass names — all stay in FCM. Default to "consumer
   concern" when uncertain.
3. **Mechanism here, policy in the consumer.** The library supplies row copy, reference discovery,
   the disposition framework and the restore primitives. It ships no scheduler. The consumer decides
   what to archive, what each reference's disposition is, when any of it runs, and where a restored
   object goes.

   **One exception: creation.** An account and a character are archived at the hook that mints their
   identity, because an identity with no row behind it names an archive entry that does not exist.
   Adding the mixin is the opt-in. See [docs/design.md](docs/design.md) § The one exception: creation.
4. **Test the object, never the library.** Optional capabilities are detected by asking the object
   whether it exposes what is needed — never by checking whether a sibling library is installed.
   Detecting a library is a hidden dependency wearing an optional one's clothes.
5. **Identity comes from the mixin, and only from the mixin.** The library matches rows across two
   databases by `archive_id`, and `ArchiveRecord` keys on a `UUIDField`, so the value has to be a
   uuid4 minted once and never reissued. The mixins are what guarantee that; an object merely
   exposing an `archive_id` attribute guarantees nothing, and the library cannot check after the fact
   — a value from anywhere else either fails at write time as an invalid UUID, or collides and makes
   `restore()` return the wrong object with nothing in any log. So `archive()` tests for the mixin
   itself rather than for the attribute. See `AR-09` in the test plan.
6. **Test-first.** A case lands in [docs/test-plan.md](docs/test-plan.md), then the test, then the
   code. The plan is a commitment rather than a wishlist, and its `Test function` column is the
   coverage trail. See [test-first-process.md](../../design/test-first-process.md).
7. **Vanilla first.** The library must be fully useful with no optional integration present. Every
   enhancement degrades to the plain behaviour rather than becoming a requirement.

## Out of scope

Decided as questions arise — the project is too young for a settled list. Rulings so far:

- **Database backups.** This is not `pg_dump` and is not a substitute for backing up a database.
- **Consumer-minted identifiers.** Identity is the mixin's to mint, not the consumer's to supply. See
  principle 5.
- **A database-resolution helper, for now.** The standard has a library owning an alias ship an
  `archive_database()` / `describe_archive_database()` pair, and the linter reports its absence as
  `database_helper_missing`. That is a **deliberate deferral, not an oversight**: `evennia-database-cascade`
  is being built to formalise exactly that resolution, and this library will take it as a direct
  dependency rather than hand-roll a second implementation to throw away. The consumer writes the
  `DATABASES` entry by hand until then. Do not close the warn by writing the helper — see
  [docs/interoperability.md](docs/interoperability.md) § evennia-database-cascade.

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
│   └── demo_game/             # gamedir installed per docs/installing.md
├── src/
│   └── evennia_archive/       # library code (src layout)
│       ├── api.py             # archive() / restore() / find / delete
│       ├── apps.py            # AppConfig; ready() runs the boot check
│       ├── config.py          # every constant, and check_settings()
│       ├── db_router.py       # ArchiveRouter
│       ├── lockfuncs.py       # owns_character()
│       ├── log.py             # binds archive_log via evennia-logging-extension → archive.log
│       ├── migrations/        # ArchiveRecord's schema
│       ├── mixins.py          # the archivable mixins
│       ├── models.py          # ArchiveRecord
│       └── tests.py           # unit tests (run via runtests.py)
└── tests/                     # standalone test settings (test_settings.py, urls.py)
```

## Tools and environment

- Python 3.10+ (pinned via `pyproject.toml`).
- Runtime dependencies: Evennia and `evennia-logging-extension`. The extension is not published, so
  a dev venv installs it from its checkout: `pip install -e ../evennia-logging-extension`.
- Tests run through Django's test runner via `python runtests.py` — not pytest.
- Development uses a dedicated venv at `venv/` (gitignored), independent of any consumer game.
