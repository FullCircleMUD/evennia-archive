# Design

The whole design of `evennia-archive` in one document: what it holds, what it refuses to hold, the
interface a consumer calls, and the reference-translation problem that is the substance of the work.
It is written as the intended finished state rather than as a record of what is built. Much of it now
is — see [progress.md](progress.md) for what exists and what proves it.

## How to read this document

Sections are written as settled design. Where something is **not** settled, a box sits above it:

> **Working assumption — not locked in.** The shape is agreed but the detail could still move.

> **Unknown — needs a spike.** We do not yet know the shape. Named here so it is not mistaken for a
> decision.

As the design is proven and locked in, the boxes are deleted. When the last box is gone the document
is the specification.

This is a deliberate departure from the usual documentation discipline, which forbids writing
unagreed material as settled. The boxes are what make it honest — **do not remove one without the
project owner confirming that section is locked.**

---

## The problem

Evennia identity is the `ObjectDB` primary key. Rebuild a world and those keys are re-issued from
scratch, so an object's rows cannot simply be restored into the new database: every reference inside
them points at something that no longer exists. That includes references buried inside pickled
attribute values, which are invisible to SQL.

The consequence is that "rebuild the world from source" and "keep the players" are hard to have at the
same time. This library exists to make them compatible.

## Scope — the governing principle

**The archive holds what cannot be derived. The consumer supplies what can.**

An object's own state — its attributes, its progression, its identity — exists nowhere else. Lose it
and it is gone. Where the object was standing is a policy the consumer can reapply from scratch: a
home room, a start room, a placement script.

That single sentence answers every future "why doesn't it restore X". It is also why this is **not a
database backup** and not a substitute for one. A database restore gives you back the data, not the
running system; so does this.

**Archival is shallow**: the object's own row, its attributes and its tags. Contained objects are not
followed. `archive()` takes one object and copies that object, so a consumer who wants an inventory
archived calls it for each item. Deep archival would mean the library deciding how far a containment
tree extends and which parts of it matter, which is a game question wearing a library's clothes.

Containment is the one case where location genuinely *is* unrecoverable state — "this was inside that
chest". That is handled as a disposition rather than a special rule: if the container is also
archived, the reference translates through its identifier like any other.

## The archive database

A second Evennia database on **the same schema**, migrated alongside the game, and **never run as a
game** — no world build, no scripts, no mobs, nothing instantiated.

Because both ends share a schema, attribute values move across as opaque bytes and are never parsed.
That is the central simplification and it is worth stating why the alternative was rejected: a
serialised export format would have to unpickle every value, which needs a class allowlist, shims for
consumer-defined classes, and ongoing maintenance as new attributes appear. A schema clone captures a
new attribute automatically, with no code change and nothing to keep in step.

**A model-based router cannot route reads and writes here.** Both databases hold the same models, so
there is no model-level fact distinguishing a live character from an archived one — nothing for
`db_for_read` to branch on. Access to archived rows is therefore explicit, via `.using("archive")`.
The router's job for Evennia's tables is `allow_migrate` and nothing else.

The alias is declared by the consumer and the schema is built by a second migrate call. Both are
documented in [archive-settings.md](archive-settings.md), and the demo gamedir under `examples/` uses
that document verbatim so the instructions are what gets tested.

> **Unknown — needs a spike.** How this behaves in a project whose *other* apps already have routers.
> Reading FCM's suggests it is fine — a router of that shape answers `False` for its own app against
> the archive and `None` for everything else, which both keeps its tables out and lets Evennia's in —
> but that is reasoning from source, not a test. The demo gamedir has no other routers to exercise it.

## The archive holds rows, not objects

Two rules govern every operation that touches the archive. Both exist because Evennia's model layer
assumes there is one database, and the archive is a second one wearing the same schema.

### Never instantiate a row fetched from the archive

Evennia's models are `SharedMemoryModel`, and the idmapper caches instances **by primary key alone**,
with no idea which database a row came from. So this is not safe:

```python
copy = ObjectDB.objects.using("archive").get(pk=5)   # may be the LIVE object with pk 5
```

