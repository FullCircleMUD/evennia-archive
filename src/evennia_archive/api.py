# SPDX-License-Identifier: BSD-3-Clause
"""The library's public operations.

Four calls:

    archive(obj)              copy an object into the archive
    find(key, value)          archive ids of objects matching an attribute
    restore(archive_id)       rebuild one in the live database
    delete(archive_id)        remove an archived copy

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
from django.db.models import Q
from django.utils import timezone
from evennia.typeclasses.models import Attribute, Tag

from .mixins import ARCHIVE_ID_KEY
from .models import ArchiveRecord

ARCHIVE_ALIAS = "archive"


def _model_named(model_name):
    """The Evennia db model a record's archived_model names.

    Matched on the bare model name, which follows Evennia's own
    convention on Attribute.db_model ("objectdb", "accountdb"). The
    limitation that comes with it: a consumer whose own app defines a
    model of the same name would be ambiguous here.
    """
    from django.apps import apps

    for model in apps.get_models():
        if model.__name__.lower() == model_name:
            return model
    raise LookupError(f"no model named {model_name!r}")

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


def _purge_attributes(db_model, archived_pk):
    """Delete an archived row's attributes and the links to them.

    Attributes are owned outright by the row they hang off, so both the
    links and the Attribute rows go.
    """
    through = db_model.db_attributes.through
    owner, target = _link_fields(through, db_model, Attribute)

    links = through.objects.using(ARCHIVE_ALIAS).filter(**{f"{owner}_id": archived_pk})
    Attribute.objects.using(ARCHIVE_ALIAS).filter(
        pk__in=links.values_list(f"{target}_id", flat=True)
    ).delete()
    links.delete()


def _purge_tag_links(db_model, archived_pk):
    """Detach an archived row from its tags, leaving the tags themselves.

    Tags are shared rows — several objects point at one — so deleting the
    tag because one holder went away would strip it from the others.
    """
    through = db_model.db_tags.through
    owner, _ = _link_fields(through, db_model, Tag)
    through.objects.using(ARCHIVE_ALIAS).filter(
        **{f"{owner}_id": archived_pk}
    ).delete()


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

    _purge_attributes(db_model, archived_pk)

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

    _purge_tag_links(db_model, archived_pk)

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


class NotArchived(LookupError):
    """Raised when nothing in the archive carries the given identity."""


# Where a restored object records a value it could not keep. The game can
# read it whenever it likes — at restore, or the next time the player logs
# in — and offer them a rename. Deleting it is the consumer's business.
RENAMED_FROM_KEY = "archive_renamed_from"

# A restore that cannot find a free value after this many tries is stuck
# rather than unlucky.
_MAX_RENAME_ATTEMPTS = 1000


def _conflicting_field(db_model, values):
    """The first unique column whose value a live row already holds.

    Asked of the model rather than learned by catching IntegrityError and
    reading it: that message format differs between SQLite and Postgres
    and across Django versions, so parsing it to find the column is
    guesswork. The model knows exactly.

    In practice this only ever finds ``username`` on an account. It is the
    single unique field Evennia declares outside primary keys, objects
    have none, and a consumer cannot add more — typeclass state lives in
    Attributes rather than in the schema.
    """
    for field in db_model._meta.fields:
        if not field.unique or field.primary_key:
            continue
        value = values.get(field.attname)
        if value is None:
            continue
        if db_model.objects.filter(**{field.attname: value}).exists():
            return field.attname, value
    return None, None


def _free_the_unique_values(db_model, values):
    """Adjust unique values until none collide, returning what changed.

    A name taken while its owner was away should not stop them getting
    their character back, so the restore proceeds under an adjusted name
    rather than failing. What is actually irreplaceable is the state —
    levels, skills, progression — and none of that is touched by this.

    Returns ``{field: original_value}`` for whatever had to change, empty
    if nothing did.
    """
    renamed = {}
    field, value = _conflicting_field(db_model, values)

    while field is not None:
        original = renamed.setdefault(field, value)
        for attempt in range(1, _MAX_RENAME_ATTEMPTS + 1):
            candidate = f"{original}{attempt}"
            if not db_model.objects.filter(**{field: candidate}).exists():
                values[field] = candidate
                break
        else:
            raise RuntimeError(
                f"no free {field} based on {original!r} after "
                f"{_MAX_RENAME_ATTEMPTS} attempts"
            )
        field, value = _conflicting_field(db_model, values)

    return renamed


def _archived_values(db_model, archived_pk):
    """The archived row's column values, as a plain dict.

    values(), not an instance — see docs/design.md § The archive holds
    rows, not objects.
    """
    values = (
        db_model.objects.using(ARCHIVE_ALIAS).filter(pk=archived_pk).values().first()
    )
    if values is None:
        return None
    values.pop("id", None)
    return values


def _restore_attributes(db_model, archived_pk, live_pk):
    """Recreate the archived attribute set on the restored object."""
    through = db_model.db_attributes.through
    owner, target = _link_fields(through, db_model, Attribute)

    archived_ids = (
        through.objects.using(ARCHIVE_ALIAS)
        .filter(**{f"{owner}_id": archived_pk})
        .values_list(f"{target}_id", flat=True)
    )
    for row in (
        Attribute.objects.using(ARCHIVE_ALIAS).filter(pk__in=list(archived_ids)).values()
    ):
        row.pop("id", None)
        restored = Attribute.objects.create(**row)
        through.objects.create(
            **{f"{owner}_id": live_pk, f"{target}_id": restored.pk}
        )


def _restore_tags(db_model, archived_pk, live_pk):
    """Reattach the archived tags, reusing live tags where they exist."""
    through = db_model.db_tags.through
    owner, target = _link_fields(through, db_model, Tag)

    archived_ids = (
        through.objects.using(ARCHIVE_ALIAS)
        .filter(**{f"{owner}_id": archived_pk})
        .values_list(f"{target}_id", flat=True)
    )
    for row in Tag.objects.using(ARCHIVE_ALIAS).filter(pk__in=list(archived_ids)).values():
        row.pop("id", None)
        data = row.pop("db_data", None)
        tag, _ = Tag.objects.get_or_create(**row, defaults={"db_data": data})
        through.objects.create(**{f"{owner}_id": live_pk, f"{target}_id": tag.pk})


def _live_pk_for(db_model, archive_id):
    """The primary key of a live object already carrying this identity."""
    return (
        db_model.objects.filter(
            db_attributes__db_key=ARCHIVE_ID_KEY,
            db_attributes__db_strvalue=archive_id,
        )
        .values_list("pk", flat=True)
        .first()
    )


def _as_pk(value):
    return getattr(value, "pk", value)


def restore(archive_id, return_object=True):
    """Recreate an archived object in the live database.

    Takes an identifier rather than an object, because the object does
    not exist yet. Returns the restored object, or its primary key when
    ``return_object`` is False.

    **The object comes back stripped of every dbref it once held** —
    location, home, destination, owning account. Those are primary keys
    into a database that has been rebuilt, so they mean nothing now.
    Deciding where a restored object goes and what it reattaches to is
    the consumer's business, not this library's. We store it and hand it
    back; they place it.

    Idempotent: restoring an identity that is already live returns the
    existing object rather than making a second copy.

    **A unique value that is no longer free gets a number appended** —
    in practice always an account whose username was taken while its
    owner was away. `rowan` becomes `rowan1`, then `rowan2`, until one is
    free. The restore proceeds rather than failing, because the name is
    the recoverable part and the state behind it is not.

    The value that could not be kept is recorded on the restored object
    under ``archive_renamed_from``, so the game can offer a rename
    whenever suits — at restore, or the next time they log in.
    """
    archive_id = str(archive_id)
    record = ArchiveRecord.objects.using(ARCHIVE_ALIAS).filter(pk=archive_id).first()
    if record is None:
        raise NotArchived(f"nothing archived under {archive_id!r}")

    db_model = _model_named(record.archived_model)
    values = _archived_values(db_model, record.archived_pk)
    if values is None:
        raise NotArchived(
            f"{archive_id!r} points at {record.archived_model} row "
            f"{record.archived_pk}, which is not in the archive"
        )

    existing_pk = _live_pk_for(db_model, archive_id)
    if existing_pk is not None:
        return _return_as(db_model, existing_pk, return_object)

    renamed = _free_the_unique_values(db_model, values)

    with transaction.atomic():
        # bulk_create for the same reason archive() uses it: a plain
        # create() fires Evennia's creation hooks, which would run the
        # typeclass's setup over the top of the state we are restoring —
        # including minting a fresh archive_id.
        # See docs/design.md § The archive holds rows, not objects.
        live_pk = db_model.objects.bulk_create([db_model(**values)])[0].pk
        _restore_attributes(db_model, record.archived_pk, live_pk)
        _restore_tags(db_model, record.archived_pk, live_pk)
        if renamed:
            note = Attribute.objects.create(
                db_key=RENAMED_FROM_KEY,
                db_value=renamed,
                db_model=record.archived_model,
                db_lock_storage="",
            )
            through = db_model.db_attributes.through
            owner, target = _link_fields(through, db_model, Attribute)
            through.objects.create(
                **{f"{owner}_id": live_pk, f"{target}_id": note.pk}
            )

    ArchiveRecord.objects.using(ARCHIVE_ALIAS).filter(pk=archive_id).update(
        last_restored=timezone.now()
    )
    return _return_as(db_model, live_pk, return_object)


def _return_as(db_model, live_pk, return_object):
    """Hand back the object, or evict it and hand back its key."""
    obj = db_model.objects.get(pk=live_pk)
    if return_object:
        return obj
    obj.flush_from_cache(force=True)
    return live_pk


def _model_names_in_archive(model=None):
    """Which archived models a search should cover."""
    if model is not None:
        return [model if isinstance(model, str) else model.__name__.lower()]
    return list(
        ArchiveRecord.objects.using(ARCHIVE_ALIAS)
        .values_list("archived_model", flat=True)
        .distinct()
    )


def find(key, value, model=None):
    """Archive identifiers of objects whose ``key`` attribute holds ``value``.

    Returns a list, because the library cannot know whether a consumer's
    chosen attribute is unique. Choosing between several matches — or
    knowing there can only be one and taking it — is theirs.

    ``model`` narrows the search to one archived model ("accountdb",
    "objectdb", or the class itself). Left out, every model the archive
    holds is searched.

    **Wrap this in deferToThread.** It is the one call here that can block
    long enough to be felt by every connected player; the reasons are in
    docs/design.md § find() is the expensive call — defer it.

    A pickled comparison is type-sensitive, and matching the stored type
    is the caller's job: ``find("level", 12)`` matches an attribute stored
    as the integer 12, while ``find("level", "12")`` matches nothing, and
    does so silently. The library cannot know what type an attribute holds
    and will not guess, because a wrong guess is a false match rather than
    an error.
    """
    found = []
    for model_name in _model_names_in_archive(model):
        db_model = _model_named(model_name)

        # One filter() call, not two chained. Conditions on a
        # multi-valued relation only apply to the *same* related row when
        # they share a filter() — split them and an object matches if any
        # attribute has the key and any other has the value.
        #
        # Matching either column removes the need for a storage-mode
        # flag: strattr sets db_strvalue and leaves db_value null, and a
        # normal attribute is the reverse.
        matches = (
            db_model.objects.using(ARCHIVE_ALIAS)
            .filter(
                Q(db_attributes__db_key=key)
                & (
                    Q(db_attributes__db_strvalue=value)
                    | Q(db_attributes__db_value=value)
                )
            )
            .values_list("pk", flat=True)
        )

        # The archived row does not need reading for its identity —
        # ArchiveRecord already maps model and key to it.
        found.extend(
            ArchiveRecord.objects.using(ARCHIVE_ALIAS)
            .filter(archived_model=model_name, archived_pk__in=list(matches))
            .values_list("archive_id", flat=True)
        )

    return [str(archive_id) for archive_id in found]


def delete(archive_id):
    """Remove an archived copy and the record pointing at it.

    Returns True if something was deleted, False if the identity was not
    in the archive.

    A hard delete, not a flag. A soft-deleted row would make correctness
    depend on every read remembering a filter, and forgetting it once
    resurrects objects a player chose to destroy.

    Idempotent, and deliberately quiet about an identity it does not
    hold: the natural caller is a consumer's own delete hook, which fires
    for objects that were never archived in the first place.

    Note that not calling this is a choice with a consequence — archived
    copies outlive the objects they came from and will be restored. See
    docs/design.md § Deletion is offered, not mandated.
    """
    archive_id = str(archive_id)

    with transaction.atomic(using=ARCHIVE_ALIAS):
        record = (
            ArchiveRecord.objects.using(ARCHIVE_ALIAS).filter(pk=archive_id).first()
        )
        if record is None:
            return False

        db_model = _model_named(record.archived_model)
        _purge_attributes(db_model, record.archived_pk)
        _purge_tag_links(db_model, record.archived_pk)
        db_model.objects.using(ARCHIVE_ALIAS).filter(pk=record.archived_pk).delete()

        # The bookkeeping row goes with the copy. Leaving it behind would
        # be a soft delete through the back door, with a last_archived
        # pointing at nothing.
        ArchiveRecord.objects.using(ARCHIVE_ALIAS).filter(pk=archive_id).delete()
        return True
