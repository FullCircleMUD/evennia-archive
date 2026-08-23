# SPDX-License-Identifier: BSD-3-Clause
"""The library's own bookkeeping table.

One row per archived object, updated in place — a state table, not a log.
A hundred archives of the same object produce one row with a moving
timestamp, so the table is bounded by object count rather than event
count and needs no retention policy.

It lives in the archive database, not the live one. Anywhere else and it
is destroyed by the very rebuild it exists to help recover from.
"""

from django.db import models


class ArchiveRecord(models.Model):
    """When each archived object was last written and last restored."""

    archive_id = models.UUIDField(
        primary_key=True,
        help_text="Stable identifier of the archived object. Immutable.",
    )

    # Where the archived copy actually sits. Primary keys in the live
    # database are worthless across a rebuild — that is the problem this
    # library exists to solve — but the archive is never torn down, so a
    # primary key *inside it* is stable by construction. Recording it
    # turns "I have an archive_id, find the row" into a direct hit
    # instead of an attribute lookup.
    #
    # The model is needed alongside it because the archive holds several
    # tables; a bare primary key is ambiguous between them. Naming follows
    # Evennia's own convention on Attribute.db_model — "objectdb",
    # "accountdb".
    archived_model = models.CharField(
        max_length=32,
        help_text='Which archive table the copy is in, e.g. "objectdb".',
    )
    archived_pk = models.IntegerField(
        help_text="Primary key of the copy within the archive database.",
    )

    # There is nowhere else to put this. ObjectDB carries only
    # db_date_created, which copies to the archive verbatim and records
    # when the object was *made*, not when it was archived; and attribute
    # values are pickled, so storing it there makes it unqueryable in SQL.
    # Without this column, "which objects have a stale or missing
    # archive?" cannot be written.
    last_archived = models.DateTimeField(
        help_text="When the archived copy was last written.",
    )

    # No query needs this today. It is here because it records an event,
    # and events that go unrecorded are unrecoverable — add the column
    # later and every restore before then is simply unknown.
    last_restored = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the object was last restored. Null if never.",
    )

    class Meta:
        verbose_name = "archive record"
        verbose_name_plural = "archive records"
        indexes = [
            # The recovery sweep asks for records older than a cutoff.
            models.Index(fields=["last_archived"], name="archive_last_archived_idx"),
        ]

    def __str__(self):
        return f"{self.archive_id} (archived {self.last_archived})"