`.using()` steers the query; it does not steer the cache. If the live database has a row with that
key and it is already cached, the cached instance comes back instead — and anything done to it lands
in the real game database. `copy.db_attributes.all().delete()` would then destroy a live character's
attributes while looking like archive maintenance.

This is reached in ordinary use, not at the edges. A fresh archive and a live database both start
counting primary keys at 1, so collisions are the normal case rather than the unlucky one — a read
for an archived character can hand back a room. And it is silent both ways: without a collision the
same code passes its tests.

**So: reads use `.values()` / `.values_list()`, writes use explicit querysets scoped with `.using()`,
and many-to-many work goes at the through table directly** — reaching a row's own m2m manager means
having the instance, which is the unsafe thing.

A useful test of any new archive code: if it holds a model instance loaded from the archive, it is
wrong.

### Never write through Evennia's save path

Evennia's typeclass hooks hang off `TypedObject.save()`. A plain ORM `create()` fires `at_first_save`
and therefore `at_object_creation` — so an archived copy would run its typeclass's creation logic,
including this library's own identity minting, on a row that is a copy rather than a new object.

**So: `bulk_create()` rather than `create()`, and queryset `update()` rather than `save()`.** Both
issue SQL without going through `save()`.

The principle underneath both rules is the same. **A copy is data, not an object.** It has no
location, nothing puppets it, no scripts tick on it, and nothing should fire hooks at it. The moment
the library treats an archived row as a live object, it stops being an archive and starts being a
second game.

## The library's own table

Alongside the cloned Evennia schema, the library owns exactly one table of its own — one row per
archived object.

| Column | |
|---|---|
| `archive_id` | Primary key. The stable identifier, immutable, and the join key to everything else |
| `archived_model` | Which archive table the copy sits in — `"objectdb"`, `"accountdb"` |
| `archived_pk` | Primary key of the copy *within the archive database* |
| `last_archived` | When the copy was last written. Not null — a row exists because something was archived |
| `last_restored` | When it was last restored. Nullable; most objects never are |

**`archived_model` and `archived_pk` make this the index into the archive**, not merely bookkeeping.
Primary keys in the live database are worthless across a rebuild — that is the problem the library
exists to solve — but the archive is never torn down, so a key *inside it* is stable by construction.
Recording it turns "I have an `archive_id`, find the row" into a direct hit rather than an attribute
lookup. The model is needed alongside the key because the archive holds several tables and a bare
primary key is ambiguous between them.

It also makes `archive()` an upsert: read the identity off the incoming object, look for a record,
and either update the row it points at or insert a new copy and record where it landed.

**`last_archived` earns its place because there is nowhere else to put it.** `ObjectDB` carries only
`db_date_created`, which copies to the archive verbatim and records when the object was *made*, not
when it was archived. Attribute values are pickled, so storing it there makes it unqueryable in SQL.
Without this column, *"which objects have a stale or missing archive?"* — the sweep that recovers
from a failed archive run — cannot be written.

**`last_restored` earns its place because it records an event.** No query needs it today, but events
that go unrecorded are unrecoverable: add the column later and every restore before then is simply
unknown. The concrete case is support — *"a player says their character came back wrong"* — where the
first question is when it was restored.

This is a **state table, not a log**. A hundred archives of the same object produce one row with a
moving timestamp, not a hundred rows. It is bounded by object count rather than event count, so it
needs no retention policy.

**It lives in the archive database, not the live one** — otherwise it is destroyed by the very
rebuild it exists to help recover from.

## The router

The library's table is why a router exists at all. Without a table of its own there would be nothing
to route, and the archive would work with no router declared.

It follows the shape already established in FCM's `xrpl` and `ai_memory` routers, with two
differences that matter:

- **Two attributes, not one.** Those routers use a single `app_label` that doubles as the alias name.
  Here the app is `evennia_archive` and the alias is `archive`, so they must be separate — conflating
  them routes the library's own models to an alias that does not exist.
- **`exclusive = False`.** Those routers carry a clause meaning *"nothing but my models may enter my
  database"*. Inverted here: Evennia's models are precisely what belongs in the archive. Copying that
  clause would make the router refuse the thing the archive is for.

