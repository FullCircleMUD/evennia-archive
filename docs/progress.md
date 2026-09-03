# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-09-03 — Three kinds of mixin, and a lock that survives a restore

**Attributes are copied as values, so the idmapper cannot answer for them.** Evennia's `Attribute` is
a `SharedMemoryModel`, and the idmapper caches instances on primary key alone with no database in the
key. Both databases number their attributes from 1, so archiving an account put its rows into the
cache and archiving a character a moment later read them back in place of its own — the character's
archived copy held the account's attributes, and its owner stamp and current shard were lost. Proved
by dumping the same live row two ways: raw SQL said `archive_id`, the ORM said `scaling_username`.
`_replace_attributes` and `_restore_attributes` are now one `_copy_attributes` parameterised by
alias, reading with `values()` and writing with `bulk_create`. `_purge_attributes` gained the same
alias parameter and switched to raw deletes, because `QuerySet.delete()` cannot fast-path `Attribute`
and would build every row as an object on the way to removing it. Cases `PG-01`–`08`, `CP-01`–`12`.

**Four `archive()` cases were reading the archive through the idmapper and had never tested it.**
`ObjectDB.objects.using("archive").get(pk=...)` is answered from the live cache — the probe fetched a
Room where it asked for the archived character. `AR-04` and `AR-05` passed only because the defect
cached the right values under the right numbers; `AR-07` and `AR-08` assert negatives and passed
whatever came back. All four now read as values.

**`ArchivableMixin` is replaced by three kind-specific mixins**, children of `ArchivableBaseMixin`:
`ArchivableObjectMixin`, `ArchivableCharacterMixin` and `ArchivableAccountMixin`. The base owns the
identity and refuses its own creation hooks, so a typeclass carrying it directly fails where the
mistake is made rather than at the first archive. `_identity_of` tests the base, so every child
qualifies — proved by narrowing it to a child and watching thirteen tests fail.

**A character's ownership survives a restore.** `ArchivableAccountMixin.at_post_create_character`
stamps the character with the account's `archive_id` and replaces the `puppet`, `edit` and `delete`
locks Evennia writes with primary keys baked in. Those keys change on every restore, so the locks
came back naming objects that no longer existed and the owning account was refused its own character.
The replacement is `owns_character()`, a lock function the library now ships, which compares the
accessor's identity to the character's stamp and carries no value in the lockstring. Cases `ID-09`,
`AM-03`–`AM-12`, `CM-02`–`CM-04`, `AR-11`, `LF-01`–`LF-06`.

107 tests.

## 2026-09-01 — The mixin is the contract, and the library has a log of its own

**`ArchivableMixin` is now required, not merely offered.** `_identity_of` used to accept anything
exposing an `archive_id`, which read as flexibility and was not. `ArchiveRecord` keys on a
`UUIDField` and `restore()` matches a live row by that value, so an identity minted anywhere else
either fails at write time as an invalid UUID — which is what a hand-rolled value actually does, deep
inside Django, after the copy has begun — or collides and restores the wrong object with nothing in
any log. The mixin is what guarantees a uuid4 minted once and never reissued, and the library has no
way to check the guarantee after the fact.

`archive()` now tests for the mixin itself, and separates the two mistakes it was conflating: no mixin
at all, and the mixin present but `at_archive_init()` never called on an object that predates it.
`AR-09` and `AR-10` cover them. Principle 5 in `CLAUDE.md` was rewritten rather than quietly
falsified — it had said identity was a contract whose implementation belonged to the consumer, which
is the opposite of this.

**A logging shim, `archive_log` into `archive.log`.** Copied verbatim from its siblings, bringing the
library into line with the standard that every library logs to a file of its own rather than into the
main server log. Covered by `LG-01` to `LG-06`. **Nothing calls it yet** — what an archive or a
restore should emit has not been agreed, and is marked `[TBD]` in the test plan.

Also: the test plan itself came under version control, having described the suite for a week without
being committed.

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

- `ArchivableObjectMixin` / `ArchivableCharacterMixin` / `ArchivableAccountMixin` — mint an
  `archive_id` at creation and never change it
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
