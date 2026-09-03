# SPDX-License-Identifier: BSD-3-Clause
"""
The mixins that mark a typeclass as archivable and give it a stable identity.

Mix one into any typeclass whose objects you want archived — characters,
accounts, ships, guild halls. The library archives nothing that does not
carry one, so adding it is the consumer's declaration of intent rather than
something the library infers.

What they provide is one thing: an ``archive_id``, minted once when the
object is created and never changed afterwards. Primary keys are
meaningless across two databases, so this is what lets a row in the live
game and a row in the archive be known to be the same object.

Pick the one that matches what you are archiving. They differ in which
creation hook Evennia calls, and in what each kind needs beyond identity:

    ArchivableObjectMixin      anything descending from ObjectDB
    ArchivableCharacterMixin   player characters — the ones an account owns
    ArchivableAccountMixin     accounts
    ArchivableBaseMixin        the identity itself — not for direct use

Rules:
    archive_id is minted at creation   → never at archive time
    archive_id is immutable            → reminting would orphan the archive
    archive_id is a canonical string   → lowercase, hyphenated, from uuid4()
    stored unpickled (strattr)         → so lookups are plain string equality

Usage — the mixin hooks creation itself, so most consumers add it and
stop:

    class Character(ArchivableObjectMixin, DefaultCharacter):
        pass

If your typeclass already overrides the creation hook, call `super()` as
usual and the identity is still minted:

    def at_object_creation(self):
        super().at_object_creation()
        ...your own setup...
"""

import uuid

# The Attribute key. Changing this orphans every archived row in every
# existing install — it is the one name in this library that cannot be
# revised after release.
ARCHIVE_ID_KEY = "archive_id"

# The Attribute key naming the account a character belongs to, holding that
# account's archive_id. The archive drops db_account on the way in — it is a
# primary key, and those mean nothing in the other database — so this is the
# only link from a character back to its owner that survives a restore.
OWNER_ACCOUNT_KEY = "owner_account_archive_id"


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
    after this class. `ArchivableObjectMixin` below is the worked example.
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

    def at_post_create_character(self, character, **kwargs):
        # Only ArchivableAccountMixin implements this, so an account
        # reaching the base's version was declared with the wrong mixin —
        # and its characters would go unstamped and unlocked.
        raise NotImplementedError(
            f"{type(self).__name__} creates characters but does not carry "
            "ArchivableAccountMixin, so nothing stamps them with an owner "
            "or writes them a lock that survives a restore."
        )


class ArchivableObjectMixin(ArchivableBaseMixin):
    """Identity for a typeclass descending from ``ObjectDB``.

    Also the parent of `ArchivableCharacterMixin`: a Character is an Object
    and mints through this same hook, so the character mixin adds what
    characters need rather than repeating this.

    A consumer overriding ``at_object_creation`` calls plain ``super()`` as
    usual — that lands here, not on the base. The grandparent form below is
    the library's own business.
    """

    def at_object_creation(self):
        # super(ArchivableBaseMixin, self), not super(): the base sits
        # between this class and Evennia's in the MRO, and its hook refuses.
        # Resuming after the base skips exactly that one class, so a
        # consumer's own mixin further down still gets called.
        super(ArchivableBaseMixin, self).at_object_creation()
        self.at_archive_init()


class ArchivableCharacterMixin(ArchivableObjectMixin):
    """Identity for a player character — one an account creates and owns.

    A Character is an Object, so minting comes from `ArchivableObjectMixin`
    unchanged. Declaring this instead is what tells the library the object
    belongs to an account, which is what `ArchivableAccountMixin` acts on
    when the account creates it.

    **Not for NPCs or mobs.** Most games type those as Character subclasses
    to inherit combat and movement, and they have no owner to stamp. A game
    that wants an NPC's state archived declares `ArchivableObjectMixin` on
    it: same identity, same round trip, no ownership.

    Nothing stops one being *created* without an owner — at creation there
    is no account reference to test against, so the check has to happen
    later. `archive()` is where it does: an object wearing this mixin with
    no owner was not created by an account, and is refused.
    """

    @property
    def owner_account_archive_id(self):
        """The `archive_id` of the account this character belongs to.

        ``None`` when nothing has stamped it. Reading is not archiving —
        a bare read is never an error, because the same absence is correct
        on an NPC and wrong on a player character, and only `archive()` has
        cause to care about the difference.
        """
        return self.attributes.get(OWNER_ACCOUNT_KEY, strattr=True)


class ArchivableAccountMixin(ArchivableBaseMixin):
    """Identity for an account.

    Accounts mint through ``at_account_creation`` rather than the object
    hook, which is the whole reason the kinds are separate classes.
    """

    def at_account_creation(self):
        # super(ArchivableBaseMixin, self), not super(): the base sits
        # between this class and Evennia's in the MRO, and its hook refuses.
        super(ArchivableBaseMixin, self).at_account_creation()
        self.at_archive_init()

    def get_owner_lockstring(self, character):
        """The ownership locks written onto a character this account creates.

        Evennia writes these at creation with primary keys as literals —
        ``puppet:id(3) or pid(2) or perm(Developer) or pperm(Developer)``.
        Both keys change on every restore, so the locks come back naming
        objects that no longer exist and the owning account is refused its
        own character, with nothing in any log.

        `owns_character` asks the same question against values that cannot
        drift. It reads the owner off the character's stamp, so nothing has
        to be carried in the string and the two cannot disagree.

        The permission clauses are Evennia's own, kept so an administrator
        and a superuser get in exactly as before.

        Takes the character so a consumer overriding this can vary the
        locks by what was created; the library's own answer does not.
        """
        return ";".join(
            [
                "puppet:owns_character() or perm(Developer) or pperm(Developer)",
                "edit:owns_character() or perm(Admin)",
                "delete:owns_character() or perm(Admin)",
            ]
        )

    def at_post_create_character(self, character, **kwargs):
        """Stamp a new character with this account, and fix its locks.

        Evennia's own body runs first: it adds the character to the roster,
        sets ``_last_puppet`` for the first one, and writes the primary-key
        locks this then replaces.

        Safe to call again. A consumer whose chargen builds characters
        without going through ``create_character`` calls this themselves,
        so a second call has to leave the same result rather than a second
        set of lock clauses.
        """
        super(ArchivableBaseMixin, self).at_post_create_character(
            character, **kwargs
        )

        # Not every Character in a game belongs to an account. One that
        # cannot read the stamp back has no business carrying it, and
        # nothing should be locked to an owner it does not name.
        if not isinstance(character, ArchivableCharacterMixin):
            return

        # Never overwrite: a character that already names an owner keeps
        # it, so calling this from a second account cannot steal one.
        if not character.owner_account_archive_id:
            character.attributes.add(
                OWNER_ACCOUNT_KEY, self.at_archive_init(), strattr=True
            )

        # An upsert per access type: LockHandler rebuilds db_lock_storage
        # from a dict keyed on access type, so these three replace Evennia's
        # outright and the eleven others are left as they were.
        character.locks.add(self.get_owner_lockstring(character))