Everything else transfers unchanged — including returning `None` to defer, which is what lets it
coexist with a consumer's own routers rather than fighting them.

**The consumer declares the router**, alongside the app and the alias. The library does not reach
into their settings to append it. `evennia-shards` does inject settings from `AppConfig.ready()`, but
`DATABASE_ROUTERS` is the wrong setting to do it to: `django.db.router.routers` is a
`cached_property`, so anything that touches the ORM before `ready()` runs snapshots the list without
us in it and the router silently never applies. A visible line in the consumer's settings cannot fail
that way. See [archive-settings.md](archive-settings.md).

## Identity

**The library's one requirement: a mixin on every typeclass the consumer wants to be archivable.**

The mixin declares the identifier **and mints it**. It hooks the object's creation, generates a UUID
at that moment, and stores it. From then on the value never changes. It is how a row in the live
database and a row in the archive are known to be the same thing, since primary keys are meaningless
across the two.

Minting is the library's job, not the consumer's. Adding the mixin is the whole of what a consumer
does — there is no identifier to supply, generate or keep unique.

Requiring a mixin rather than inferring archivability means the library never guesses. An object
without it is not archivable, and that is a consumer decision expressed in their own typeclass
definitions.

There are three to pick from, because Evennia calls a different creation hook for each kind and
because an account has a second job the others do not.

### As built

`ArchivableObjectMixin`, `ArchivableCharacterMixin` and `ArchivableAccountMixin` in
`evennia_archive.mixins`, all children of `ArchivableBaseMixin`, which owns the identity. A
consumer picks the one matching what they are archiving; the kinds differ in which creation hook
Evennia calls, and in what each needs beyond identity.

| | |
|---|---|
| Attribute key | `archive_id` — the one name in the library that cannot be revised after release |
| Value | `str(uuid.uuid4())` — canonical, lowercase, hyphenated |
| Storage | `strattr=True`, so it lands in `db_strvalue` **unpickled** |
| Minted | In `at_object_creation` / `at_account_creation`, or `at_archive_init()` explicitly |
| Type returned | `str`, not `uuid.UUID` |

| Mixin | For | Adds beyond identity |
|---|---|---|
| `ArchivableObjectMixin` | anything descending from `ObjectDB` | — |
| `ArchivableCharacterMixin` | player characters an account owns | `owner_account_archive_id` |
| `ArchivableAccountMixin` | accounts | stamps its characters, and writes their ownership locks |
| `ArchivableBaseMixin` | the identity itself | not for direct use — its creation hooks refuse |

**The base refuses rather than doing nothing.** A typeclass carrying it directly would exist with no
identity and only reveal that at the first archive, so its creation hooks raise instead, naming the
mixin that should have been used. A true abstract base is not available: Evennia's typeclasses carry
the `TypeclassBase` metaclass, and adding `ABCMeta` to it is a metaclass conflict at class
definition, while `@abstractmethod` without `ABCMeta` enforces nothing.

The cost is that the library's own children cannot call plain `super()` from a creation hook — the
mixin chain precedes Evennia's class in the MRO, so it would land on the refusal. They use
`super(ArchivableBaseMixin, self)`, which resumes after the base. **A consumer overriding a creation
hook is unaffected** and calls `super()` as normal, because that lands on the kind-specific mixin
rather than on the base.

**Why unpickled matters more than it looks.** A pickled attribute is compared by pickling the search
term and matching bytes, which only holds while the same value always serialises identically — across
a protocol change it can quietly stop matching, and a lookup that silently returns nothing is the
worst possible failure for a recovery path. Unpickled storage also means the column can be read by
hand during exactly the incidents this library exists for.

**Why canonical form is enforced at minting.** String equality is case- and format-sensitive where
`uuid.UUID` comparison is not, so `"F47AC10B…"` and `"f47ac10b…"` would fail to match despite being
the same identifier. Every value goes through one minting path, so every stored value has one form.

**The mixins ship both creation conventions.** They override the creation hooks themselves, *and*
expose `at_archive_init()` for a typeclass that already overrides them. A library cannot assume the
consumer's MRO, so it cannot pick one.

### Character ownership

