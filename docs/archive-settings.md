# Archive settings

What a consumer does to install this library — three settings entries and one change to their
typeclasses — why each is needed, and the one entry that will silently break an existing game if it
is copied carelessly.

Everything here is built and tested. The demo gamedir under `examples/` uses this document verbatim,
so these instructions are what gets exercised rather than a paraphrase of them.

## What a consumer declares

Three entries, in the consumer's `server/conf/settings.py`:

```python
# 1. The app
INSTALLED_APPS += ["evennia_archive"]

# 2. The archive database — a second Evennia schema, never run as a game
DATABASES["archive"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.path.join(GAME_DIR, "server", "archive.db3"),
}

# 3. The router — append, never assign. See below.
_ARCHIVE_ROUTER = "evennia_archive.db_router.ArchiveRouter"
DATABASE_ROUTERS = list(globals().get("DATABASE_ROUTERS", []))
if _ARCHIVE_ROUTER not in DATABASE_ROUTERS:
    DATABASE_ROUTERS.append(_ARCHIVE_ROUTER)
```

The router is a dotted-path string, exactly like an app entry — the class ships with the library and
a consumer never writes one.

## The lock function

Add the library's lock functions alongside your own:

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

## Marking typeclasses as archivable

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

> **Working assumption — not locked in.** Whether the library ships an `archive_database(GAME_DIR)`
> helper that collapses entry 2 to one line and resolves a `DATABASE_URL_ARCHIVE` override for
> Postgres.

**The alias is `archive`**, and the app is `evennia_archive`. Sibling routers use one name for both,
which is why `ArchiveRouter` carries `app_label` and `alias` as separate attributes — conflating them
would route the library's own models to an alias that does not exist.

## Migrating the archive

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

## Two things worth knowing

**The superuser prompt targets `default` regardless of `--database`.** The launcher's database
bootstrap runs before it forwards the command to Django and takes no alias argument, so the account
it creates lands in the game database. This is normal Evennia behaviour, not something the archive
causes.

**A consumer's own routers keep their tables out of the archive**, provided those routers are active.
A router of the standard shape answers `allow_migrate(db="archive", app_label="<its own>")` with
`False`, so `migrate --database archive` will not drag that app's tables in. A consumer with no
routers at all gets their app tables in the archive as well — wasted tables rather than breakage.
