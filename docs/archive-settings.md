# Archive settings

What a consumer does to install this library — three settings entries and one change to their
typeclasses — why each is needed, and the one entry that will silently break an existing game if it
is copied carelessly.

> **Working assumption — not locked in.** Whether the router stays a consumer-declared entry or is
> appended by the library from `AppConfig.ready()`. If it moves, entry 3 disappears from this
> document. Everything else here is built and tested.

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

## Marking typeclasses as archivable

Settings alone archive nothing. **The library only archives objects whose typeclass carries
`ArchivableMixin`**, so mixing it in is how a consumer says which of their objects matter:

```python
from evennia_archive.mixins import ArchivableMixin

class Character(ArchivableMixin, DefaultCharacter):
    pass
```

That is the whole of it. The mixin mints an `archive_id` when the object is created and never changes
it afterwards — there is no identifier to supply, generate, or keep unique.

If your typeclass already overrides the creation hook, call the initialiser from it instead:

```python
def at_object_creation(self):
    super().at_object_creation()
    self.at_archive_init()
```

**Existing objects predate the mixin and have no identity.** Adding it to a typeclass affects objects
created from then on; anything already in your database needs `at_archive_init()` called on it once,
which is safe to run repeatedly and never overwrites an identity that already exists.

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

> **Working assumption — not locked in.** The library may instead append its own router from
> `AppConfig.ready()`, which is the pattern `evennia-shards` uses for middleware and portal plugins.
> That would remove entry 3 from this document entirely. It is untested: `django.db.router.routers`
> is a `cached_property`, so if anything touches the ORM before `ready()` runs, the append lands on a
> list Django has already snapshotted and the router silently never applies.

> **Working assumption — not locked in.** Whether the library ships an `archive_database(GAME_DIR)`
> helper that collapses entry 2 to one line and resolves a `DATABASE_URL_ARCHIVE` override for
> Postgres.

> **Working assumption — not locked in.** The alias name. `archive` reads better in code;
> `evennia_archive` would match the sibling convention where a router's alias equals its app label.

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
