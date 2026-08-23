# SPDX-License-Identifier: BSD-3-Clause
"""The library's public operations.

Four calls, of which one is implemented so far:

    archive(obj)   copy an object into the archive

Everything about *when* to call them belongs to the consumer. The library
ships no scheduler, no hooks and no triggers.

READ BEFORE CHANGING ANYTHING HERE:
docs/design.md § The archive holds rows, not objects.

Two rules govern every operation in this module — never instantiate a row
fetched from the archive, and never write through Evennia's save path.
Both look like fussiness and neither is; breaking the first writes to the
live game database while appearing to maintain the archive. The reasoning
lives in the design doc rather than here so there is one copy of it.
"""

from django.db import transaction
from django.utils import timezone
from evennia.typeclasses.models import Attribute, Tag

from .models import ArchiveRecord

ARCHIVE_ALIAS = "archive"

# Foreign keys into the live database. Dropped rather than copied — a
# primary key means nothing across two databases. Rebuilding the
# relationships they describe is the reference-translation work, which
# needs a disposition table that does not exist yet.
# See docs/design.md § Reference translation.
_DROPPED_REFERENCES = {"db_location", "db_home", "db_destination", "db_account"}


class NotArchivable(ValueError):
    """Raised when an object has no archive identity.

    Almost always means the typeclass is missing ``ArchivableMixin``, or
    the object predates it being added. Minting one here would be worse
    than refusing: an object may already be archived under an identity it
    has since lost, and a fresh one would silently create a second copy
    with no link between them.
    """


def _identity_of(obj):
    archive_id = getattr(obj, "archive_id", None)
    if not archive_id:
        raise NotArchivable(
            f"{obj!r} has no archive_id. Add ArchivableMixin to its typeclass, "
            "and call at_archive_init() on objects created before you did."
        )
    return archive_id


def _copyable_fields(obj, db_model):
    """The object's own column values, minus its key and its references."""
    values = {}
    for field in db_model._meta.concrete_fields:
        if field.primary_key or field.name in _DROPPED_REFERENCES:
            continue
        values[field.attname] = getattr(obj, field.attname)
    return values


def _link_fields(through, owner_model, target_model):
    """Names of the two foreign keys on an m2m through table."""
    owner = target = None
    for field in through._meta.fields:
        if not field.is_relation:
            continue
        if field.related_model is owner_model:
            owner = field.name
        elif field.related_model is target_model:
            target = field.name
    return owner, target


def _replace_attributes(source, db_model, archived_pk):
    """Mirror the source's attribute set onto the archived row.

    Deleted and rebuilt rather than diffed. An object that had twenty
    attributes and now has eighteen needs two *gone*, so a diff has to
    handle removal anyway — and at one object per call the write volume
    is nothing.

    Goes at the through table directly rather than the archived row's own
    m2m manager: reaching that manager would mean instantiating the row.
    See docs/design.md § The archive holds rows, not objects.
    """
    through = db_model.db_attributes.through
    owner, target = _link_fields(through, db_model, Attribute)

    links = through.objects.using(ARCHIVE_ALIAS).filter(**{f"{owner}_id": archived_pk})
    Attribute.objects.using(ARCHIVE_ALIAS).filter(
        pk__in=links.values_list(f"{target}_id", flat=True)
    ).delete()
    links.delete()

    for attr in source.db_attributes.all():
        copied = Attribute.objects.using(ARCHIVE_ALIAS).create(
            db_key=attr.db_key,
            db_category=attr.db_category,
            db_value=attr.db_value,
            db_strvalue=attr.db_strvalue,
            db_lock_storage=attr.db_lock_storage,
            db_model=attr.db_model,
            db_attrtype=attr.db_attrtype,
        )
        through.objects.using(ARCHIVE_ALIAS).create(
            **{f"{owner}_id": archived_pk, f"{target}_id": copied.pk}
        )


def _replace_tags(source, db_model, archived_pk):
    """Mirror the source's tag set onto the archived row.

    Tags differ from attributes: they are shared rows, so several objects
    point at one Tag. The archive therefore reuses a matching tag where
    one exists rather than accumulating duplicates. Only the links are
    rebuilt; the tags themselves stay.
    """
    through = db_model.db_tags.through
    owner, target = _link_fields(through, db_model, Tag)

    through.objects.using(ARCHIVE_ALIAS).filter(
        **{f"{owner}_id": archived_pk}
    ).delete()

    for tag in source.db_tags.all():
        archived_tag, _ = Tag.objects.using(ARCHIVE_ALIAS).get_or_create(
            db_key=tag.db_key,
            db_category=tag.db_category,
            db_tagtype=tag.db_tagtype,
            db_model=tag.db_model,
            defaults={"db_data": tag.db_data},
        )
        through.objects.using(ARCHIVE_ALIAS).create(
            **{f"{owner}_id": archived_pk, f"{target}_id": archived_tag.pk}
        )


def archive(obj):
    """Copy ``obj`` into the archive, inserting or updating as needed.

    The object must carry ``ArchivableMixin``. Returns the ArchiveRecord.

    Everything written lands in one database, so a single transaction
    covers both the copy and the record pointing at it — the pointer and
    its target cannot end up disagreeing.

    See docs/design.md § The archive holds rows, not objects before
    changing how anything here reads or writes.
    """
    archive_id = _identity_of(obj)
    db_model = obj.__dbclass__
    model_name = db_model.__name__.lower()

    with transaction.atomic(using=ARCHIVE_ALIAS):
        record = (
            ArchiveRecord.objects.using(ARCHIVE_ALIAS)
            .filter(pk=archive_id)
            .first()
        )

        # values_list, never .get(). The idmapper would hand back a live
        # object with the same primary key.
        # See docs/design.md § The archive holds rows, not objects.
        archived_pk = None
        if record:
            archived_pk = (
                db_model.objects.using(ARCHIVE_ALIAS)
                .filter(pk=record.archived_pk)
                .values_list("pk", flat=True)
                .first()
            )
            # A record pointing at a row that is gone falls through to
            # the insert branch rather than crashing. Self-healing costs
            # nothing and turns a hard failure into a rewrite.

        values = _copyable_fields(obj, db_model)
        if archived_pk is None:
            # bulk_create, not create — it bypasses save(), and Evennia's
            # creation hooks hang off save().
            # See docs/design.md § The archive holds rows, not objects.
            archived_pk = db_model.objects.using(ARCHIVE_ALIAS).bulk_create(
                [db_model(**values)]
            )[0].pk
        else:
            db_model.objects.using(ARCHIVE_ALIAS).filter(pk=archived_pk).update(
                **values
            )

        _replace_attributes(obj, db_model, archived_pk)
        _replace_tags(obj, db_model, archived_pk)

        record, _ = ArchiveRecord.objects.using(ARCHIVE_ALIAS).update_or_create(
            archive_id=archive_id,
            defaults={
                "archived_model": model_name,
                "archived_pk": archived_pk,
                "last_archived": timezone.now(),
            },
        )
        return record
