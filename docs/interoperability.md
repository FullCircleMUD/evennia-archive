# Interoperability

This library against every sibling library in `libraries/`.

**No library code exists yet**, so the sections below state what the agreed design implies rather than
what the code does. Each is provisional until there is an implementation to check, and clearances
should be re-confirmed at that point rather than inherited.

What this library will do that can constrain a sibling: read `ObjectDB` and `AccountDB` rows together
with their attributes and tags, write to a **second database alias**, and dispatch its copy job **off
the reactor thread** so it never blocks play. Any library that scopes ORM access, owns thread-local
state, or routes queries by alias is therefore in scope.

## evennia-archive

This library.

## evennia-mob-spawner

**No coupling anticipated.** Neither library imports the other. Mobs are not archived — they are
spawned from rule sets and an instance is ephemeral, so nothing this library stores refers to one.

`[TBD — confirm once the disposition table exists: whether any character-held reference can point at a
spawned mob (a pet, a charmed follower, a quest target). If one can, its disposition belongs in that
table and this section needs revisiting.]`

## evennia-shards

**Optional integration anticipated**, and the sibling with real constraints.

Two of shards' documented constraints look directly applicable:

- **Off-thread ORM work loses the shard context.** The copy job is agreed to run through
  `deferToThread`, which is exactly the case shards documents — queries go unscoped and inserts land
  with `shard_id=NULL` unless the callable is wrapped at the dispatch site.
- **The router runs unscoped**, so where an archive job is allowed to run is a placement question, not
  just a scheduling one.

Both constraints are shards', since they follow from its data model. They are documented in
[its `interoperability.md`](../../evennia-shards/docs/interoperability.md) and
[`tenancy.md`](../../evennia-shards/docs/tenancy.md), and are not restated here.

Note that detecting a sharded deployment is not the same as a successful import — see shards'
guidance on `get_role()`.

`[TBD — needs discussion: what a sharded consumer's archive should contain. One archive per shard, or
one archive across all shards, is an open question with consequences for both libraries.]`

## evennia-targeting

**No coupling.** Neither library imports the other. Targeting wraps `caller.search()` to filter
candidate lists already in hand; it issues no query this library would see and creates nothing this
library would archive. This library performs no searching — it reads rows by identifier.

## evennia-world-builder

**Optional integration, by capability rather than import.**

World-builder is the anticipated source of stable room identifiers, which are what would let a
restored character return to the exact room rather than to their home room. But this library must
never detect world-builder. It asks a room whether it exposes a stable identifier and degrades to the
home room if not — so hand-rolled room keys satisfy the same contract, and a consumer without
world-builder loses nothing but the enhancement.

This is principle 4 in [CLAUDE.md](../CLAUDE.md): test the object, never the library.

`[TBD — needs discussion: the shape of that capability contract, and whether the location identifier
is resolved at backup time or mirrored onto the character on movement.]`

## evennia-yaml-reader

**No coupling.** Neither library imports the other. yaml-reader depends only on `pyyaml`, has no
Evennia dependency and touches no database, so nothing it does is visible to this library and nothing
this library does is visible to it.