A character belongs to an account, and that link has to survive a restore. `db_account` cannot carry
it — a primary key means nothing in the other database, so the archive drops it like every other
dbref. `ArchivableAccountMixin.at_post_create_character` stamps the character with the account's
`archive_id` instead, in the one place both objects are in hand.

The same hook rewrites the character's ownership locks. Evennia writes those at creation with
primary keys as literals:

```
puppet:id(3) or pid(2) or perm(Developer) or pperm(Developer)
```

Both keys change on every restore, so the locks come back naming objects that no longer exist and the
owning account is refused its own character, with nothing in any log. They are replaced with a lock
function the library ships:

```
puppet:owns_character() or perm(Developer) or pperm(Developer)
edit:owns_character() or perm(Admin)
delete:owns_character() or perm(Admin)
```

`owns_character()` compares the accessor's `archive_id` to the character's owner stamp. It takes no
argument, so the lock and the stamp cannot come to name different accounts. A consumer registers it
in `LOCK_FUNC_MODULES` — see [archive-settings.md](archive-settings.md) — and a missing registration
refuses everyone rather than admitting them.

The permission clauses are Evennia's own, kept so an administrator and a superuser get in exactly as
before. Access types the rewrite does not name are untouched: `locks.add` is an upsert per access
type.

**Wearing the character mixin means having an owner.** `archive()` refuses a character-mixin object
with no stamp, naming `ArchivableObjectMixin` — the declaration is for player characters, and an NPC
typed as a Character belongs on the object mixin. The check cannot happen at creation, because at
`at_object_creation` there is no account reference to test against.

Both decisions are pinned by tests — the minted value must round-trip through `uuid.UUID` unchanged,
and it must land in `db_strvalue` with `db_value` null.


## Public interface

Two calls. Everything else — when, how often, in response to what — belongs to the consumer.

```python
archive(obj)                                                       # obj must carry the mixin
find(key, value, model=None)                                       # → [archive_id, ...]
restore(archive_id, return_object=True)
delete(archive_id)                                                 # removes the archived copy
```

`archive()` takes an object and copies it. It does not know or care whether that object is a
character, an account, a ship or a guild hall.

`restore()` cannot take an object, because the object does not exist yet. It takes the identifier.

`delete()` removes the archived copy and its bookkeeping row. It takes an identifier rather than an
object, which also keeps it unambiguous against Evennia's `obj.delete()` — nothing in this library
ever deletes a live object.

### Finding an archive_id

`restore()` needs an identifier the caller does not have. A returning player arrives with something
their game recognises — a wallet address, a username, an email — and the archive has to be searchable
by it.

**`find()` searches the archive by an attribute the consumer nominates and returns a list.** A list
rather than a single result, because the library cannot know whether the consumer's field is unique.
Choosing between several matches is the consumer's job, as is knowing that their field *is* unique
and taking the first.

FCM's flow is the motivating case: a player signs with their Xaman wallet, no account exists on the
live server, so the archive is searched by wallet address. A hit means restore; a miss means create a
new account. The library never learns what a wallet is.

**Two lookup paths exist, with very different costs**, and the difference should be exposed rather
than hidden:

| Column | |
|---|---|
| `db_strvalue` | Plain varchar — queryable by direct string equality, with no serialisation in the path. Not itself indexed. Most Attributes do not set it |
| `db_value` | Pickled. Works, because Django pickles the search term to compare, but Evennia's own docstring calls it *"not a very efficient operation"* and no index helps |

So a consumer whose identifying field lands in `strvalue` gets a lookup that scales; one using a
normal attribute gets a table scan.

### find() is the expensive call — defer it

**Wrap `find()` in `deferToThread`.** It is the one call in this library that can block long enough to
be felt by every connected player, and the reasons are structural rather than incidental:

- **Neither value column is indexed.** `db_key` is, so a search narrows to attributes of that name
  first — but if the key is a common one, the surviving set is still large and every row in it gets
  compared.
- **A pickled comparison is a blob comparison.** Where an unpickled search matches a short string,
  a pickled one matches serialised bytes, and a large attribute value is a large comparison.
- **It may search more than one model.** Left unnarrowed, the cost multiplies by the number of models
  the archive holds.
