# Interoperability

This library against every sibling library in `libraries/`.

What this library does that can constrain a sibling: it reads `ObjectDB` and `AccountDB` rows together
with their attributes and tags, writes to a **second database alias** holding a clone of Evennia's
schema, and refuses to start when that alias resolves to the game's database. Any library that scopes
ORM access, routes queries by alias, or resolves database connections is therefore in scope.

Its calls are plain synchronous functions — no Twisted, no reactor assumed. Dispatching off the
reactor is the consumer's decision, so a sibling that owns thread-local state is constrained by where
a consumer chooses to call from rather than by anything this library does.

## evennia-ai-memory

**No coupling.** Neither imports the other. ai-memory owns uniquely-named tables on its own alias, so
the two aliases never contend; nothing it stores refers to an archived row, and nothing archived
refers to a memory.

The pair does share one consideration: both own an alias, so a consumer running both declares two
routers. Each answers only for its own app label, so neither captures the other's models.

## evennia-archive

This library.

## evennia-calendar

**No coupling.** Neither imports the other. Game time is derived from Evennia's own clock and the
calendar owns no tables; nothing archived refers to it, and a restore changes nothing it reads.

## evennia-database-cascade

**Hard dependency.** This library declares its alias in `db_spec.py` and the cascade derives the
`DATABASES` entry, the router and the migration list from it — the library ships no router and no
resolution code of its own. This is the cascade's first real consumer.

**The constraint runs from here to there: this library cannot use the shared rung.** The archive is a
clone of Evennia's schema, so pointing its alias at the game's database does not give it a second set
of tables — it hands it Evennia's. The spec expresses both halves:
`allow_sharing_common_db=False` (the shared `DATABASE_URL` rung is refused) and
`allow_foreign_tables_in_own_db=True` (Evennia's tables are exactly what belongs in the archive's
database).

`check_settings()` still asserts the constraint from this side — it refuses to start when the
archive and the game resolve to the same database. The two checks are deliberate belt and braces: a
hand-written `DATABASES` entry bypasses the cascade, and an operator pointing `DATABASE_URL_ARCHIVE`
at the game's own database passes the cascade's rules and is caught only here.

## evennia-equipment

**No coupling.** Neither imports the other. Equipment state lives in Attributes on the objects it
describes, so it travels with an archived character as opaque bytes and needs nothing from this
library to survive a restore.

## evennia-llm-service

**No coupling.** Neither imports the other. llm-service holds no game state and owns no tables this
library would copy.

## evennia-logging-extension

**Hard dependency.** `log.py` binds `archive_log` through its `make_logger`, and every line this
library emits is delivered by the extension — including lines from the pre-reactor window, where
Evennia's own `log_file` would lose them.

## evennia-message-bus

**No coupling.** Neither imports the other. The bus owns uniquely-named tables on its own alias; an
archive or a restore emits no message, and the bus carries nothing that refers to an archived row.

## evennia-mob-spawner

**No coupling.** Neither imports the other. Mobs are spawned from rule sets and an instance is
ephemeral, so nothing this library stores refers to one.

`[TBD — confirm once the disposition table exists: whether any character-held reference can point at a
spawned mob (a pet, a charmed follower, a quest target). If one can, its disposition belongs in that
table and this section needs revisiting.]`

## evennia-portal-multiplex

**No coupling.** Neither imports the other. Multiplex works at the Portal and Server transport layer
and touches no game database.

## evennia-scaling

**No coupling.** Neither imports the other. Scaling places accounts across instances, and a restored
account carries no instance affinity — `restore()` returns it stripped of every dbref, so placement is
the consumer's to reapply exactly as it is for a new account.

## evennia-shards

**Optional integration anticipated**, and the sibling with real constraints.

Two of shards' documented constraints apply wherever a consumer defers this library's calls off the
reactor:

- **Off-thread ORM work loses the shard context.** Queries go unscoped and inserts land with
  `shard_id=NULL` unless the callable is wrapped at the dispatch site. This library ships no
  dispatcher, so the wrapping is the consumer's — but the calls documented as needing `deferToThread`
  are exactly the ones this bites.
- **The router runs unscoped**, so where an archive job is allowed to run is a placement question,
  not just a scheduling one.

Both constraints are shards', since they follow from its data model. They are documented in
[its `interoperability.md`](../../evennia-shards/docs/interoperability.md) and
[`tenancy.md`](../../evennia-shards/docs/tenancy.md), and are not restated here.

Detecting a sharded deployment is not the same as a successful import — see shards' guidance on
`get_role()`.

`[TBD — needs discussion: what a sharded consumer's archive should contain. One archive per shard, or
one archive across all shards, is an open question with consequences for both libraries.]`

## evennia-survival

**No coupling.** Neither imports the other. Hunger and thirst are Attributes on the character, so they
travel with an archived copy as bytes and come back with it.

## evennia-targeting

**No coupling.** Neither imports the other. Targeting filters candidate lists already in hand; it
issues no query this library would see and creates nothing this library would archive. This library
performs no searching — it reads rows by identifier.

## evennia-world-builder

**Optional integration, by capability rather than import.**

World-builder is the anticipated source of stable room identifiers, which are what would let a
restored character return to the exact room rather than to their home room. This library must never
detect world-builder: it asks a room whether it exposes a stable identifier and degrades to the home
room if not, so hand-rolled room keys satisfy the same contract and a consumer without world-builder
loses nothing but the enhancement.

This is principle 4 in [CLAUDE.md](../CLAUDE.md): test the object, never the library.

`[TBD — needs discussion: the shape of that capability contract, and whether the location identifier
is resolved at backup time or mirrored onto the character on movement.]`

## evennia-yaml-reader

**No coupling.** Neither imports the other. yaml-reader depends only on `pyyaml`, has no Evennia
dependency and touches no database, so nothing it does is visible to this library and nothing this
library does is visible to it.

## fcm-telemetry-spawn

**No coupling.** Neither imports the other. Telemetry aggregates economic activity and stores no
per-object state this library would copy.

## fcm-xrpl

**No coupling**, and the pairing worth thinking about hardest.

xrpl owns the mirror of on-chain ownership on its own alias, and that alias survives a world rebuild
for the same reason this one does. A character restored here comes back with its wallet address in an
Attribute, so the link to its holdings survives without either library knowing about the other.

**Neither library reconciles the other.** A restored character whose mirror rows were themselves lost
is a recovery problem the consumer owns, and FCM documents it in `account-recovery.md` rather than
either library assuming it.
