# SPDX-License-Identifier: BSD-3-Clause
"""evennia-archive: keep your players when you rebuild your Evennia world.

A second Evennia database on the same schema, migrated alongside the game and
never run as a game, holding accounts and characters. Rebuild the world from
source and the players survive it.

``__all__`` below is the public surface. A consumer makes five calls, composes
one of three mixins onto its own typeclasses, and catches two exception types —
everything else is internal whatever its import path happens to allow.

Five calls::

    archive(obj)                       copy an object into the archive
    find_by_attribute(key, value)      archive ids of objects matching an attribute
    find_by_column(model, col, value)  archive ids of rows matching a column
    restore(archive_id)                rebuild one in the live database
    delete(archive_id)                 remove an archived copy

Objects are archivable when their typeclass carries `ArchivableObjectMixin`,
`ArchivableCharacterMixin` or `ArchivableAccountMixin`. Each mints the identity
that matches a live row to its archived copy. Nothing else is archivable, and
nothing is archived until you ask. Everything a consumer reaches through an
instance — ``archive_id``, ``archive_now()``, ``at_archive_init()`` — arrives
with the mixin and needs no export of its own.

**The surface is kept as small as it can be.** A name is published because a
consumer cannot do its job without it, not because publishing it seems
harmless. A need that appears later is added then, with a reason.

Installation — the app, a second database alias and a router — is in
docs/installing.md.
"""

__version__ = "0.1.0"

#: Where each published name is resolved from. The values are module paths
#: rather than objects: see ``__getattr__``.
_SOURCES = {
    "ArchivableAccountMixin": "evennia_archive.mixins",
    "ArchivableCharacterMixin": "evennia_archive.mixins",
    "ArchivableObjectMixin": "evennia_archive.mixins",
    # Published for the same reason the calls are: `archive()` tells callers to
    # catch NotArchivable at their call site, and `restore()` raises NotArchived
    # — neither is catchable by name without an import, and reaching into `api`
    # for one is what a public surface exists to stop.
    "NotArchivable": "evennia_archive.api",
    "NotArchived": "evennia_archive.api",
    "archive": "evennia_archive.api",
    "delete": "evennia_archive.api",
    "find_by_attribute": "evennia_archive.api",
    "find_by_column": "evennia_archive.api",
    "restore": "evennia_archive.api",
}

__all__ = sorted(_SOURCES)


def __getattr__(name):
    """Resolve the public surface on first use, not at import.

    A plain ``from .api import archive`` at module scope would run while Django
    is still building its app registry — this package is in ``INSTALLED_APPS``
    and ``api`` reaches ``models.py``. That raises ``AppRegistryNotReady`` and
    the server does not start.

    Deferring costs one lookup the first time a consumer touches a name and
    nothing afterwards, since Python caches the result on the module.

    **Refusing everything not published is load-bearing, and not only for
    typos.** Without it, an ordinary ``from evennia_archive import api`` would
    re-enter this function looking for ``api`` and import the same module
    again — infinite recursion rather than a missing attribute. Rejecting every
    name we do not publish is what lets Python's own submodule import handle
    the rest.
    """
    source = _SOURCES.get(name)
    if source is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    return getattr(import_module(source), name)
