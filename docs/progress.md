# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-08-24 — Published to PyPI as `evennia-archive` 0.1.0

First public release: https://pypi.org/project/evennia-archive/0.1.0/. Version bumped from the
bootstrap `0.0.1`, in sync across `pyproject.toml`, `__init__.py`, and the smoke test. README's
Install section now leads with `pip install evennia-archive`, every link converted to an absolute
GitHub URL, and the "not published" status claim dropped. Built with `python -m build`, verified via
`twine check` and a clean-room `pip install` into a fresh venv before upload, then re-verified against
the live PyPI copy with a forced uncached install. Tagged `v0.1.0`.

## 2026-08-23 — Initial build

The library archives Evennia objects and accounts into a second database and restores them into a
rebuilt one. Not published, and not yet used by a consumer game.

**What exists**

- `ArchivableMixin` — mints an `archive_id` at creation and never changes it
- `ArchiveRecord` and `ArchiveRouter` — the index into the archive, and what keeps it there
- `archive(obj)` — copies an object in, as an upsert
- `find(key, value, model=None)` — archive identifiers of objects matching an attribute
- `restore(archive_id)` — rebuilds it in the live database, stripped of dbrefs
- `delete(archive_id)` — removes an archived copy and its record

**What proves it**

A full install from a bare gamedir, following [archive-settings.md](archive-settings.md) verbatim so
the instructions are what gets exercised:

| | `evennia.db3` | `archive.db3` |
|---|---|---|
| Tables | 42 | 43 |
| `evennia_archive_archiverecord` | absent | present |
| Accounts | 1 (`root`) | 0 |
| Objects | 0 | 0 |

The router is demonstrably consulted: `allow_migrate` refuses the library's model against `default`
and allows it into `archive`, while letting Evennia's own 42 tables through.

The round trip closes for **both** `ObjectDB` and `AccountDB`. A test archives an object, deletes it
from the live database, and restores it with key, attributes, tags and identity intact and a
*different* primary key — identity survives, dbrefs do not, which is the whole design in one
assertion.

A unique value taken while its owner was away does not block a restore. An account's username is
unique and a world rebuild frees every name in it, so a returning player may find theirs held by
someone else; the restore proceeds as `rowan1`, `rowan2` and so on, recording the original under
`archive_renamed_from` so the game can offer a rename whenever suits. Characters are unaffected —
`ObjectDB` declares no unique fields at all.

**Test suite: 51 tests, all passing.**

**Proven in a running game.** Beyond the unit tests, the full disaster scenario was executed by hand
in `examples/demo_game`: an account and two characters created and archived, `evennia.db3` deleted
and rebuilt with `evennia migrate`, the game restarted, and a character recovered into the rebuilt world
from **nothing but a wallet address** — level intact, under a new primary key, in a database that had
never seen it. The archive sat untouched through the wipe.

The same run showed what happens if a consumer creates an account before checking the archive: the
new account takes the username and mints its own identity, leaving the archived one unrestorable.
The documented flow — sign in, search the archive, restore on a hit — avoids it, and the auto-rename
above now handles the case where the name really has gone.

Every call is a plain synchronous function — the library imports no Twisted and assumes no reactor,
because a management command, a migration and a test have none. Dispatching off the reactor is the
consumer's decision, and `find()` is the one that genuinely needs it.

**What does not exist yet**

- Reference translation. Live-database foreign keys are dropped rather than rebuilt, so an archived
  object knows its own state and nothing about what it was attached to. This needs the disposition
  table described in [design.md](design.md).
- Any testing on PostgreSQL. Everything so far is SQLite.
- Behaviour alongside a consumer's own database routers, which is reasoned from source but unrun.
