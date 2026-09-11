# Installing

What a consumer does to install this library — the apps, one database call, one lock-function entry
and one change to their typeclasses — and why each is needed.

Everything here is built and tested. The demo gamedir under `examples/` uses this document verbatim,
so these instructions are what gets exercised rather than a paraphrase of them.

## Read this before you configure the database

**The archive must be a different database from the game. Not a different alias on the same database
— a different database.**

The archive is a *clone of Evennia's schema*: the same `objectdb`, `accountdb` and forty other table
names the game uses. That is what lets attribute values move across as opaque bytes and never be
parsed, and it is why the usual "give every alias its own name and share one Postgres instance"
arrangement does not work here. Point the `archive` alias at the game's database and it does not get
its own copy of those tables — it gets the game's. `archive()` then writes into the live data while
looking like it is archiving, and the world rebuild this library exists to survive destroys the
archive along with everything else.

The library declares that constraint to `evennia-database-cascade`, which places the alias: the
shared `DATABASE_URL` rung is refused for it, so the archive lands on its own
`DATABASE_URL_ARCHIVE` database or its own local `archive.db3` file, never the game's.

The library also refuses to start when the archive and the game resolve to the same database — a
hand-written entry can reach what the cascade would have refused. The check compares engine, name,
host and port; two entries that reach one database under different hostnames would pass it, so the
constraint is yours to hold as well.

## Required settings

All in the consumer's `server/conf/settings.py`. Two of them the library refuses to start without.

| Setting | What it does | Without it |
|---|---|---|
| `INSTALLED_APPS` += the two apps | Loads the library and the cascade that places its database | Nothing runs — including the boot check, which is why this one cannot be validated |
| The cascade's `configure()` call | Resolves the `archive` alias and its router from this library's own spec | **Refused at boot.** No `archive` entry means every call would raise `ConnectionDoesNotExist` at first use |
| `LOCK_FUNC_MODULES` += `"evennia_archive.lockfuncs"` | Registers `owns_character()`, which the mixin writes into every character's locks | **Refused at boot.** The clause cannot resolve, so every ownership check evaluates false and an account is refused its own character |

## Optional settings

**There are none.** The library reads no setting of its own — the alias is always `archive`, a
constant rather than something a consumer names. Everything above is a Django or Evennia setting the
library validates rather than one it invents.

## 1. Install the package

```
pip install evennia-archive
```

The library logs through `evennia-logging-extension` and declares its database to
`evennia-database-cascade`; neither is on PyPI yet, so install both from their checkouts alongside:

```
pip install -e path/to/evennia-logging-extension
pip install -e path/to/evennia-database-cascade
```

## 2. Add the apps

```python
INSTALLED_APPS += ["evennia_archive", "evennia_database_cascade"]
```

Both: the library, and the cascade whose boot check verifies the alias it placed.

## 3. Let the cascade place the database

