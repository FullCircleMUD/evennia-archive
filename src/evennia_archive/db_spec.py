# SPDX-License-Identifier: BSD-3-Clause
"""The database alias this library declares to evennia-database-cascade.

The cascade derives the ``DATABASES`` entry, the router and the migration
list from this declaration — the library ships no router and no resolution
code of its own.

Both ``allow_`` flags are the schema-clone constraint: the archive holds
Evennia's own table names, so the game's database would hand the alias the
live tables rather than a second set (no sharing the common URL), and
Evennia's tables are exactly what belongs in the archive's database
(foreign tables welcome in it).

On the consumer's settings path, so it imports nothing from Django.
"""

from evennia_database_cascade import AliasSpec

from .config import ARCHIVE_ALIAS

SPEC = AliasSpec(
    app_labels="evennia_archive",
    alias=ARCHIVE_ALIAS,
    allow_sharing_common_db=False,
    allow_foreign_tables_in_own_db=True,
)