- **The archive may not be local.** It is expressly designed to be movable onto separate compute, at
  which point every query carries network latency the live database does not.

None of that is a problem when it runs on a worker thread, and all of it is when it runs on the
reactor. A consumer who defers nothing else should still defer this.

For contrast, the others are cheap: `restore()` and `delete()` resolve through `ArchiveRecord`'s
primary key, and `archive()` writes a bounded number of rows for one object. `restore()` does carry
one attribute lookup of its own — the check that stops a re-run duplicating — but that narrows on
`archive_id`, which is unpickled and matches at most one row.

Implementation notes for when this is written:

- **Evennia's `get_by_attribute()` cannot be used directly.** It is a *manager* method ending in
  `self.filter(...)`, and `.using("archive")` returns a QuerySet, which carries no manager methods.
  The library re-expresses the same filter with `.using()` itself — a few lines, and it drops a
  dependency on Evennia's manager surface.
- **It is two hops.** Match the archived object on the consumer's attribute, then read `archive_id`
  off that same object. The second hop wants a `values_list` join rather than loading the objects,
  so that searching the archive does not instantiate archived objects on the searching process.

```python
find(key, value, model=None)
```

**No storage mode to choose.** An attribute stored with `strattr` has `db_strvalue` set and
`db_value` null; a normal one is the reverse. So the query matches *either* column and is correct
whichever way the consumer stored it — one query, no mode flag, nothing for a caller to get wrong.

**`model` is optional.** The archive knows which models it holds, because `ArchiveRecord.archived_model`
records them, so an unnarrowed search covers all of them. Narrowing is an optimisation the consumer
can apply when they know — a game searching by wallet address knows it wants accounts.

**A pickled comparison is type-sensitive, and that is the caller's to get right.** `find("level", 12)`
matches an attribute stored as the integer 12; `find("level", "12")` matches nothing, silently. The
library has no way to know what type an attribute holds, and guessing — coercing the term, or trying
several types — would produce false matches quietly, which is worse than none. Search with a value of
the type you stored.

The library's own lookups are immune, because `archive_id` is stored unpickled and compared as a
plain string. That was chosen for protocol stability; type-insensitivity is a second dividend.

> **Unknown — needs a spike.** Whether attribute lookup behaves identically against a non-default
> alias. The query shape is ordinary Django, but the pickled-value comparison has not been tested
> across `.using()`.

> **Unknown — a gap this raises.** A consumer with several matches has to choose between them, and
> `archive_id` alone tells them nothing. They would need to see something per candidate — key,
> typeclass, when it was last archived — without restoring it. That implies a read-only peek
> operation. Not needed for FCM, where the wallet is unique, but a generic consumer with a
> non-unique field meets it immediately.

### Deletion is offered, not mandated

The library provides deletion; whether to use it is the consumer's policy. A game where a player
destroys a character permanently calls `delete()` from its own delete hook. A game that wants
destroyed characters to remain recoverable simply doesn't.

**The consequence has to be a chosen one, not a discovered one: if `delete()` is never called,
archived copies outlive the objects they came from and will be restored.** A player who deliberately
destroyed a character gets it back after the next rebuild.

Deletion is a **hard delete**, not a flag. A soft-deleted row makes correctness depend on every
restore query remembering a filter, and forgetting it once resurrects characters players chose to
destroy. Removing the row makes that structurally impossible.

