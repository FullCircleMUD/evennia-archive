# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-08-23 — restore() ships; the round trip closes

The second API call, and with it the first end-to-end proof of the premise. A test archives an
object, **deletes it from the live database**, and restores it: key, attributes and tags all intact,
and a **different primary key** from the one the archive holds. Identity survives, dbrefs do not,
which is the whole design in one assertion.

`restore()` takes an identifier because the object does not exist yet, and returns the restored
object — or its primary key with `return_object=False`, which evicts the instance from the idmapper
cache first, for consumers whose restoring process must not hold resident game objects.

Location is set at insert rather than afterwards, so no movement hooks fire on an object that did not
move. Unset, it is `None` — a legal Evennia state — because where a restored object belongs is a game
decision the library does not make.

Restoring an identity that is already live returns the existing object rather than making a second
copy, so a re-run cannot duplicate.

**Known limitation:** `archived_model` stores a bare model name, following Evennia's own convention
on `Attribute.db_model`. A consumer whose app defines a model of the same name would be ambiguous.

**Test suite: 26 tests, all passing.**

## 2026-08-23 — archive() ships, and two Evennia constraints surface

First of the four API calls. `archive(obj)` copies an object into the archive as an upsert: read the
identity, find the `ArchiveRecord`, and either update the row it points at or insert a copy and record
where it landed. Attributes and tags are replaced wholesale rather than diffed. All inside one
transaction on the archive alias, so the pointer and its target cannot disagree.

Writing it surfaced two constraints that govern the whole library, now in
[design.md § The archive holds rows, not objects](design.md):

**The idmapper is not database-aware.** Evennia's models are `SharedMemoryModel` and instances are
cached by primary key alone, so `ObjectDB.objects.using("archive").get(pk=5)` can return the *live*
object with pk 5. The first draft did exactly that and would have deleted a live character's
attributes while appearing to maintain the archive.

Found only because the test fixtures occupy low primary keys in the live database and the archived
copy collided with one — a read for `"Rowan"` returned a room called `"Room"`. Without the collision
it would have passed its tests. Worth remembering when deciding how isolated a test should be: the
contamination is what caught it.

**Evennia's creation hooks hang off `save()`.** A plain ORM `create()` in the archive fires
`at_first_save` and therefore `at_object_creation`, so an archived copy ran its typeclass's creation
logic — including this library's own identity minting. `bulk_create()` and queryset `update()` bypass
it.

References into the live database (`db_location`, `db_home`, `db_destination`, `db_account`) are
dropped for now. Rebuilding them is the reference-translation work and needs a disposition table that
does not exist yet.

**Test suite: 16 tests, all passing.**

## 2026-08-23 — Identity landed, migration path proven end to end

`ArchivableMixin` ships. Identity is locked: attribute key `archive_id`, value `str(uuid.uuid4())`
canonical lowercase hyphenated, stored `strattr=True` so it is unpickled in `db_strvalue`, minted in
the creation hooks or via `at_archive_init()`, never overwritten. Two tests pin the decisions that
are expensive to reverse — canonical round-trip through `uuid.UUID`, and `db_strvalue` populated with
`db_value` null.

`ArchiveRecord` and `ArchiveRouter` ship alongside it. The record carries `archived_model` and
`archived_pk`, making it the index into the archive rather than only bookkeeping — primary keys
inside the archive are stable because the archive is never rebuilt.

**Evidence — a full install from a bare gamedir, following `archive-settings.md` verbatim:**

| | `evennia.db3` | `archive.db3` |
|---|---|---|
| Tables | 42 | 43 |
| `evennia_archive_archiverecord` | absent | present |
| Accounts | 1 (`root`) | 0 |
| Objects | 0 | 0 |

The router is demonstrably consulted: `allow_migrate` refused the library's model against `default`
and allowed it into `archive`, while letting Evennia's 42 tables through. Library test suite: 8 tests,
all passing.

## 2026-08-23 — Design drafted, first decision locked

[design.md](design.md) written as the intended finished state, with a box above every section that is
not yet settled. Boxes are removed only when the project owner confirms that section is locked.

**Locked:** what `restore()` returns. The row is written through the ORM so an instance always
exists; `return_object=True` (the default) returns it, `return_object=False` evicts it from the
idmapper cache via `flush_from_cache(force=True)` and returns the primary key. The second mode serves
consumers whose restoring process must not hold resident game objects.

**Still open:** the identity attribute's name and storage, how consumers declare dispositions,
shallow versus deep archival, and three spikes — second-database migration, the full set of Evennia
packed markers, and how the reference survey runs repeatedly.

## 2026-08-23 — Repository scaffolded

Structure, packaging and the standalone test runner are in place, following
`design/library-standards.md` in the umbrella.

- `pyproject.toml`, `runtests.py`, `tests/test_settings.py`, `tests/urls.py`
- `src/evennia_archive/` with `__version__` and a smoke test proving install and runner work end to end
- `README.md`, `CLAUDE.md`, `docs/INDEX.md`, `docs/interoperability.md`

**Evidence:** `pip install -e .` and `python runtests.py` both succeed against a dedicated venv.

**Not yet present:** any library code, `examples/`, `docs/archive/`. The public interface is not
designed.

## 2026-08-23 — Repository created

Created as `FullCircleMUD/evennia-archive` and cloned to `libraries/evennia-archive/`.

The library is being extracted from character-backup design work on FullCircleMUD. The shape agreed
before the repo existed:

- A second Evennia database on the same schema, migrated alongside the game, never run as a game
- Account and character rows copied across as they change; attribute values move as opaque bytes,
  never parsed, because both ends share a schema
- Reference translation is the substantive problem — stable identifier in, new primary key out
- Phase one solves the vanilla case; optional integrations come later

`[TBD — needs discussion: the public interface, the identity attribute's name and storage, and
whether location is resolved at backup time or mirrored on movement.]`
