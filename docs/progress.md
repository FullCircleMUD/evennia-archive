# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-08-23 — Initial build

The library archives Evennia objects and accounts into a second database and restores them into a
rebuilt one. Not published, and not yet used by a consumer game.

**What exists**

- `ArchivableMixin` — mints an `archive_id` at creation and never changes it
- `ArchiveRecord` and `ArchiveRouter` — the index into the archive, and what keeps it there
- `archive(obj)` — copies an object in, as an upsert
- `restore(archive_id)` — rebuilds it in the live database, stripped of dbrefs

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

**Test suite: 30 tests, all passing.**

**What does not exist yet**

- `find()` and `delete()`
- Reference translation. Live-database foreign keys are dropped rather than rebuilt, so an archived
  object knows its own state and nothing about what it was attached to. This needs the disposition
  table described in [design.md](design.md).
- Any testing on PostgreSQL. Everything so far is SQLite.
- Behaviour alongside a consumer's own database routers, which is reasoned from source but unrun.
