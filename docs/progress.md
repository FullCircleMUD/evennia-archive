# Progress

Reverse-chronological milestone log. Newest first. Each entry states what became true and what proves
it.

## 2026-09-08 — Refusing a settings module it cannot work with

156 tests.

**`config.py` holds every constant, and `check_settings()`.** Called from `AppConfig.ready()`, so a
misconfigured instance refuses to start rather than failing at the first archive. Three refusals,
collected and raised together: no `archive` entry in `DATABASES`, `LOCK_FUNC_MODULES` without
`evennia_archive.lockfuncs`, and the archive naming the same database as `default`. Cases `CS-01` to
`CS-07`.

**The archive cannot share the game's database, and now says so.** It is a clone of Evennia's schema,
so pointing its alias at the game's database does not give it a second set of tables — it hands it
Evennia's. Archiving would write into the live rows and the rebuild would take both. Every other
alias in the corpus owns uniquely-named tables and shares perfectly well; this one is the exception.
The check compares `TEST["NAME"]` alongside engine, name, host and port, because under Django's test
runner that is the database an alias actually uses — without it the check refused the library's own
suite.

**Constants moved to `config.py` and are imported where needed.** `db_router.py` held a second
independent `"archive"` literal with nothing to say it was the same string as `api.py`'s. Cases
`CT-01` to `CT-03`.

**Six log sites, chosen because nothing else would report them.** A raise that reaches its caller is
already reported, so these are the paths where no exception is thrown and no message reaches anyone:
the `archive()` self-heal, a `restore()` rename, a username refused because the archive holds it,
account `#1` being skipped, and a successful restore or delete. Every case has a negative twin —
each site sits on a path that also runs constantly, and a log an operator has learned to scroll past
is worse than no log. Cases `LO-01` to `LO-12`.

**`installing.md` carries numbered steps, both settings tables and a "what is not checked for you"
section**, and `interoperability.md` covers all sixteen siblings rather than six. `evennia-database-cascade`
is recorded there as an agreed hard dependency that is not yet in place.

## 2026-09-06 — An identity always has a row behind it

134 tests.

**Account `#1` is not archived at creation.** Found by booting a second instance in
`evennia-scaling`'s demo: Evennia's initial setup makes a superuser called `root` on every first boot,
so the second one collided on `username` in the shared archive — and an initial-setup failure stops the
Server *and* the Portal, which took down every instance behind it.

Keyed on the primary key rather than on `is_superuser`. `#1` is what Evennia demands be present and
what cannot be restored anywhere without displacing the local one; a second superuser is neither, so it
is archived like any other account. That is what lets a consumer have a privileged account that moves
between instances. Cases `AM-22` and `AM-23`.

**Accounts and characters are archived when they are created.** Accounts at `at_account_creation`,
characters at the end of `at_post_create_character` — after the owner stamp and the lock rewrite, so
the stored copy carries both. Those hooks already minted an `archive_id`, and an identity with no row
behind it is a half-state: it names an archive entry that does not exist, and `restore()` on it raises.
Cases `AM-14` to `AM-17`.

This is the library's one departure from "the consumer decides when to archive", and `design.md` and
`CLAUDE.md` now say so rather than contradicting it. Adding the mixin is the opt-in: the hooks check
for it before doing anything. Objects are deliberately left out — `at_object_creation` is a hook a
spawner may reach thousands of times an hour, and characters inherit it.

**A departed player keeps their name.** The archive carries Evennia's `UNIQUE` on `username`, so an
account archived and then deleted holds that name while it is free in the live database — and the next
person to take it could not be archived, which with creation-time archiving means their registration
fails outright. `validate_username` refuses the name instead: Evennia's own check first, then the
archive. Refusing rather than renaming, because the player who left has not given the name up and the
person being turned away has not lost anything yet. Cases `AM-18` to `AM-21`.

**Both finds ignore case by default.** `case_insensitive=True`, and `False` to require an exact match.
On `find_by_column` it applies to text columns only — there is no case in a boolean or an integer to be
insensitive about, and asking for one must not break a search that works. On `find_by_attribute` it
reaches `db_strvalue` alone: a pickled value is compared as bytes. Cases `FN-09` to `FN-11` and `FC-10`
to `FC-12`.

**`archive()` returns a string identity whichever branch it took.** A record loaded from the database
read its `UUIDField` back as a `uuid.UUID` while a newly created one still held the string it was
given, so the return type depended on whether a copy already existed. The column stays a `UUIDField` —
on Postgres that is 16 bytes against 36, on a key everything joins through — and the string is put back
on the record at the boundary, where the rest of the library already speaks strings. Case `AR-12`.

Two cases were rebuilt rather than repaired, because creation-time archiving made their setup
impossible to construct: `FC-08` now proves the alias by letting the two copies diverge, and the
restore-collision cases take their name with a plain account typeclass that nothing archives.

## 2026-09-05 — Two ways to search the archive

**`find()` is now `find_by_attribute()`, and `find_by_column()` joins it.** Not everything worth
searching on is an attribute: an account's `username` is a column on `AccountDB`, and looking one up
meant duplicating it into an attribute purely to make it findable. Both calls return the same thing —
archive identifiers, ready for `restore()` — so a consumer picks whichever matches where their value
actually lives. The rename landed first and on its own, with the eight `FN` cases proving the
behaviour was untouched by it.

**`model` is required on the column search**, where it is optional on the attribute one. Attribute
keys are uniform across models, and a model that has never held one simply does not match. Columns
are not: `username` exists on `accountdb` and nowhere else, so an unnarrowed column search would have
to either raise on every model lacking the column or swallow the miss silently. Naming the model makes
the mistake explicit instead — an unknown model raises `LookupError`, a column the model does not have
raises `FieldDoesNotExist`. Cases `FC-01` to `FC-09`.

The column is checked for being concrete, not merely for existing: `_meta.get_field()` answers for
`db_attributes` and `db_tags` as well as for columns, and a relation is not something a caller can
hold a value in.

`FC-08` is the case worth having. `username` is a column on the live table too, so a search that
leaked to the default alias would find the live account and look like a success.

117 tests.

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

A full install from a bare gamedir, following [installing.md](installing.md) verbatim so
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
