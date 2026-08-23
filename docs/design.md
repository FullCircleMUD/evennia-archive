# Design

The whole design of `evennia-archive` in one document: what it holds, what it refuses to hold, the
interface a consumer calls, and the reference-translation problem that is the substance of the work.
It is written as the intended finished state rather than as a record of what is built, because nothing
is built yet.

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

> **Working assumption — not locked in.** Archival is **shallow**: the object's own row, its
> attributes and its tags. Contained objects are not followed. A consumer who wants an inventory
> archived calls `archive()` for each item. Deep archival would mean the library deciding how far a
> containment tree extends, which is a game question.

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

> **Unknown — needs a spike.** How the second database is declared and migrated is not established.
> Open: whether a database router is required at all; whether `migrate --database archive` builds the
> full Evennia schema into the alias; how a consumer declares the alias without the library reaching
> into their settings; and how this behaves when other apps in the consumer's project already have
> routers of their own.
>
> A model-based router cannot route *reads and writes* here, because both databases hold the same
> models — there is no model-level fact distinguishing a live character from an archived one. Access
> is therefore explicit via `.using()`. Any router would exist only to control `allow_migrate`.

## The library's own table

Alongside the cloned Evennia schema, the library owns exactly one table of its own — one row per
archived object.

| Column | |
|---|---|
| `archive_id` | Primary key. The stable identifier, immutable, and the join key to everything else |
| `last_archived` | When the copy was last written. Not null — a row exists because something was archived |
| `last_restored` | When it was last restored. Nullable; most objects never are |

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

> **Unknown — needs a spike.** Whether the library appends its own router from `AppConfig.ready()`,
> as `evennia-shards` does for middleware, or whether the consumer declares it. See
> [archive-settings.md](archive-settings.md).

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

> **Working assumption — not locked in.** The identifier is a UUID, minted at object creation. What
> the attribute is called, and whether it is a plain Attribute or an `AttributeProperty`, is open —
> and it is the one decision that cannot be revised after release, because renaming it orphans every
> archived row in every existing install. Worth confirming whether it lands in `db_strvalue`, which
> would make it SQL-indexable and matter for bulk queries.

> **Working assumption — not locked in.** Consumers may register behaviour per typeclass. If so, the
> lookup walks the **MRO** rather than matching the concrete class, so a consumer registering against
> `MyCharacter` still matches `MyEliteCharacter`, and mixin composition behaves as expected.

## Public interface

Two calls. Everything else — when, how often, in response to what — belongs to the consumer.

```python
archive(obj)                                                       # obj must carry the mixin
find(attribute, value)                                             # → [archive_id, ...]
restore(archive_id, location=None, home=None, return_object=True)
delete(archive_id)                                                 # removes the archived copy
```

> **Working assumption — not locked in.** Parameter *names* and the `location` / `home` arguments are
> illustrative. The return behaviour described below is settled.

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
| `db_strvalue` | Plain varchar — directly queryable and indexable. Most Attributes do not set it |
| `db_value` | Pickled. Works, because Django pickles the search term to compare, but Evennia's own docstring calls it *"not a very efficient operation"* and no index helps |

So a consumer whose identifying field lands in `strvalue` gets a lookup that scales; one using a
normal attribute gets a table scan.

Implementation notes for when this is written:

- **Evennia's `get_by_attribute()` cannot be used directly.** It is a *manager* method ending in
  `self.filter(...)`, and `.using("archive")` returns a QuerySet, which carries no manager methods.
  The library re-expresses the same filter with `.using()` itself — a few lines, and it drops a
  dependency on Evennia's manager surface.
- **It is two hops.** Match the archived object on the consumer's attribute, then read `archive_id`
  off that same object. The second hop wants a `values_list` join rather than loading the objects,
  so that searching the archive does not instantiate archived objects on the searching process.

> **Working assumption — not locked in.** The signature, and whether `find()` takes a category or an
> explicit `strvalue`/`value` choice, or infers it.

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

> **Unknown — needs a spike.** The full set of packed markers Evennia uses. `__packed_dbobj__` is
> confirmed; `dbserialize` must be read for the rest. A missed marker is a reference that copies
> verbatim and silently points at the wrong object after restore.

> **Unknown — needs a spike.** Whether the reference survey can run as a repeating check rather than a
> one-off audit. It needs to, because consumers will add attributes the survey has never seen — but
> whether that is a management command, a test helper, or something else is not decided.

## Restore semantics

A restored object is created with the location it is given. If none is given, it is created with
**no location at all**.

`None` is a legal state in Evennia, not an error one: the object exists, it is queryable, it is simply
nowhere. That makes it the honest default, because placement is a game decision the library has no
business making. Falling back to `settings.DEFAULT_HOME` was considered and rejected — it resolves to
Limbo, and Limbo is a game concept.

Nothing is at risk of being lost, because restore hands the caller a handle to the object. A consumer
who already knows where the object belongs passes `location=`, which also avoids firing movement hooks
on an object that did not move.

`db_home` is treated the same way, with its own parameter.

## What the consumer owns

The library ships no scheduler, no hooks and no triggers. All of the following are consumer code:

- **When to archive.** A logout hook, a periodic script, an admin command, a save point — the library
  is indifferent.
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

1. Whether the library appends its own router from `AppConfig.ready()`, or the consumer declares it
   in their settings.
2. Whether attribute lookup behaves identically against a non-default alias. The query shape is
   ordinary Django, but the pickled-value comparison is untested across `.using()`.
3. The full set of Evennia packed markers, from `dbserialize`.
4. How the reference survey runs repeatedly rather than once.

**Decisions:**

1. The identity attribute's name and storage — irreversible after release.
2. How consumers declare dispositions.
3. Shallow versus deep archival.
4. `find()`'s signature — whether it takes an explicit `strvalue`/`value` choice or infers it.

**Not yet examined at all:**

- A read-only peek at an archived object, so a consumer whose lookup field is not unique can choose
  between candidates without restoring all of them.
- Behaviour under a sharded consumer, where the live game spans several databases.
- Whether the archive should hold history or only the latest state of each object.
- A bulk restore call. It would use `bulk_create` and build no instances, so its return type is its
  own question rather than an extension of `return_object`.