This does not contradict [the rule that nothing prunes the archive](#the-archive-database). That rule
forbids *housekeeping* — a sweep that removes rows with no live counterpart, which is exactly how the
player who returns after three years is lost. An explicit `delete()` call is an instruction, not a
sweep guessing. Both hold: never prune, always honour an explicit delete.

The cost is that deletion is irreversible — a mistaken or malicious delete cannot be undone. Worth
stating plainly, though it is not a regression, since destroying an object without an archive is
equally final.

### What restore returns

The row is written through the ORM, so an instance always exists momentarily. The parameter governs
what happens to that instance, not whether it is built:

- **`return_object=True`** (default) — the instance is returned. The caller gets `.id` for free and
  avoids a round-trip.
- **`return_object=False`** — the instance is evicted from Evennia's idmapper cache and the primary
  key is returned instead.

```python
obj = <insert via ORM>
if return_object:
    return obj
obj.flush_from_cache(force=True)   # evicts the cache entry; touches no row
return obj.id
```

The second mode exists because some consumers must not leave instances resident on the process that
runs the restore — a routing process not permitted to hold game objects, for instance. That is a real
constraint, and the library serves it without needing to know why. A single-process game ignores the
parameter entirely.

Three details that matter to anyone reading the implementation:

- **The parameter is named for the return, not the insert.** Instantiation happens either way, so
  `instantiate=False` would misdescribe what the code does.
- **`flush_from_cache()` is not `delete()`.** It removes the process-wide cache entry and leaves the
  row untouched. Reading `obj.id` afterwards is safe — the instance itself is intact, and the local
  reference drops when the function returns.
- **`force=True` is deliberate.** A typeclass can veto a non-forced flush by returning `False` from
  `at_idmapper_flush()`, which exists for objects managing their own lifecycle. Forcing is safe here
  because nothing is puppeting a freshly restored object.

The flush guarantees the instance is not left cached *by this call*. Anything that later loads that
primary key creates and caches a fresh instance, which is inherent and expected.

## Reference translation

The substance of the library. Every stored reference is a primary key that will be wrong in the
target database.

**Two categories, two discovery methods:**

| Category | Where | How it is found |
|---|---|---|
| Column-level | `db_location_id`, `db_home_id`, `db_destination_id`, M2M link tables | Schema introspection |
| Inside pickled values | Anywhere an attribute holds an object reference | Scanning the raw bytes for Evennia's packed markers |

**The rule, written once:** drop the primary key, keep the stable identifier if one exists. As new
kinds of stable identifier appear, they slot into the same mechanism without it changing.

**Direction matters.** On the way *in*, references are neutralised — replaced by the target's stable
identifier. The archive never runs, so its pointers never need to resolve, and this removes any need
for a bidirectional key map. On the way *out*, they are reconstructed against whatever keys the target
database issued.

**Four dispositions:**

- **Translate** — resolve to the equivalent object's new key, via its stable identifier
- **Zero** — deliberately drop it
- **Defer** — resolve in a second pass, once the target object exists
- **Regenerate** — do not carry it at all; rebuild it from a relationship already known

> **Working assumption — not locked in.** How a consumer declares dispositions is open — per
> typeclass, per field, a registry, or defaults with overrides. What is agreed is that the *library*
> does not decide them, because which references matter is a game question.
>
> If it turns out to be per typeclass, the lookup must walk the **MRO** rather than match the
> concrete class, so a consumer registering against `MyCharacter` still matches `MyEliteCharacter`
> and mixin composition behaves as expected.

> **Unknown — needs a spike.** The full set of packed markers Evennia uses. `__packed_dbobj__` is
> confirmed; `dbserialize` must be read for the rest. A missed marker is a reference that copies
> verbatim and silently points at the wrong object after restore.

> **Unknown — needs a spike.** Whether the reference survey can run as a repeating check rather than a
> one-off audit. It needs to, because consumers will add attributes the survey has never seen — but
> whether that is a management command, a test helper, or something else is not decided.

## Restore semantics

**A restored object comes back stripped of every dbref it once held** — location, home, destination,
owning account. Those were primary keys into a database that has since been rebuilt, so they point at
nothing, or worse, at whatever now happens to hold that key.

`None` is a legal state in Evennia, not an error one: the object exists, it is queryable, it is
simply nowhere. Nothing is at risk of being lost, because restore hands back the object itself.

**Where a restored object goes is entirely the consumer's business.** The library stores it and gives
it back; deciding where it belongs and what it reattaches to is a game decision, and the consumer is
better placed to make it than any library could be.

### A name taken while its owner was away

An account's username is unique, and a world rebuild frees every name in it. So a player who stops
playing, has their world rebuilt, and comes back a year later may find someone else holding the name
they left behind.

**The restore proceeds under a numbered name rather than failing.** `rowan` becomes `rowan1`, then
`rowan2`, until one is free. The reasoning is a judgement about what players actually mind losing: a
name is recoverable — they can be offered another — while levels, skills and progression are not, and
refusing the restore would cost them both.

The value that could not be kept is recorded on the restored object under `archive_renamed_from`, as
`{field: original}`. That is deliberately durable rather than a callback: the game can act on it at
restore time, or leave it until the player next logs in and offer them a rename then. Clearing it
afterwards is the consumer's business.

**This only ever affects accounts, which is the part that makes it acceptable.** A player is attached
to their character's name — that is who they appear as in the world — and a character name is never
touched: `ObjectDB` declares no unique fields, so Rowan comes back as Rowan even with another Rowan
standing next to him. The only name the library can ever change is the account one, which players see
least and are least attached to.

`username` is the single unique field Evennia declares outside primary keys, so a character restore
can never be blocked. Nor can a consumer add more, because typeclass state lives in Attributes rather
than in the schema.

Collisions are detected by asking the model which of its fields are unique and querying each, not by
catching `IntegrityError` and reading it. That message format differs between SQLite and Postgres and
across Django versions, so parsing it to learn which column collided is guesswork where the model
knows exactly.

`restore()` therefore takes no placement arguments, and does not fall back to
`settings.DEFAULT_HOME` — that resolves to Limbo, which is a game concept. `location` and `home` are
`ObjectDB` concepts in any case: accounts have neither column, so an API carrying them would have to
refuse arguments it could not apply.

The consumer loses nothing by this. They can set `obj.location` directly and fire no hooks, or call
`move_to()` and fire them deliberately — either is more control than a parameter would offer.

## What the consumer owns

The library ships no scheduler, no hooks and no triggers. All of the following are consumer code:

- **When to archive.** A logout hook, a periodic script, an admin command, a save point — the library
  is indifferent.
- **Which thread it runs on.** Every call in this library is a plain synchronous function. It imports
  no Twisted and assumes no reactor, because a management command, a migration and a test have none.
  Dispatching off the reactor is the consumer's decision for the same reason scheduling is — and on
  Evennia that means `threads.deferToThread(archive, obj)` at the callsite. **`find()` in particular
  should be deferred; see below for why.**
- **What to archive.** Which typeclasses carry the mixin, and which objects get passed to `archive()`.
- **Failure handling.** What to log, what to retry, what to alarm on.
- **Reconciliation.** Detecting archived objects with no live counterpart, or the reverse.
- **Placement.** Where a restored object goes and what happens to it afterwards.

This is the mechanism/policy split, and it is why the library can be useful to a game with none of
FCM's concepts in it.

## Optional capabilities

Where the library can do better given more information, it asks **the object**, never the environment.

Concretely: if a location exposes a stable identifier, a restored object can be returned to exactly
where it was rather than left at `None`. The check is whether *this object* offers that identifier —
never whether some library is installed. Detecting a library is a hidden dependency wearing an
optional one's clothes, and it would also exclude a consumer who solved the same problem their own
way.

Every optional capability degrades to the plain behaviour rather than becoming a requirement.

## Non-goals

- **Database backups.** Different job, different tool.
- **Deciding what is archivable.** The mixin is the consumer's declaration.
- **Deciding where restored objects go.**
- **Scheduling anything.**

## Open questions and spikes

Collected from the boxes above, so they can be worked through deliberately.

**Spikes:**

1. Behaviour alongside a consumer's existing routers — reasoned from source, never run.
2. Whether attribute lookup behaves identically against a non-default alias. The query shape is
   ordinary Django, but the pickled-value comparison is untested across `.using()`.
3. The full set of Evennia packed markers, from `dbserialize`.
4. How the reference survey runs repeatedly rather than once.

**Decisions:**

1. How consumers declare dispositions.
2. Whether the library ships an `archive_database()` helper for the alias declaration.

**Not yet examined at all:**

- A read-only peek at an archived object, so a consumer whose lookup field is not unique can choose
  between candidates without restoring all of them.
- Behaviour under a sharded consumer, where the live game spans several databases.
- Whether the archive should hold history or only the latest state of each object.
- A bulk restore call. It would use `bulk_create` and build no instances, so its return type is its
  own question rather than an extension of `return_object`.
