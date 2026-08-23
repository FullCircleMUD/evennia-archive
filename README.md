# evennia-archive

Archives Evennia accounts and characters into a separate database so you can rebuild your world
without losing your players.

## Status

**Working, but not published and not yet used by a real game.** Objects and accounts can be archived
and restored, with identity surviving and dbrefs deliberately not. `find()` and `delete()` are not
written yet, and nothing has been tested on PostgreSQL. See [docs/progress.md](docs/progress.md) for
what exists and what proves it.

## The problem it solves

Evennia identity is the `ObjectDB` primary key. Rebuild your world and those keys are re-issued from
scratch, so a character's rows cannot simply be restored — the references inside them point at objects
that no longer exist, including references buried inside pickled attribute values.

That makes "rebuild the world from source" and "keep the players" hard to have at the same time.

## The approach

A second Evennia database, on the same schema, migrated alongside the game and **never run as a game**
— no rooms, no mobs, nothing instantiated. Account and character rows are copied into it as they
change. Because both ends share a schema, attribute values move as opaque bytes and are never parsed.

What replaces that work is reference translation: every stored reference is resolved to a stable
identifier on the way in, and back to whatever primary key the object receives on the way out.

## Is this for you?

Probably, if you rebuild your world from source — YAML, batch scripts, a world builder — and want
player characters to survive the rebuild.

Probably not, if you are looking for database backups. This is not `pg_dump`; it holds a live,
queryable record of accounts and characters, and it is not a substitute for backing up your database.

## Install

Not published to PyPI yet.

Editable install for development against a checkout:

```
git clone https://github.com/FullCircleMUD/evennia-archive.git
cd evennia-archive
python -m venv venv
# Activate the venv (platform-specific)
pip install evennia
pip install -e .
python runtests.py
```

Installing the package is not enough on its own — a consumer declares the app, a second database
alias and a router in their own settings. **See [docs/archive-settings.md](docs/archive-settings.md)
for what to add**, including the one entry that will silently break a game that already has database
routers.

## Learn more

- [docs/INDEX.md](docs/INDEX.md) — the design wiki
- [docs/design.md](docs/design.md) — the whole design, with a box above everything not yet settled
- [docs/archive-settings.md](docs/archive-settings.md) — what a consumer declares in their settings
- [docs/interoperability.md](docs/interoperability.md) — this library against its siblings
- [CLAUDE.md](CLAUDE.md) — context for LLM agents working in this repo

## Licence

BSD 3-Clause. See [LICENSE](LICENSE).
