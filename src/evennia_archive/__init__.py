# SPDX-License-Identifier: BSD-3-Clause
"""evennia-archive: keep your players when you rebuild your Evennia world.

A second Evennia database on the same schema, migrated alongside the game and
never run as a game, holding accounts and characters. Rebuild the world from
source and the players survive it.

Four calls, in ``evennia_archive.api``::

    archive(obj)              copy an object into the archive
    find(key, value)          archive ids of objects matching an attribute
    restore(archive_id)       rebuild one in the live database
    delete(archive_id)        remove an archived copy

Objects are archivable when their typeclass carries one of the mixins in
``evennia_archive.mixins`` — `ArchivableObjectMixin`,
`ArchivableCharacterMixin` or `ArchivableAccountMixin`. Each mints the
identity that matches a live row to its archived copy. Nothing else is
archivable, and nothing is archived until you ask.

Installation — the app, a second database alias and a router — is in
docs/archive-settings.md.
"""

__version__ = "0.1.0"
