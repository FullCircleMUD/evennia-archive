# SPDX-License-Identifier: BSD-3-Clause
"""
ArchivableMixin — marks a typeclass as archivable and gives it a stable identity.

Mix into any typeclass whose objects you want archived — characters,
accounts, ships, guild halls. The library archives nothing that does not
carry this mixin, so adding it is the consumer's declaration of intent
rather than something the library infers.

What it provides is one thing: an ``archive_id``, minted once when the
object is created and never changed afterwards. Primary keys are
meaningless across two databases, so this is what lets a row in the live
game and a row in the archive be known to be the same object.

Rules:
    archive_id is minted at creation   → never at archive time
    archive_id is immutable            → reminting would orphan the archive
    archive_id is a canonical string   → lowercase, hyphenated, from uuid4()
    stored unpickled (strattr)         → so lookups are plain string equality

Usage — the mixin hooks creation itself, so most consumers add it and
stop:

    class Character(ArchivableMixin, DefaultCharacter):
        pass

If your typeclass already overrides the creation hook, call the init
directly instead:

    def at_object_creation(self):
        super().at_object_creation()
        self.at_archive_init()
"""

import uuid

# The Attribute key. Changing this orphans every archived row in every
# existing install — it is the one name in this library that cannot be
# revised after release.
ARCHIVE_ID_KEY = "archive_id"


class ArchivableBaseMixin:
    """The archive identity, and nothing else.

    Owns ``archive_id`` and ``at_archive_init()``. Its creation hooks exist
    only to refuse: a typeclass carrying this directly has no working hook,
    so it would exist without an identity and only reveal that at the first
    archive. Refusing at creation puts the failure where the mistake is.

    A true abstract base is not available. Evennia's typeclasses carry the
    ``TypeclassBase`` metaclass, and adding ``ABCMeta`` to that raises a
    metaclass conflict at class definition; ``@abstractmethod`` without
    ``ABCMeta`` enforces nothing.

    **A subclass implementing a creation hook must skip this class when it
    calls up.** The mixin chain precedes Evennia's own class in the MRO, so
    plain ``super()`` from a child lands on the refusal below rather than on
    Evennia's hook. Use ``super(ArchivableBaseMixin, self)``, which resumes
    after this class. `ArchivableMixin` below is the worked example.
    """

    @property
    def archive_id(self):
        """This object's archive identity, or None if never minted.

        Returns a string rather than a ``uuid.UUID``. It is stored as a
        string so that lookups are plain equality rather than a pickled
        comparison, and returning it unchanged keeps the value the caller
        sees identical to the value in the database.
        """
        return self.attributes.get(ARCHIVE_ID_KEY, strattr=True)

    def at_archive_init(self):
        """Mint an archive_id if this object has none. Safe to call repeatedly.

        Returns the identifier, whether newly minted or already present.

        Never overwrites. An object that already has an identity keeps it
        — reminting would strand whatever is already archived under the
        old value, with nothing to link the two.
        """
        existing = self.archive_id
        if existing:
            return existing

        # str() of a uuid4 is already canonical: lowercase and hyphenated.
        # Minting through this one path is what guarantees every stored
        # value has the same form, which matters because string equality
        # is case-sensitive where uuid.UUID comparison is not.
        minted = str(uuid.uuid4())
        self.attributes.add(ARCHIVE_ID_KEY, minted, strattr=True)
        return minted

    def at_object_creation(self):
        # Characters reach this too — a Character is an Object, and mints
        # its identity through the same hook.
        raise NotImplementedError(
            f"{type(self).__name__} carries ArchivableBaseMixin directly, "
            "which mints no identity. Use ArchivableObjectMixin, or "
            "ArchivableCharacterMixin for a character."
        )

    def at_account_creation(self):
        raise NotImplementedError(
            f"{type(self).__name__} carries ArchivableBaseMixin directly, "
            "which mints no identity. Use ArchivableAccountMixin."
        )


class ArchivableMixin(ArchivableBaseMixin):
    """Gives a typeclass a stable identity for archiving."""

    # Evennia calls exactly one of these, depending on what the mixin was
    # mixed into. The other is simply never invoked.
    #
    # super(ArchivableBaseMixin, self) rather than super(): the base sits
    # between this class and Evennia's in the MRO, and its hook refuses.

    def at_object_creation(self):
        super(ArchivableBaseMixin, self).at_object_creation()
        self.at_archive_init()

    def at_account_creation(self):
        super(ArchivableBaseMixin, self).at_account_creation()
        self.at_archive_init()
