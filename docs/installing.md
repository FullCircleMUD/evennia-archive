# Installing

What a consumer does to install this library — four settings entries and one change to their
typeclasses — why each is needed, and the one entry that will silently break an existing game if it
is copied carelessly.

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

If you resolve database connections from environment variables — a per-alias URL, then a shared one,
then a local file — **the archive cannot use the shared rung.** Give it its own URL or its own file.

The library refuses to start when the two resolve to the same database, so this fails loudly at boot
rather than quietly at the first restore. The check compares engine, name, host and port; two entries
that reach one database under different hostnames would pass it, so the constraint is yours to hold
as well.

## Required settings

Four entries, all in the consumer's `server/conf/settings.py`. Two of them the library refuses to
start without.

| Setting | What it does | Without it |
|---|---|---|
| `INSTALLED_APPS` += `"evennia_archive"` | Loads the app, its model and its `ready()` hook | Nothing runs — including the boot check, which is why this one cannot be validated |
| `DATABASES["archive"]` | The second Evennia schema. Must be a different database from `default` | **Refused at boot.** Without it every call raises `ConnectionDoesNotExist` at first use |
| `DATABASE_ROUTERS` += `ArchiveRouter` | Keeps `evennia_archive`'s own table out of the game database at migrate time | A stray `evennia_archive_archiverecord` in the game database. Not checked — see below |
| `LOCK_FUNC_MODULES` += `"evennia_archive.lockfuncs"` | Registers `owns_character()`, which the mixin writes into every character's locks | **Refused at boot.** The clause cannot resolve, so every ownership check evaluates false and an account is refused its own character |

## Optional settings

**There are none.** The library reads no setting of its own — the alias is always `archive`, a
constant rather than something a consumer names. Everything above is a Django or Evennia setting the
library validates rather than one it invents.

## 1. Install the package

```
pip install evennia-archive
```

## 2. Add the app

```python
INSTALLED_APPS += ["evennia_archive"]
```

## 3. Declare the archive database

```python
DATABASES["archive"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.path.join(GAME_DIR, "server", "archive.db3"),
}
```

Read the section above this one first if you have not. This entry must name a **different database**
from `default`, and the library refuses to start if it does not.

## 4. Add the router

```python
_ARCHIVE_ROUTER = "evennia_archive.db_router.ArchiveRouter"
DATABASE_ROUTERS = list(globals().get("DATABASE_ROUTERS", []))
if _ARCHIVE_ROUTER not in DATABASE_ROUTERS:
    DATABASE_ROUTERS.append(_ARCHIVE_ROUTER)
```

Append, never assign — see *Why the router list is appended* below. The router is a dotted-path
string, exactly like an app entry; the class ships with the library and a consumer never writes one.

## 5. Register the lock function

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

## 6. Mix into your typeclasses

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

## 7. Migrate both databases

The archive is a **schema clone**: the same Evennia migrations, applied to a second database.

```
evennia migrate                      # the game, as normal
evennia migrate --database archive   # the archive
```

Both are needed. The second is not optional and not implied by the first.

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

## Why the router list is appended, never assigned

Evennia does not define `DATABASE_ROUTERS` at all, so a game with no routers of its own could get
away with a plain assignment. **A game that already has routers cannot.**

The failure is silent and expensive. A consumer with existing routers who writes
`DATABASE_ROUTERS = ["evennia_archive.db_router.ArchiveRouter"]` replaces their list rather than
extending it. Every model those routers were steering now falls back to `default` — no exception, no
warning, just reads and writes landing in the wrong database until someone notices the data isn't
there.

The membership check also makes the block idempotent, so a settings module imported twice cannot
stack duplicate routers.

## Why the consumer declares the router rather than the library injecting it

`evennia-shards` appends its own middleware and portal plugins from `AppConfig.ready()`, so the
technique is proven in a sibling. `DATABASE_ROUTERS` is the wrong setting to use it on.

`django.db.router.routers` is a `cached_property`. Anything that touches the ORM before our
`ready()` runs snapshots the router list without us in it, and the router then silently never
applies — no error, no warning, tables quietly landing in the wrong database. Middleware does not
have that problem because Django assembles that chain at first request, long after `ready()`.

A visible line in the consumer's settings cannot fail that way, and they are already editing that
block for the app and the alias.

**The alias is `archive`**, and the app is `evennia_archive`. Sibling routers use one name for both,
which is why `ArchiveRouter` carries `app_label` and `alias` as separate attributes — conflating them
would route the library's own models to an alias that does not exist.

## What is not checked for you

`check_settings()` refuses to start on a settings module it cannot work with, but three things sit
outside what it can see. They are yours to get right.

- **`INSTALLED_APPS`.** Leave the library out and `AppConfig.ready()` never runs, so the boot check
  never runs either. Nothing validates anything, and the library is simply inert.
- **Whether the archive has been migrated.** `evennia migrate --database archive` is a separate
  command and is not implied by a bare `migrate`. Checking it would mean a database query inside
  `ready()`, which Django advises against. A missed migration surfaces as a missing-table error at
  the first archive.
- **The router.** Its absence leaves a stray `evennia_archive_archiverecord` in the game database and
  breaks nothing — every query this library issues names the alias explicitly. It is not checked
  because refusing to start over it would be disproportionate.

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