This library ships its declaration — the alias, its SQLite fallback `archive.db3`, and the
schema-clone constraint above. What remains for you is the cascade's one settings call and, per
deployment, an environment variable saying where the archive lives. Follow
[evennia-database-cascade's installing.md](../../evennia-database-cascade/docs/installing.md)
from step 4; nothing in it is archive-specific beyond one fact:

**The archive refuses the shared rung.** `DATABASE_URL` alone is an error for this alias — give it
`DATABASE_URL_ARCHIVE`, or set nothing and it lands in `server/archive.db3`.

## 4. Register the lock function

```python
LOCK_FUNC_MODULES = list(LOCK_FUNC_MODULES) + ["evennia_archive.lockfuncs"]
```

`list(...)` rather than `+=`: Evennia declares `LOCK_FUNC_MODULES` as a tuple, and `+=` with a list
raises `TypeError: can only concatenate tuple (not "list") to tuple` before the server starts.

`ArchivableAccountMixin` writes `owns_character()` into a character's `puppet`, `edit` and `delete`
locks, replacing the primary keys Evennia bakes in at creation — those name objects that no longer
exist after a restore, and the owning account is refused its own character with nothing in any log.

Without this line the clause cannot resolve and evaluates false, so every ownership check refuses.
That is the direction a missing registration should fail in, but it looks identical to a permissions
problem, so it is worth checking first if an account cannot puppet a character it owns.

## 5. Mix into your typeclasses

Settings alone archive nothing. **The library only archives objects whose typeclass carries one of
its mixins**, so mixing one in is how a consumer says which of their objects matter. Pick the one
matching what you are archiving:

```python
from evennia_archive.mixins import (
    ArchivableAccountMixin,
    ArchivableCharacterMixin,
    ArchivableObjectMixin,
)

class Character(ArchivableCharacterMixin, DefaultCharacter):
    pass

class Account(ArchivableAccountMixin, DefaultAccount):
    pass

class Ship(ArchivableObjectMixin, DefaultObject):
    pass
```

The mixin mints an `archive_id` when the object is created and never changes it afterwards — there is
no identifier to supply, generate, or keep unique.

| Mixin | Use it for |
|---|---|
| `ArchivableObjectMixin` | anything descending from `ObjectDB` — items, rooms, ships |
| `ArchivableCharacterMixin` | player characters, which an account creates and owns |
| `ArchivableAccountMixin` | accounts |

**`ArchivableCharacterMixin` is for players' characters only.** It declares that an account owns the
object, and `archive()` refuses one that names no owner. Most games type their NPCs and mobs as
Character subclasses to inherit combat and movement — those take `ArchivableObjectMixin`, which gives
them the same identity and the same round trip without the ownership.

If your typeclass already overrides the creation hook, call `super()` as usual and the identity is
still minted:

```python
def at_object_creation(self):
    super().at_object_creation()
    ...your own setup...
```

**Existing objects predate the mixin and have no identity.** Adding it to a typeclass affects objects
created from then on; anything already in your database needs `at_archive_init()` called on it once,
which is safe to run repeatedly and never overwrites an identity that already exists. The owner stamp
and the ownership locks are written at character creation too, so a character that predates
`ArchivableAccountMixin` keeps its primary-key locks until an account calls
`at_post_create_character(character)` on it — which is also what a game with its own chargen calls,
if it builds characters without going through `create_character`.

## 6. Migrate both databases

The archive is a **schema clone**: the same Evennia migrations, applied to a second database.

```
evennia cascade_migrate
```

One command: it runs the bare `migrate` for the game, then `migrate --database archive` — and the
same for any other cascade-placed alias you have installed. The two-step form
(`evennia migrate`, then `evennia migrate --database archive`) does the same by hand. Both steps are
needed; the second is not optional and not implied by the first.

**Verified behaviour** (Evennia 6.1.0, SQLite, `examples/demo_game`):

| | `evennia.db3` | `archive.db3` |
|---|---|---|
| Tables | 42 | 42 — identical set |
| Accounts | 1 | 0 |
| Objects | 0 | 0 |

All seven Evennia apps and Django's contrib apps migrate cleanly into a non-default alias. Nothing in
those migrations assumes `default`.

**The archive is left with a full schema and no rows**, which is the intended state. World setup —
Limbo, `#1`, `#2` — is not part of `migrate`; it runs from `run_initial_setup()` at server start. So
a database that is migrated but never started stays empty by construction.

That is also why **the archive must never be run as a game**. Start a server against it once and
Evennia will populate it.

## What is not checked for you

`check_settings()` refuses to start on a settings module it cannot work with, but two things sit
outside what it can see. They are yours to get right.

- **`INSTALLED_APPS`.** Leave the library out and `AppConfig.ready()` never runs, so the boot check
  never runs either. Nothing validates anything, and the library is simply inert.
- **Whether the archive has been migrated.** The alias's migrate run is a separate step and is not
  implied by the game's — `evennia cascade_migrate` covers both, the bare `evennia migrate` does
  not. Checking it would mean a database query inside `ready()`, which Django advises against. A
  missed migration surfaces as a missing-table error at the first archive.

One thing the boot check does cover, but only to the depth settings can express: **the archive and the
game being the same database.** It compares engine, name, host and port, so two entries reaching one
database under different hostnames pass. Closing that would need a query from `ready()` too.

## Two things worth knowing

**The superuser prompt targets `default` regardless of `--database`.** The launcher's database
bootstrap runs before it forwards the command to Django and takes no alias argument, so the account
it creates lands in the game database. This is normal Evennia behaviour, not something the archive
causes.

**A consumer's own routers keep their tables out of the archive**, provided those routers are active.
A router of the standard shape answers `allow_migrate(db="archive", app_label="<its own>")` with
`False`, so `migrate --database archive` will not drag that app's tables in. A consumer with no
routers at all gets their app tables in the archive as well — wasted tables rather than breakage.
