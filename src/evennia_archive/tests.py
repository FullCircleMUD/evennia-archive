# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for evennia-archive.

Run via ``python runtests.py`` from the library root.
"""
import os
import uuid
from unittest import TestCase as PlainTestCase
from unittest import mock

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.test import override_settings
from evennia.accounts.accounts import DefaultAccount
from evennia.locks import lockhandler
from evennia.accounts.models import AccountDB
from evennia.objects.models import ObjectDB
from evennia.typeclasses.models import Attribute
from evennia.objects.objects import DefaultCharacter, DefaultObject
from evennia.utils.create import create_account, create_object
from evennia.utils.test_resources import BaseEvenniaTest

import evennia_archive
from evennia_archive import config
from evennia_archive.config import check_settings
from evennia_archive.api import (
    NotArchivable,
    NotArchived,
    RENAMED_FROM_KEY,
    _copy_attributes,
    _purge_attributes,
    _purge_tag_links,
    archive,
    delete,
    find_by_attribute,
    find_by_column,
    restore,
)
from evennia_archive.lockfuncs import owns_character
from evennia_archive.log import archive_log
from evennia_archive.mixins import (
    ARCHIVE_ID_KEY,
    OWNER_ACCOUNT_KEY,
    ArchivableAccountMixin,
    ArchivableBaseMixin,
    ArchivableCharacterMixin,
    ArchivableObjectMixin,
)
from evennia_archive.models import ArchiveRecord


class ArchivableTestObject(ArchivableObjectMixin, DefaultObject):
    """A minimal typeclass carrying the object mixin, for tests only."""


class ArchivableTestCharacter(ArchivableCharacterMixin, DefaultCharacter):
    """A minimal character typeclass, for the stamp and the locks."""


class PlainTestCharacter(DefaultCharacter):
    """A character with no archivable mixin — `AM-06`.

    Not every Character in a game belongs to a player. An account creating
    one of these must not raise, and must not stamp it.
    """


class ObjectMixinTestCharacter(ArchivableObjectMixin, DefaultCharacter):
    """Archivable, but not declared as account-owned — `AM-06`.

    The discriminating fixture. A character carrying no mixin at all is
    excluded by any check, so it cannot tell whether the stamp is gated on
    the character mixin or merely on archivability. This one can.
    """


# Evennia's BaseEvenniaTest replaces LOCK_FUNC_MODULES outright, so its own
# two entries are repeated here — the override does not extend, and dropping
# them would take every built-in lockfunc with it.
_LOCK_FUNC_MODULES = (
    "evennia.locks.lockfuncs",
    "evennia.game_template.server.conf.lockfuncs",
    "evennia_archive.lockfuncs",
)


class LookalikeTestObject(DefaultObject):
    """Exposes ``archive_id`` without the mixin, and is refused anyway.

    AR-09 turns on this distinction. The attribute alone says nothing about
    how the value was minted or whether it is unique, and ``restore()``
    matches live rows on it.
    """

    @property
    def archive_id(self):
        return "hand-rolled-identity"


class BaseOnlyTestObject(ArchivableBaseMixin, DefaultObject):
    """The mistake `ID-07` guards against — the base mixed in directly.

    The base owns the identity and nothing else. A typeclass declaring it
    instead of `ArchivableObjectMixin` gets no creation hook that works,
    so the base refuses rather than letting the object exist without one.
    """


class BaseOnlyTestAccount(ArchivableBaseMixin, DefaultAccount):
    """The same mistake on an account — `ID-08`."""


class ObjectMixinTestObject(ArchivableObjectMixin, DefaultObject):
    """The kind-specific mixin, declared the way a consumer would."""


class OverridingTestObject(ArchivableObjectMixin, DefaultObject):
    """A consumer overriding the hook — `OM-03`.

    Plain ``super()`` here is correct and documented: it lands on
    `ArchivableObjectMixin`, not on the base. The grandparent rule binds
    only on children of the base, which is the library's own business.
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.consumer_hook_ran = True


class MarkerMixin:
    """A consumer mixin sitting between ours and Evennia's — `OM-04`."""

    def at_object_creation(self):
        super().at_object_creation()
        self.db.marker_hook_ran = True


class LayeredTestObject(ArchivableObjectMixin, MarkerMixin, DefaultObject):
    """`OM-04` — the marker must not be skipped on the way to Evennia."""


def _read_back_logs():
    """Everything under the suite's LOG_DIR, as one string.

    Delivery is asserted by reading the file back — a mocked shim passes
    whether or not a line ever reached a file. Shared by every class with
    read-back cases (`CS`, `LO`).
    """
    text = []
    for name in sorted(os.listdir(settings.LOG_DIR)):
        if name.endswith(".log"):
            path = os.path.join(settings.LOG_DIR, name)
            with open(path, encoding="utf-8") as handle:
                text.append(handle.read())
    return "\n".join(text)


def _clear_logs():
    """Point Evennia's writer at the suite's LOG_DIR and empty it.

    The log directory is latched module-globally at the first-ever write.
    In the full suite that write happens inside Evennia's test scaffolding,
    under settings whose LOG_DIR is the game template in site-packages — so
    every later line lands there and a read-back here finds nothing.
    Re-pointing the latch and dropping the cached handles makes delivery
    land where these settings say.

    Files are truncated, never removed: a removed file would leave a cached
    handle writing to an unlinked inode, and every later line would
    silently vanish.
    """
    from evennia.utils import logger as evennia_logger

    evennia_logger._LOGDIR = settings.LOG_DIR
    for handle in evennia_logger._LOG_FILE_HANDLES.values():
        handle.close()
    evennia_logger._LOG_FILE_HANDLES.clear()
    evennia_logger._LOG_FILE_HANDLE_COUNTS.clear()

    for name in os.listdir(settings.LOG_DIR):
        if name.endswith(".log"):
            with open(os.path.join(settings.LOG_DIR, name), "w"):
                pass


class TestPackageInstalls(PlainTestCase):
    """Smoke test: the package imports and Django loads it as an app."""

    def test_version_is_exposed(self):
        """SM-01"""
        self.assertEqual(evennia_archive.__version__, "0.1.0")

    def test_registered_in_installed_apps(self):
        """SM-02"""
        self.assertIn("evennia_archive", settings.INSTALLED_APPS)


class TestLogShim(PlainTestCase):
    """LG — the logging shim binds; the mechanism is evennia-logging-extension's."""

    def test_the_log_shim_binds_and_a_call_returns_none(self):
        """LG-01"""
        self.assertIsNone(archive_log("scaffold check"))


class TestArchivableBaseMixin(BaseEvenniaTest):
    """Identity is minted at creation, canonical, immutable and unpickled.

    Exercised through a concrete child, since the base refuses to create.
    """

    # Creating an account or a character now writes to the archive, so a
    # class that creates one has to declare the alias.
    databases = {"default", "archive"}

    def _make(self):
        return create_object(ArchivableTestObject, key="subject")

    def test_minted_id_is_a_canonical_uuid(self):
        """ID-02"""
        obj = self._make()
        # Round-tripping through uuid.UUID and back must be a no-op. This
        # is what makes plain string equality a safe lookup: a value that
        # differed in case or formatting would fail to match despite
        # being the same identifier.
        self.assertEqual(str(uuid.UUID(obj.archive_id)), obj.archive_id)

    def test_init_is_idempotent(self):
        """ID-03"""
        obj = self._make()
        first = obj.archive_id
        self.assertEqual(obj.at_archive_init(), first)
        self.assertEqual(obj.archive_id, first)

    def test_ids_are_unique_across_objects(self):
        """ID-04"""
        self.assertNotEqual(self._make().archive_id, self._make().archive_id)

    def test_stored_unpickled_in_strvalue(self):
        """ID-05"""
        # The load-bearing storage decision. If this ever flips to
        # db_value the attribute is pickled, lookups become a byte
        # comparison whose stability depends on the pickle protocol, and
        # nobody can read the column by hand. Locked in by this test so a
        # change breaks here rather than silently orphaning installs.
        obj = self._make()
        attr = obj.attributes.get(ARCHIVE_ID_KEY, return_obj=True, strattr=True)
        self.assertEqual(attr.db_strvalue, obj.archive_id)
        self.assertIsNone(attr.db_value)

    def test_object_without_the_mixin_has_no_identity(self):
        """ID-06"""
        plain = create_object(DefaultObject, key="plain")
        self.assertFalse(hasattr(plain, "archive_id"))

    def test_base_refuses_object_creation(self):
        """ID-07"""
        # A true abstract base is not available — Evennia's TypeclassBase
        # metaclass conflicts with ABCMeta — so refusing from the hook is
        # the guard, and it fires where the mistake is made rather than at
        # the first archive.
        with self.assertRaises(NotImplementedError) as caught:
            create_object(BaseOnlyTestObject, key="misdeclared")
        self.assertIn("ArchivableObjectMixin", str(caught.exception))

    def test_base_refuses_account_creation(self):
        """ID-08"""
        with self.assertRaises(NotImplementedError) as caught:
            create_account(
                "misdeclared",
                "misdeclared@example.com",
                "sekritpw",
                typeclass=BaseOnlyTestAccount,
            )
        self.assertIn("ArchivableAccountMixin", str(caught.exception))

    def test_base_refuses_to_stamp_a_character(self):
        """ID-09"""
        # Called on the base explicitly: an account that would reach it
        # through the MRO cannot be created at all, since ID-08 refuses it.
        account = create_account(
            "wrongmixin",
            "wrongmixin@example.com",
            "sekritpw",
            typeclass=ArchivableTestAccount,
        )
        character = create_object(ArchivableTestCharacter, key="Rowan")
        with self.assertRaises(NotImplementedError) as caught:
            ArchivableBaseMixin.at_post_create_character(account, character)
        self.assertIn("ArchivableAccountMixin", str(caught.exception))


@override_settings(LOCK_FUNC_MODULES=_LOCK_FUNC_MODULES)
class TestArchivableAccountMixin(BaseEvenniaTest):
    """The kind-specific mixin for accounts.

    Registers the library's lockfuncs for the same reason `LF` does: the
    hook writes `owns_character()` into a lockstring, and a clause naming
    an unregistered function cannot be parsed.
    """

    databases = {"default", "archive"}

    def setUp(self):
        super().setUp()
        # _LOCKFUNCS is a process-wide cache, built once on the first
        # LockHandler ever constructed. By the time this class runs it
        # already holds Evennia's set, so the override above changes
        # nothing until the cache is rebuilt under it.
        lockhandler._cache_lockfuncs()

    @classmethod
    def tearDownClass(cls):
        # After super() the override is lifted, so this puts the cache back
        # to what the rest of the suite expects.
        super().tearDownClass()
        lockhandler._cache_lockfuncs()

    def _account(self, key="rowan"):
        return create_account(
            key, f"{key}@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )

    def _created_character(self, account=None, typeclass=ArchivableTestCharacter):
        """A character put through the account's hook, as Evennia would.

        `create_character` is not used: it needs a full gamedir's chargen
        settings. The two steps it takes that matter here are creating the
        object and calling the hook, so this does both.
        """
        account = account or self._account()
        character = create_object(typeclass, key="Rowan")
        account.at_post_create_character(character)
        return account, character

    def _in_archive(self, archive_id):
        return (
            ArchiveRecord.objects.using("archive")
            .filter(pk=str(archive_id))
            .exists()
        )

    def test_an_account_mixin_account_is_archivable(self):
        """AM-02"""
        account = self._account()
        self.assertEqual(archive(account).archive_id, account.archive_id)

    def test_a_new_character_is_archived(self):
        """AM-14

        The hook mints the character an identity, and an identity with no
        row behind it names an archive entry that does not exist —
        `restore()` on it raises.
        """
        _, character = self._created_character()
        self.assertTrue(self._in_archive(character.archive_id))

    def test_the_archived_copy_carries_the_stamp_and_locks(self):
        """AM-15

        Stored after the stamp and the lock rewrite, not before. Asserted
        through a round trip rather than by reading the archive's rows,
        because what matters is what comes back.
        """
        account, character = self._created_character()
        archive_id = character.archive_id
        character.delete()

        restored = restore(archive_id)

        self.assertEqual(restored.owner_account_archive_id, account.archive_id)
        self.assertIn("owns_character()", restored.locks.get("puppet"))

    def test_a_character_without_the_mixin_is_not_archived(self):
        """AM-16

        The hook returns before it stamps one, and it has to return before
        it stores one too.
        """
        account = self._account()
        character = create_object(PlainTestCharacter, key="Rowan")
        before = ArchiveRecord.objects.using("archive").count()

        account.at_post_create_character(character)

        self.assertEqual(
            ArchiveRecord.objects.using("archive").count(), before
        )

    def test_a_new_account_is_archived(self):
        """AM-17

        The same rule as a character, at the hook that mints an account's
        identity.
        """
        account = self._account()
        self.assertTrue(self._in_archive(account.archive_id))

    def test_account_one_is_not_archived_at_creation(self):
        """AM-22

        Evennia requires `#1` on every instance and makes one at first
        boot. It belongs where it was made — restoring it anywhere would
        displace that instance's own — so an archived copy could never be
        used for anything.

        The hook is called directly, with the key faked: `#1` is made by
        Evennia's initial setup and a test cannot arrange one.
        """
        account = self._account()

        with mock.patch.object(type(account), "pk", 1), mock.patch(
            "evennia_archive.api.archive"
        ) as archiving:
            account.at_account_creation()

        archiving.assert_not_called()

    def test_another_superuser_is_archived(self):
        """AM-23

        Being a superuser is not the reason `#1` is skipped. A second
        superuser is not what Evennia demands be present and its name
        collides with nothing, so it is archived like any other account.
        """
        account = self._account()
        account.is_superuser = True
        account.save()

        with mock.patch("evennia_archive.api.archive") as archiving:
            account.at_account_creation()

        archiving.assert_called_once_with(account)

    def _departed(self, key="rowan"):
        """An account archived and then gone from the live database."""
        account = self._account(key)
        account.delete()

    def test_a_username_held_in_the_archive_is_refused(self):
        """AM-18

        The archive carries Evennia's UNIQUE on username, so a name it
        holds cannot be taken by anyone else. Refused here rather than
        left to fail at the point of archiving, where it lands on
        registration.
        """
        self._departed("rowan")

        valid, errors = ArchivableTestAccount.validate_username("rowan")

        self.assertFalse(valid)
        self.assertTrue(any("rowan" in error for error in errors), errors)

    def test_a_free_username_is_accepted(self):
        """AM-19"""
        valid, errors = ArchivableTestAccount.validate_username("mirren")

        self.assertTrue(valid, errors)
        self.assertEqual(errors, [])

    def test_evennias_own_refusal_stands(self):
        """AM-20

        The archive is consulted after the local check, not instead of
        it. A name taken in the live database is still refused with
        Evennia's own message.
        """
        self._account("rowan")

        valid, errors = ArchivableTestAccount.validate_username("rowan")

        self.assertFalse(valid)
        self.assertTrue(errors)

    def test_an_archived_username_is_refused_whatever_the_case(self):
        """AM-21

        Evennia authenticates case-insensitively, so `Rowan` and `rowan`
        are one account to it. A check that missed the difference would
        let the collision straight back in.
        """
        self._departed("rowan")

        valid, _ = ArchivableTestAccount.validate_username("RoWaN")

        self.assertFalse(valid)

    def test_stamps_the_character_with_its_owner(self):
        """AM-03"""
        account, character = self._created_character()
        self.assertEqual(character.owner_account_archive_id, account.archive_id)

    def test_the_stamp_is_stored_unpickled(self):
        """AM-04"""
        # find_by_attribute() matches db_strvalue. Pickled, the same
        # value would be a byte comparison whose stability depends on the
        # pickle protocol.
        _, character = self._created_character()
        attr = character.attributes.get(
            OWNER_ACCOUNT_KEY, return_obj=True, strattr=True
        )
        self.assertTrue(attr.db_strvalue)
        self.assertIsNone(attr.db_value)

    def test_the_stamp_is_never_overwritten(self):
        """AM-05"""
        first = self._account("rowan")
        character = create_object(ArchivableTestCharacter, key="Rowan")
        character.attributes.add(OWNER_ACCOUNT_KEY, first.archive_id, strattr=True)

        self._account("sable").at_post_create_character(character)

        self.assertEqual(character.owner_account_archive_id, first.archive_id)

    def test_a_character_without_the_mixin_is_left_alone(self):
        """AM-06"""
        # Not every Character in a game is a player's. One that cannot read
        # the stamp back has no business carrying it.
        for index, typeclass in enumerate(
            (PlainTestCharacter, ObjectMixinTestCharacter)
        ):
            with self.subTest(typeclass=typeclass.__name__):
                _, character = self._created_character(
                    account=self._account(f"owner{index}"), typeclass=typeclass
                )
                self.assertIsNone(
                    character.attributes.get(OWNER_ACCOUNT_KEY, strattr=True)
                )

    def test_evennias_own_hook_still_runs(self):
        """AM-07"""
        account, character = self._created_character()
        self.assertIn(character, account.characters.all())
        self.assertEqual(account.db._last_puppet, character)

    def test_the_puppet_lock_uses_the_lockfunc(self):
        """AM-08"""
        _, character = self._created_character()
        self.assertEqual(
            character.locks.get("puppet"),
            "puppet:owns_character() or perm(Developer) or pperm(Developer)",
        )

    def test_the_edit_and_delete_locks_use_the_lockfunc(self):
        """AM-09"""
        _, character = self._created_character()
        self.assertEqual(
            character.locks.get("edit"), "edit:owns_character() or perm(Admin)"
        )
        self.assertEqual(
            character.locks.get("delete"), "delete:owns_character() or perm(Admin)"
        )

    def test_no_primary_key_clause_survives(self):
        """AM-10"""
        # AM-08 passes as soon as one working clause exists, and would not
        # notice a dead pid() sitting beside it in db_lock_storage.
        _, character = self._created_character()
        for access_type in ("puppet", "edit", "delete"):
            clause = character.locks.get(access_type)
            self.assertNotIn("id(", clause.replace("owns_character(", ""))
        self.assertNotIn("pid(", character.db_lock_storage)

    def test_the_permission_clauses_survive(self):
        """AM-11"""
        # The other half of AM-10: a rewrite thorough enough to drop the
        # stale clauses could just as easily drop an operator's way in.
        _, character = self._created_character()
        self.assertIn("perm(Developer)", character.locks.get("puppet"))
        self.assertIn("pperm(Developer)", character.locks.get("puppet"))
        self.assertIn("perm(Admin)", character.locks.get("edit"))
        self.assertIn("perm(Admin)", character.locks.get("delete"))

    def test_two_characters_share_the_owner_stamp(self):
        """AM-13"""
        # The stamp is the account's identity, not anything derived from the
        # character. That is what lets a consumer find a whole roster by one
        # value rather than one character at a time.
        account = self._account()
        first = create_object(ArchivableTestCharacter, key="Rowan")
        second = create_object(ArchivableTestCharacter, key="Bramble")
        account.at_post_create_character(first)
        account.at_post_create_character(second)

        self.assertEqual(
            first.owner_account_archive_id, second.owner_account_archive_id
        )
        self.assertEqual(first.owner_account_archive_id, account.archive_id)

    def test_unnamed_access_types_survive(self):
        """AM-12"""
        # locks.add is an upsert per access type: the ones it names are
        # replaced outright, the ones it does not are untouched. AM-08 and
        # AM-09 prove the replacement; this proves the rewrite is three
        # clauses rather than a whole lockstring.
        untouched = (
            "control",
            "examine",
            "view",
            "get",
            "drop",
            "call",
            "tell",
            "teleport",
            "teleport_here",
            "boot",
            "msg",
        )
        account = self._account()
        character = create_object(ArchivableTestCharacter, key="Rowan")
        before = {t: character.locks.get(t) for t in untouched}
        # Otherwise the comparison below could pass on two empty dicts.
        self.assertTrue(any(before.values()))

        account.at_post_create_character(character)

        self.assertEqual({t: character.locks.get(t) for t in untouched}, before)


class TestArchivableCharacterMixin(BaseEvenniaTest):
    """The kind-specific mixin for characters, extending the object one."""

    databases = {"default", "archive"}

    def test_creation_mints_an_id(self):
        """CM-01"""
        # Inherited from ArchivableObjectMixin: a Character is an Object and
        # mints through the same hook.
        character = create_object(ArchivableTestCharacter, key="Rowan")
        self.assertTrue(character.archive_id)

    def _owned(self, key="Rowan", owner="1a04b6d2-0f4e-4f6a-9c9b-2f2a8d3e4c5f"):
        """A character stamped with an owner, without an account creating it.

        The stamp is what the account's hook writes; setting it directly
        keeps these cases about the character mixin rather than about the
        account's creation path.
        """
        character = create_object(ArchivableTestCharacter, key=key)
        character.attributes.add(OWNER_ACCOUNT_KEY, owner, strattr=True)
        return character

    def test_owner_accessor_returns_the_stamp(self):
        """CM-02"""
        character = self._owned()
        self.assertEqual(
            character.owner_account_archive_id,
            "1a04b6d2-0f4e-4f6a-9c9b-2f2a8d3e4c5f",
        )

    def test_a_character_with_no_owner_reads_as_none(self):
        """CM-03"""
        # Reading is not archiving. An NPC typed as a Character has no
        # owner, and asking for one is not an error — AR-11 is where the
        # misdeclaration is refused.
        character = create_object(ArchivableTestCharacter, key="Guard")
        self.assertIsNone(character.owner_account_archive_id)

    def test_a_character_mixin_character_is_archivable(self):
        """CM-04"""
        character = self._owned()
        self.assertEqual(archive(character).archive_id, character.archive_id)


@override_settings(LOCK_FUNC_MODULES=_LOCK_FUNC_MODULES)
class TestOwnsCharacterLockFunc(BaseEvenniaTest):
    """LF — the lock function the library ships.

    One question: is the accessor the account that owns this character.

    The registration is overridden here rather than set in
    tests/test_settings.py, because Evennia's own BaseEvenniaTest applies
    an override_settings that replaces LOCK_FUNC_MODULES outright — so
    anything a project registers is invisible inside it.
    """

    # Creating an account or a character now writes to the archive, so a
    # class that creates one has to declare the alias.
    databases = {"default", "archive"}

    def setUp(self):
        super().setUp()
        lockhandler._cache_lockfuncs()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        lockhandler._cache_lockfuncs()

    def _account(self, key="rowan"):
        return create_account(
            key, f"{key}@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )

    def _character_of(self, account, key="Rowan"):
        character = create_object(ArchivableTestCharacter, key=key)
        character.attributes.add(OWNER_ACCOUNT_KEY, account.archive_id, strattr=True)
        return character

    def test_the_owning_account_is_granted(self):
        """LF-01"""
        account = self._account()
        self.assertTrue(owns_character(account, self._character_of(account)))

    def test_another_account_is_refused(self):
        """LF-02"""
        character = self._character_of(self._account("rowan"))
        self.assertFalse(owns_character(self._account("sable"), character))

    def test_a_character_with_no_owner_refuses_everyone(self):
        """LF-03"""
        # The dangerous shape is both sides absent. Comparing None to None
        # would be true and would hand every unowned character to every
        # account, silently and completely.
        orphan = create_object(ArchivableTestCharacter, key="Guard")
        self.assertIsNone(orphan.owner_account_archive_id)
        self.assertFalse(owns_character(self._account(), orphan))
        self.assertFalse(
            owns_character(create_object(DefaultObject, key="rock"), orphan)
        )

    def test_a_puppeted_character_resolves_to_its_account(self):
        """LF-04"""
        # edit and delete are checked with the character as accessor, so
        # without this the owner would be refused its own character.
        account = self._account()
        character = self._character_of(account)
        character.db_account = account
        self.assertTrue(owns_character(character, character))

    def test_an_unpuppeted_character_is_refused(self):
        """LF-05"""
        character = self._character_of(self._account())
        self.assertIsNone(character.db_account)
        self.assertFalse(owns_character(character, character))

    def test_resolves_out_of_a_real_lockstring(self):
        """LF-06"""
        # The only case that exercises it as a lock function: parsed from a
        # string, resolved through LOCK_FUNC_MODULES, and called with the
        # arguments Evennia chooses.
        account = self._account()
        character = self._character_of(account)
        character.locks.add("puppet:owns_character()")
        self.assertTrue(character.access(account, "puppet"))
        self.assertFalse(character.access(self._account("sable"), "puppet"))


class TestArchivableObjectMixin(BaseEvenniaTest):
    """The kind-specific mixin for objects: it mints, and it calls up."""

    databases = {"default", "archive"}

    def test_creation_mints_an_id(self):
        """OM-01"""
        obj = create_object(ObjectMixinTestObject, key="subject")
        self.assertTrue(obj.archive_id)

    def test_a_consumer_override_calling_plain_super_still_mints(self):
        """OM-03"""
        obj = create_object(OverridingTestObject, key="subject")
        self.assertTrue(obj.archive_id)
        self.assertTrue(obj.db.consumer_hook_ran)

    def test_an_object_mixin_object_is_archivable(self):
        """OM-02"""
        # _identity_of has to test the base, not one of the children —
        # testing a child would refuse the other two kinds outright.
        obj = create_object(ObjectMixinTestObject, key="subject")
        self.assertEqual(archive(obj).archive_id, obj.archive_id)

    def test_a_mixin_below_ours_still_gets_its_hook(self):
        """OM-04"""
        # super(ArchivableBaseMixin, self) has to resume immediately after
        # the base. Skipping further would silently drop every hook between
        # us and Evennia — a consumer's mixin would stop running with
        # nothing to show for it.
        obj = create_object(LayeredTestObject, key="subject")
        self.assertTrue(obj.archive_id)
        self.assertTrue(obj.db.marker_hook_ran)


class TestArchive(BaseEvenniaTest):
    """archive() copies an object into the archive and records where."""

    # Django only creates test databases for aliases a class declares.
    # Without this the archive alias is never built and every query
    # against it raises DatabaseOperationForbidden.
    databases = {"default", "archive"}

    def _make(self, key="subject", **kwargs):
        return create_object(ArchivableTestObject, key=key, **kwargs)

    def test_refuses_an_object_without_identity(self):
        """AR-01"""
        plain = create_object(DefaultObject, key="plain")
        with self.assertRaises(NotArchivable):
            archive(plain)

    def test_refuses_a_hand_rolled_archive_id(self):
        """AR-09"""
        lookalike = create_object(LookalikeTestObject, key="lookalike")
        self.assertEqual(lookalike.archive_id, "hand-rolled-identity")
        with self.assertRaises(NotArchivable):
            archive(lookalike)

    def test_refuses_a_mixin_object_never_initialised(self):
        """AR-10"""
        obj = self._make(key="Uninitialised")
        obj.attributes.remove(ARCHIVE_ID_KEY)
        self.assertIsNone(obj.archive_id)
        with self.assertRaises(NotArchivable) as caught:
            archive(obj)
        self.assertIn("at_archive_init", str(caught.exception))

    def test_creates_a_copy_in_the_archive(self):
        """AR-02"""
        obj = self._make(key="Rowan")
        record = archive(obj)

        key = ObjectDB.objects.using("archive").values_list(
            "db_key", flat=True
        ).get(pk=record.archived_pk)
        self.assertEqual(key, "Rowan")
        self.assertEqual(record.archived_model, "objectdb")

    def test_copy_does_not_land_in_the_live_database(self):
        """AR-03"""
        obj = self._make(key="Rowan")
        archive(obj)
        # Two rows named Rowan in `default` would mean the copy was
        # written to the wrong alias — the failure the router exists to
        # prevent, and one that would otherwise look like success.
        self.assertEqual(ObjectDB.objects.filter(db_key="Rowan").count(), 1)

    def test_attributes_come_across(self):
        """AR-04"""
        obj = self._make()
        obj.db.level = 12
        obj.db.skills = {"blades": 3}
        record = archive(obj)

        # Read as values, never as instances. ObjectDB is a
        # SharedMemoryModel, so .get() consults the idmapper — which is
        # keyed on pk with no database in the key — and hands back
        # whichever live object holds that number.
        copied = _attr_rows("archive", _link_ids("archive", ObjectDB, record.archived_pk))
        source = _attr_rows("default", _link_ids("default", ObjectDB, obj.pk))
        self.assertEqual(copied["level"]["db_value"], source["level"]["db_value"])
        self.assertEqual(copied["skills"]["db_value"], source["skills"]["db_value"])

    def test_identity_comes_across(self):
        """AR-05"""
        obj = self._make()
        record = archive(obj)
        copied = _attr_rows("archive", _link_ids("archive", ObjectDB, record.archived_pk))
        self.assertEqual(copied[ARCHIVE_ID_KEY]["db_strvalue"], obj.archive_id)

    def test_second_archive_updates_rather_than_duplicates(self):
        """AR-06"""
        obj = self._make(key="Rowan")
        first = archive(obj)

        obj.key = "Rowan the Grey"
        obj.db.level = 20
        second = archive(obj)

        self.assertEqual(first.archived_pk, second.archived_pk)
        self.assertEqual(ObjectDB.objects.using("archive").count(), 1)
        self.assertEqual(ArchiveRecord.objects.using("archive").count(), 1)

        key = ObjectDB.objects.using("archive").values_list(
            "db_key", flat=True
        ).get(pk=second.archived_pk)
        self.assertEqual(key, "Rowan the Grey")

    def test_the_returned_identity_is_a_string(self):
        """AR-12

        Both calls, not just the first. A record loaded from the database
        reads the column back as a `uuid.UUID`; the return type must not
        depend on whether a copy already existed.
        """
        obj = self._make(key="Rowan")

        first = archive(obj)
        second = archive(obj)

        self.assertEqual(first.archive_id, obj.archive_id)
        self.assertEqual(second.archive_id, obj.archive_id)
        self.assertIsInstance(second.archive_id, str)

    def test_removed_attributes_are_removed_from_the_copy(self):
        """AR-07"""
        obj = self._make()
        obj.db.doomed = "here"
        archive(obj)

        del obj.db.doomed
        record = archive(obj)

        copied = _attr_keys("archive", _link_ids("archive", ObjectDB, record.archived_pk))
        # A positive alongside the negative: a read that returned the wrong
        # object would also not contain "doomed", and would say nothing.
        self.assertIn(ARCHIVE_ID_KEY, copied)
        self.assertNotIn("doomed", copied)

    def test_refuses_a_character_with_no_owner(self):
        """AR-11"""
        # ArchivableCharacterMixin declares that an account owns the object,
        # and an account stamps every character it creates. No stamp means
        # no account created it — an NPC wearing a mixin meant for players.
        npc = create_object(ArchivableTestCharacter, key="Guard")
        self.assertIsNone(npc.owner_account_archive_id)
        with self.assertRaises(NotArchivable) as caught:
            archive(npc)
        self.assertIn("ArchivableObjectMixin", str(caught.exception))

    def test_location_reference_is_dropped(self):
        """AR-08"""
        room = create_object(DefaultObject, key="somewhere")
        obj = self._make(location=room)
        record = archive(obj)

        copied = (
            ObjectDB.objects.using("archive")
            .filter(pk=record.archived_pk)
            .values("db_key", "db_location_id")
            .first()
        )
        # The key confirms this is the archived row and not whatever the
        # idmapper holds at that number — every object has a null location
        # until something sets one, so the assertion below is only
        # meaningful once the row is identified.
        self.assertEqual(copied["db_key"], obj.db_key)
        self.assertIsNone(copied["db_location_id"])


class TestRestore(BaseEvenniaTest):
    """restore() rebuilds an archived object in the live database."""

    databases = {"default", "archive"}

    def _archive_then_wipe(self, **attrs):
        """Archive an object, then delete it — the rebuild scenario."""
        obj = create_object(ArchivableTestObject, key="Rowan")
        for key, value in attrs.items():
            obj.attributes.add(key, value)
        obj.tags.add("veteran", category="rank")
        archive_id = obj.archive_id
        archive(obj)
        obj.delete()
        return archive_id

    def test_refuses_an_unknown_identity(self):
        """RS-01"""
        with self.assertRaises(NotArchived):
            restore(uuid.uuid4())

    def test_round_trip_restores_the_object(self):
        """RS-02"""
        archive_id = self._archive_then_wipe(level=12, skills={"blades": 3})

        restored = restore(archive_id)

        self.assertEqual(restored.db_key, "Rowan")
        self.assertEqual(restored.db.level, 12)
        self.assertEqual(restored.db.skills, {"blades": 3})

    def test_identity_survives_the_round_trip(self):
        """RS-03"""
        archive_id = self._archive_then_wipe()
        self.assertEqual(restore(archive_id).archive_id, archive_id)

    def test_tags_survive_the_round_trip(self):
        """RS-04"""
        archive_id = self._archive_then_wipe()
        restored = restore(archive_id)
        self.assertTrue(restored.tags.get("veteran", category="rank"))

    def test_restored_object_has_a_new_primary_key(self):
        """RS-05"""
        # The point of the whole design: identity survives, dbrefs do not.
        archive_id = self._archive_then_wipe()
        record = ArchiveRecord.objects.using("archive").get(pk=archive_id)
        self.assertNotEqual(restore(archive_id).pk, record.archived_pk)

    def test_restored_object_comes_back_stripped_of_dbrefs(self):
        """RS-06"""
        # The library's whole position on placement: every reference the
        # object held was a key into a database that no longer exists, so
        # it comes back holding none of them. Where it goes next is the
        # consumer's decision.
        room = create_object(DefaultObject, key="somewhere")
        obj = create_object(ArchivableTestObject, key="Rowan", location=room)
        archive_id = obj.archive_id
        archive(obj)
        obj.delete()

        restored = restore(archive_id)
        self.assertIsNone(restored.db_location_id)
        self.assertIsNone(restored.db_home_id)

    def test_restoring_twice_does_not_duplicate(self):
        """RS-07"""
        archive_id = self._archive_then_wipe()
        first = restore(archive_id)
        second = restore(archive_id)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ObjectDB.objects.filter(db_key="Rowan").count(), 1)

    def test_return_object_false_yields_a_key(self):
        """RS-08"""
        archive_id = self._archive_then_wipe()
        result = restore(archive_id, return_object=False)
        self.assertIsInstance(result, int)

    def test_restore_stamps_last_restored(self):
        """RS-09"""
        archive_id = self._archive_then_wipe()
        before = ArchiveRecord.objects.using("archive").get(pk=archive_id)
        self.assertIsNone(before.last_restored)
        restore(archive_id)
        after = ArchiveRecord.objects.using("archive").get(pk=archive_id)
        self.assertIsNotNone(after.last_restored)


class ArchivableTestAccount(ArchivableAccountMixin, DefaultAccount):
    """A minimal account typeclass carrying the account mixin."""


class TestAccountRoundTrip(BaseEvenniaTest):
    """The same round trip, on AccountDB rather than ObjectDB.

    Accounts are the other half of what a consumer archives, and they
    differ in ways that could break the mechanism: a different creation
    hook, a unique username, and Django's PermissionsMixin bolted on.
    """

    databases = {"default", "archive"}

    def _make(self, key="rowan"):
        return create_account(
            key, f"{key}@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )

    def test_account_creation_mints_an_id(self):
        """AM-01"""
        # Covers at_account_creation, which the ObjectDB tests never reach.
        self.assertTrue(self._make().archive_id)

    def test_round_trip_restores_the_account(self):
        """AC-02"""
        account = self._make()
        account.attributes.add("wallet", "rWMKPadPqT44LqfjTWqm4mrNgxDrSMcF3Z")
        account.tags.add("founder", category="cohort")
        archive_id = account.archive_id

        archive(account)
        account.delete()
        self.assertFalse(AccountDB.objects.filter(username="rowan").exists())

        restored = restore(archive_id)

        self.assertEqual(restored.username, "rowan")
        self.assertEqual(restored.email, "rowan@example.com")
        self.assertEqual(
            restored.db.wallet, "rWMKPadPqT44LqfjTWqm4mrNgxDrSMcF3Z"
        )
        self.assertTrue(restored.tags.get("founder", category="cohort"))
        self.assertEqual(restored.archive_id, archive_id)

    def test_record_names_the_account_model(self):
        """AC-03"""
        record = archive(self._make())
        self.assertEqual(record.archived_model, "accountdb")

    def test_copy_does_not_land_in_the_live_database(self):
        """AC-04"""
        archive(self._make())
        self.assertEqual(AccountDB.objects.filter(username="rowan").count(), 1)

    def test_restored_account_has_a_new_primary_key(self):
        """AC-05"""
        account = self._make()
        archive_id = account.archive_id
        record = archive(account)
        account.delete()
        self.assertNotEqual(restore(archive_id).pk, record.archived_pk)


class TestFind(BaseEvenniaTest):
    """find_by_attribute() locates archive identifiers by attribute."""

    databases = {"default", "archive"}

    def _archived_object(self, key="subject", **attrs):
        obj = create_object(ArchivableTestObject, key=key)
        for name, value in attrs.items():
            obj.attributes.add(name, value)
        archive(obj)
        return obj.archive_id

    def test_finds_nothing_in_an_empty_archive(self):
        """FN-01"""
        self.assertEqual(find_by_attribute("wallet", "rXYZ"), [])

    def test_finds_by_unpickled_attribute(self):
        """FN-02"""
        archive_id = self._archived_object()
        # archive_id itself is stored with strattr, so this exercises the
        # db_strvalue half of the query.
        self.assertEqual(find_by_attribute(ARCHIVE_ID_KEY, archive_id), [archive_id])

    def test_finds_by_pickled_attribute(self):
        """FN-03"""
        archive_id = self._archived_object(level=12)
        self.assertEqual(find_by_attribute("level", 12), [archive_id])

    def _archived_with_strattr(self, key, value):
        """An archived object carrying one unpickled attribute."""
        obj = create_object(ArchivableTestObject, key="subject")
        obj.attributes.add(key, value, strattr=True)
        archive(obj)
        return obj.archive_id

    def test_an_unpickled_match_ignores_case(self):
        """FN-09

        The default, and the `db_strvalue` half — the only half where
        case exists at all.
        """
        archive_id = self._archived_with_strattr("callsign", "Rowan")
        self.assertEqual(find_by_attribute("callsign", "rowan"), [archive_id])

    def test_case_can_be_required(self):
        """FN-10"""
        self._archived_with_strattr("callsign", "Rowan")
        self.assertEqual(
            find_by_attribute("callsign", "rowan", case_insensitive=False), []
        )

    def test_a_pickled_match_is_unaffected_by_the_flag(self):
        """FN-11

        A pickled value is compared as bytes, so there is no case in it
        to ignore. The flag must not reach that half and quietly widen it.
        """
        archive_id = self._archived_object(title="Grey")

        self.assertEqual(find_by_attribute("title", "Grey"), [archive_id])
        self.assertEqual(find_by_attribute("title", "grey"), [])

    def test_pickled_match_is_type_sensitive(self):
        """FN-04"""
        # Documented behaviour rather than a defect: the same logical
        # value of a different type pickles to different bytes.
        self._archived_object(level=12)
        self.assertEqual(find_by_attribute("level", "12"), [])

    def test_returns_every_match(self):
        """FN-05"""
        first = self._archived_object(key="a", cohort="founder")
        second = self._archived_object(key="b", cohort="founder")
        self.assertCountEqual(find_by_attribute("cohort", "founder"), [first, second])

    def test_key_and_value_must_be_the_same_attribute(self):
        """FN-06"""
        # Chaining two filters would let an object match when one
        # attribute has the key and a different one has the value.
        self._archived_object(level=12, other="founder")
        self.assertEqual(find_by_attribute("level", "founder"), [])

    def test_searches_accounts_and_objects_together(self):
        """FN-07"""
        obj_id = self._archived_object(cohort="founder")
        account = create_account(
            "rowan", "rowan@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        account.attributes.add("cohort", "founder")
        archive(account)
        self.assertCountEqual(
            find_by_attribute("cohort", "founder"), [obj_id, account.archive_id]
        )

    def test_model_narrows_the_search(self):
        """FN-08"""
        self._archived_object(cohort="founder")
        account = create_account(
            "rowan", "rowan@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        account.attributes.add("cohort", "founder")
        archive(account)
        self.assertEqual(
            find_by_attribute("cohort", "founder", model="accountdb"),
            [account.archive_id],
        )


class TestFindByColumn(BaseEvenniaTest):
    """find_by_column() locates archive identifiers by a real column."""

    databases = {"default", "archive"}

    def _archived_account(self, username="rowan"):
        account = create_account(
            username,
            f"{username}@example.com",
            "sekritpw",
            typeclass=ArchivableTestAccount,
        )
        archive(account)
        return account

    def _archived_object(self, key="subject"):
        obj = create_object(ArchivableTestObject, key=key)
        archive(obj)
        return obj.archive_id

    def test_unknown_model_raises(self):
        """FC-01"""
        with self.assertRaises(LookupError):
            find_by_column("nosuchmodel", "db_key", "subject")

    def test_a_model_class_behaves_like_its_name(self):
        """FC-02"""
        account = self._archived_account()
        self.assertEqual(
            find_by_column(AccountDB, "username", "rowan"), [account.archive_id]
        )
        self.assertEqual(
            find_by_column(AccountDB, "username", "rowan"),
            find_by_column("accountdb", "username", "rowan"),
        )

    def test_a_column_the_model_lacks_raises(self):
        """FC-03"""
        # username is an accountdb column and nothing else's. Searching
        # objectdb for it is the mistake that made model required.
        with self.assertRaises(FieldDoesNotExist):
            find_by_column("objectdb", "username", "rowan")

    def test_a_relation_is_not_a_column(self):
        """FC-04"""
        # _meta.get_field() answers for db_attributes, so a check that
        # leaned on it alone would accept this and filter on a relation.
        with self.assertRaises(FieldDoesNotExist):
            find_by_column("objectdb", "db_attributes", "subject")

    def test_finds_nothing_in_an_empty_archive(self):
        """FC-05"""
        self.assertEqual(find_by_column("accountdb", "username", "rowan"), [])

    def test_finds_an_account_by_username_and_restores_it(self):
        """FC-06"""
        account = self._archived_account()
        archive_id = account.archive_id
        account.delete()

        found = find_by_column("accountdb", "username", "rowan")

        self.assertEqual(found, [archive_id])
        self.assertEqual(restore(found[0]).username, "rowan")

    def test_returns_every_match(self):
        """FC-07"""
        # db_key is not unique, so a column search can hit several rows.
        first = self._archived_object(key="Fred")
        second = self._archived_object(key="Fred")
        self.assertCountEqual(
            find_by_column("objectdb", "db_key", "Fred"), [first, second]
        )

    def test_searches_the_archive_not_the_live_database(self):
        """FC-08

        Proved by letting the two copies diverge. `username` is a column
        on the live table as well, so a query that leaked to the default
        alias would answer from the live row — and both directions here
        say which row answered.
        """
        account = self._archived_account(username="mirren")
        account.username = "mirren2"
        account.save()

        # Only the archive still says "mirren".
        self.assertEqual(
            find_by_column("accountdb", "username", "mirren"),
            [account.archive_id],
        )
        # And only the live database says "mirren2".
        self.assertEqual(
            find_by_column("accountdb", "username", "mirren2"), []
        )

    def test_a_column_match_is_not_type_sensitive(self):
        """FC-09"""
        # The opposite of FN-04: Django coerces the term to the field's
        # type, so a string matches a boolean column.
        account = self._archived_account()
        self.assertEqual(
            find_by_column("accountdb", "is_active", "True"), [account.archive_id]
        )
        self.assertEqual(
            find_by_column("accountdb", "is_active", True), [account.archive_id]
        )

    def test_a_text_column_ignores_case(self):
        """FC-10

        The default. A consumer searching for a name should not have to
        know how it was capitalised when it was stored.
        """
        account = self._archived_account(username="mirren")
        self.assertEqual(
            find_by_column("accountdb", "username", "MiRRen"),
            [account.archive_id],
        )

    def test_case_can_be_required_on_a_column(self):
        """FC-11"""
        self._archived_account(username="mirren")
        self.assertEqual(
            find_by_column(
                "accountdb", "username", "MiRRen", case_insensitive=False
            ),
            [],
        )

    def test_a_non_text_column_is_unaffected_by_the_flag(self):
        """FC-12

        `iexact` on a boolean is meaningless, and asking for it must not
        break a search that works. There is no case to be insensitive
        about, so the flag falls through to an exact match.
        """
        account = self._archived_account()
        self.assertEqual(
            find_by_column("accountdb", "is_active", True),
            [account.archive_id],
        )
        self.assertEqual(
            find_by_column(
                "accountdb", "is_active", True, case_insensitive=False
            ),
            [account.archive_id],
        )


class TestDelete(BaseEvenniaTest):
    """delete() removes an archived copy and its record."""

    databases = {"default", "archive"}

    def _archived(self):
        obj = create_object(ArchivableTestObject, key="Rowan")
        obj.attributes.add("level", 12)
        obj.tags.add("veteran", category="rank")
        archive(obj)
        return obj

    def test_unknown_identity_is_quiet(self):
        """DL-01"""
        # The natural caller is a delete hook, which fires for objects
        # that were never archived. Raising there would break it.
        self.assertFalse(delete(uuid.uuid4()))

    def test_removes_the_copy_and_the_record(self):
        """DL-02"""
        obj = self._archived()
        self.assertTrue(delete(obj.archive_id))
        self.assertEqual(ArchiveRecord.objects.using("archive").count(), 0)
        self.assertEqual(ObjectDB.objects.using("archive").count(), 0)

    def test_removes_the_archived_attributes(self):
        """DL-03"""
        obj = self._archived()
        delete(obj.archive_id)
        self.assertEqual(Attribute.objects.using("archive").count(), 0)

    def test_leaves_the_live_object_alone(self):
        """DL-04"""
        obj = self._archived()
        delete(obj.archive_id)
        self.assertTrue(ObjectDB.objects.filter(pk=obj.pk).exists())
        self.assertEqual(obj.db.level, 12)

    def test_deleted_identity_can_no_longer_be_restored(self):
        """DL-05"""
        obj = self._archived()
        archive_id = obj.archive_id
        delete(archive_id)
        obj.delete()
        with self.assertRaises(NotArchived):
            restore(archive_id)

    def test_is_idempotent(self):
        """DL-06"""
        obj = self._archived()
        self.assertTrue(delete(obj.archive_id))
        self.assertFalse(delete(obj.archive_id))

    def test_shared_tags_survive_a_delete(self):
        """DL-07"""
        first = self._archived()
        second = create_object(ArchivableTestObject, key="Other")
        second.tags.add("veteran", category="rank")
        archive(second)

        delete(first.archive_id)

        record = ArchiveRecord.objects.using("archive").get(pk=second.archive_id)
        through = ObjectDB.db_tags.through
        self.assertEqual(
            through.objects.using("archive")
            .filter(objectdb_id=record.archived_pk)
            .count(),
            1,
        )


class TestRestoreUniqueCollision(BaseEvenniaTest):
    """A unique value taken while its owner was away does not block a restore."""

    databases = {"default", "archive"}

    def _archived_account(self, key="rowan"):
        account = create_account(
            key, f"{key}@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        account.attributes.add("level", 12)
        archive_id = account.archive_id
        archive(account)
        account.delete()
        return archive_id

    def _squatter(self, name):
        """Someone who took the name while its owner was away.

        A plain account typeclass, carrying no mixin. An archivable one
        would archive itself at creation and be refused by the archive's
        own UNIQUE on username — which is the collision `validate_username`
        exists to prevent, and not what these cases are about. Taking a
        name in the live database is all they need.
        """
        return create_account(
            name, f"{name}@example.com", "sekritpw", typeclass=DefaultAccount
        )

    def test_restores_under_a_numbered_name(self):
        """UC-01"""
        archive_id = self._archived_account()
        self._squatter("rowan")

        restored = restore(archive_id)

        self.assertEqual(restored.username, "rowan1")
        self.assertEqual(restored.archive_id, archive_id)

    def test_state_survives_the_rename(self):
        """UC-02"""
        # The point of renaming rather than refusing: the name is the
        # recoverable part, the progression behind it is not.
        archive_id = self._archived_account()
        self._squatter("rowan")
        self.assertEqual(restore(archive_id).db.level, 12)

    def test_original_name_is_recorded_on_the_restored_object(self):
        """UC-03"""
        archive_id = self._archived_account()
        self._squatter("rowan")
        restored = restore(archive_id)
        self.assertEqual(
            restored.attributes.get(RENAMED_FROM_KEY), {"username": "rowan"}
        )

    def test_counts_up_past_several_taken_names(self):
        """UC-04"""
        archive_id = self._archived_account()
        for taken in ("rowan", "rowan1", "rowan2"):
            self._squatter(taken)
        self.assertEqual(restore(archive_id).username, "rowan3")

    def test_nothing_recorded_when_the_name_was_free(self):
        """UC-05"""
        restored = restore(self._archived_account())
        self.assertEqual(restored.username, "rowan")
        self.assertIsNone(restored.attributes.get(RENAMED_FROM_KEY))

    def test_objects_never_collide(self):
        """UC-06"""
        # ObjectDB declares no unique fields, so two characters may share
        # a name and a restore can never be blocked by one.
        obj = create_object(ArchivableTestObject, key="Rowan")
        archive_id = obj.archive_id
        archive(obj)
        obj.delete()
        create_object(ArchivableTestObject, key="Rowan")

        restored = restore(archive_id)
        self.assertEqual(restored.db_key, "Rowan")
        self.assertIsNone(restored.attributes.get(RENAMED_FROM_KEY))


# Named here rather than taken from the library's own _link_fields: a test
# that works out the schema the same way the code does would agree with it
# even when both are wrong.
_OWNER_COLUMN = {ObjectDB: "objectdb_id", AccountDB: "accountdb_id"}


def _link_ids(alias, db_model, owner_pk):
    """Attribute ids linked to one row, read without instantiating anything.

    values_list throughout, deliberately. An assertion that went through
    the ORM could be handed a cached instance from the other database and
    report the wrong answer — which is the defect these cases cover.
    """
    through = db_model.db_attributes.through
    return sorted(
        through.objects.using(alias)
        .filter(**{_OWNER_COLUMN[db_model]: owner_pk})
        .values_list("attribute_id", flat=True)
    )


def _attr_keys(alias, ids):
    """The keys of the given attribute rows, in one database."""
    return sorted(
        Attribute.objects.using(alias)
        .filter(pk__in=list(ids))
        .values_list("db_key", flat=True)
    )


def _attr_rows(alias, ids):
    """The given attribute rows as plain dicts, keyed by attribute key."""
    return {
        row["db_key"]: row
        for row in Attribute.objects.using(alias).filter(pk__in=list(ids)).values()
    }


class TestPurgeAttributes(BaseEvenniaTest):
    """_purge_attributes clears one row's attributes, in one database only.

    Covered on its own because two of its properties are invisible from
    archive() and restore(): which database it reaches, and whether it
    builds objects on the way.
    """

    databases = {"default", "archive"}

    def _archived_object(self, key="Rowan", **attrs):
        obj = create_object(ArchivableTestObject, key=key)
        for name, value in attrs.items():
            obj.attributes.add(name, value)
        return obj, archive(obj).archived_pk

    def _archived_account(self, key="rowan", **attrs):
        account = create_account(
            key, f"{key}@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        for name, value in attrs.items():
            account.attributes.add(name, value)
        return account, archive(account).archived_pk

    def test_deletes_the_owners_attribute_rows(self):
        """PG-01"""
        _, archived_pk = self._archived_object(level=12, hometown="Dunmarrow")
        ids = _link_ids("archive", ObjectDB, archived_pk)
        self.assertTrue(ids)

        _purge_attributes(ObjectDB, "archive", archived_pk)

        # The rows themselves, not merely the links — and this is also
        # what pins the ordering. A raw delete runs no cascades, so the
        # links go first, and the ids have to be collected before that:
        # a lazy subquery would evaluate against rows already gone and
        # delete nothing.
        self.assertEqual(
            Attribute.objects.using("archive").filter(pk__in=ids).count(), 0
        )

    def test_deletes_the_owners_link_rows(self):
        """PG-02"""
        _, archived_pk = self._archived_object(level=12)
        self.assertTrue(_link_ids("archive", ObjectDB, archived_pk))

        _purge_attributes(ObjectDB, "archive", archived_pk)

        self.assertEqual(_link_ids("archive", ObjectDB, archived_pk), [])

    def test_leaves_another_owner_in_the_same_table_alone(self):
        """PG-03"""
        _, doomed_pk = self._archived_object(key="Rowan", level=12)
        _, kept_pk = self._archived_object(key="Sable", level=7)
        self.assertNotEqual(doomed_pk, kept_pk)
        before = _link_ids("archive", ObjectDB, kept_pk)

        _purge_attributes(ObjectDB, "archive", doomed_pk)

        self.assertEqual(_link_ids("archive", ObjectDB, kept_pk), before)
        self.assertIn("level", _attr_keys("archive", before))

    def test_leaves_the_same_pk_under_the_other_model_alone(self):
        """PG-04"""
        _, account_pk = self._archived_account(wallet="rWMKPadPqT44")
        _, object_pk = self._archived_object(level=12)
        # The sharp case is the two sharing a number, which is the normal
        # state of a fresh archive: nothing but the through table
        # separates accountdb 1 from objectdb 1.
        self.assertEqual(account_pk, object_pk)
        before = _link_ids("archive", ObjectDB, object_pk)

        _purge_attributes(AccountDB, "archive", account_pk)

        self.assertEqual(_link_ids("archive", ObjectDB, object_pk), before)
        self.assertEqual(_link_ids("archive", AccountDB, account_pk), [])

    def test_leaves_the_other_database_alone(self):
        """PG-05"""
        obj, archived_pk = self._archived_object(level=12, hometown="Dunmarrow")
        live_ids = _link_ids("default", ObjectDB, obj.pk)
        self.assertTrue(live_ids)

        _purge_attributes(ObjectDB, "archive", archived_pk)

        # Both databases number attributes from 1, so an alias that
        # leaked here would delete live player state.
        self.assertEqual(_link_ids("default", ObjectDB, obj.pk), live_ids)
        self.assertEqual(
            Attribute.objects.using("default").filter(pk__in=live_ids).count(),
            len(live_ids),
        )

    def test_an_owner_with_no_attributes_is_a_no_op(self):
        """PG-06"""
        _, archived_pk = self._archived_object(level=12)
        _purge_attributes(ObjectDB, "archive", archived_pk)
        self.assertEqual(_link_ids("archive", ObjectDB, archived_pk), [])

        _purge_attributes(ObjectDB, "archive", archived_pk)

        self.assertEqual(_link_ids("archive", ObjectDB, archived_pk), [])

    def test_the_default_alias_purges_the_live_database(self):
        """PG-07"""
        obj, archived_pk = self._archived_object(level=12)
        archived_ids = _link_ids("archive", ObjectDB, archived_pk)
        self.assertTrue(_link_ids("default", ObjectDB, obj.pk))

        _purge_attributes(ObjectDB, "default", obj.pk)

        self.assertEqual(_link_ids("default", ObjectDB, obj.pk), [])
        self.assertEqual(_link_ids("archive", ObjectDB, archived_pk), archived_ids)

    def test_instantiates_nothing(self):
        """PG-08"""
        _, archived_pk = self._archived_object(level=12, hometown="Dunmarrow")
        cache = Attribute.__dbclass__.__instance_cache__
        cache.clear()

        _purge_attributes(ObjectDB, "archive", archived_pk)

        # QuerySet.delete() cannot fast-path Attribute — Evennia listens
        # on pre_delete for every model, and the through tables hold
        # CASCADE keys back to it — so it would build every row as an
        # object, and the idmapper caches Attribute on pk alone with no
        # database in the key.
        self.assertEqual(dict(cache), {})


class TestCopyAttributes(BaseEvenniaTest):
    """_copy_attributes moves one row's attribute set between the databases.

    The one function both directions share. Its two hazards are only
    visible at this level: which database each end reaches, and whether
    anything is instantiated on the way.
    """

    databases = {"default", "archive"}

    def _live_object(self, key="Rowan", **attrs):
        obj = create_object(ArchivableTestObject, key=key)
        for name, value in attrs.items():
            obj.attributes.add(name, value)
        return obj

    def _empty_archive_row(self, obj):
        """An archived row for obj, with its attributes cleared."""
        archived_pk = archive(obj).archived_pk
        _purge_attributes(ObjectDB, "archive", archived_pk)
        return archived_pk

    def _archived_keys(self, archived_pk):
        return _attr_keys("archive", _link_ids("archive", ObjectDB, archived_pk))

    def test_every_attribute_comes_across(self):
        """CP-01"""
        obj = self._live_object(level=12, hometown="Dunmarrow")
        archived_pk = self._empty_archive_row(obj)
        source_keys = _attr_keys("default", _link_ids("default", ObjectDB, obj.pk))

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        self.assertEqual(self._archived_keys(archived_pk), source_keys)

    def test_a_pickled_value_survives(self):
        """CP-02"""
        obj = self._live_object(skills={"blades": 3, "lore": 1})
        archived_pk = self._empty_archive_row(obj)
        source = _attr_rows("default", _link_ids("default", ObjectDB, obj.pk))

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        # The pickled blob compared as stored, rather than unpickled through
        # the ORM — an instantiated read is the thing under suspicion.
        copied = _attr_rows("archive", _link_ids("archive", ObjectDB, archived_pk))
        self.assertEqual(copied["skills"]["db_value"], source["skills"]["db_value"])
        self.assertIsNone(copied["skills"]["db_strvalue"])

    def test_an_unpickled_value_survives(self):
        """CP-03"""
        obj = self._live_object()
        archived_pk = self._empty_archive_row(obj)

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        copied = _attr_rows("archive", _link_ids("archive", ObjectDB, archived_pk))
        self.assertEqual(copied[ARCHIVE_ID_KEY]["db_strvalue"], obj.archive_id)
        self.assertIsNone(copied[ARCHIVE_ID_KEY]["db_value"])

    def test_the_whole_row_comes_across(self):
        """CP-04"""
        obj = self._live_object()
        obj.attributes.add(
            "banner", "gold", category="heraldry", lockstring="read:all()"
        )
        archived_pk = self._empty_archive_row(obj)
        source = _attr_rows("default", _link_ids("default", ObjectDB, obj.pk))

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        copied = _attr_rows("archive", _link_ids("archive", ObjectDB, archived_pk))
        for column in ("db_category", "db_lock_storage", "db_model", "db_attrtype"):
            self.assertEqual(copied["banner"][column], source["banner"][column])
        self.assertEqual(copied["banner"]["db_category"], "heraldry")

    def test_the_destination_mints_its_own_ids(self):
        """CP-05"""
        obj = self._live_object(level=12)
        archived_pk = self._empty_archive_row(obj)
        squatter_pk = _link_ids("default", ObjectDB, obj.pk)[0]

        # A row already sitting in the destination at one of the source's
        # ids. If the id travelled with the data, this is where it collides.
        Attribute.objects.using("archive").bulk_create(
            [
                Attribute(
                    pk=squatter_pk,
                    db_key="squatter",
                    db_model="objectdb",
                    db_lock_storage="",
                )
            ]
        )
        Attribute.__dbclass__.__instance_cache__.clear()

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        surviving = (
            Attribute.objects.using("archive")
            .filter(pk=squatter_pk)
            .values_list("db_key", flat=True)
            .first()
        )
        self.assertEqual(surviving, "squatter")
        self.assertIn("level", self._archived_keys(archived_pk))

    def test_the_destination_is_replaced_not_merged(self):
        """CP-06"""
        obj = self._live_object(level=12, doomed="here")
        archived_pk = self._empty_archive_row(obj)
        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)
        self.assertIn("doomed", self._archived_keys(archived_pk))

        obj.attributes.remove("doomed")
        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        self.assertNotIn("doomed", self._archived_keys(archived_pk))

    def test_the_source_is_untouched(self):
        """CP-07"""
        obj = self._live_object(level=12, hometown="Dunmarrow")
        archived_pk = self._empty_archive_row(obj)
        before_ids = _link_ids("default", ObjectDB, obj.pk)
        before_rows = _attr_rows("default", before_ids)

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        self.assertEqual(_link_ids("default", ObjectDB, obj.pk), before_ids)
        self.assertEqual(_attr_rows("default", before_ids), before_rows)

    def test_copies_in_both_directions(self):
        """CP-08"""
        source = self._live_object(key="Rowan", level=12)
        archived_pk = self._empty_archive_row(source)
        _copy_attributes(ObjectDB, "default", source.pk, "archive", archived_pk)

        destination = create_object(ArchivableTestObject, key="Sable")
        _copy_attributes(ObjectDB, "archive", archived_pk, "default", destination.pk)

        landed = _attr_rows("default", _link_ids("default", ObjectDB, destination.pk))
        self.assertIn("level", landed)
        # Replace, not merge, in this direction too: Sable's own identity is
        # gone and Rowan's is in its place.
        self.assertEqual(landed[ARCHIVE_ID_KEY]["db_strvalue"], source.archive_id)

    def test_another_owners_attributes_are_not_swept_in(self):
        """CP-09"""
        obj = self._live_object(key="Rowan", level=12)
        self._live_object(key="Sable", secret="hidden")
        archived_pk = self._empty_archive_row(obj)

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        self.assertNotIn("secret", self._archived_keys(archived_pk))

    def test_an_owner_with_no_attributes_copies_nothing(self):
        """CP-10"""
        obj = self._live_object(level=12)
        archived_pk = self._empty_archive_row(obj)
        _purge_attributes(ObjectDB, "default", obj.pk)

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        self.assertEqual(_link_ids("archive", ObjectDB, archived_pk), [])

    def test_instantiates_nothing(self):
        """CP-11"""
        obj = self._live_object(level=12, hometown="Dunmarrow")
        archived_pk = self._empty_archive_row(obj)
        cache = Attribute.__dbclass__.__instance_cache__
        cache.clear()

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        self.assertEqual(dict(cache), {})

    def test_survives_a_poisoned_cache(self):
        """CP-12"""
        obj = self._live_object(hometown="Dunmarrow")
        archived_pk = self._empty_archive_row(obj)
        source = _attr_rows("default", _link_ids("default", ObjectDB, obj.pk))
        colliding_pk = source["hometown"]["id"]

        Attribute.objects.using("archive").bulk_create(
            [
                Attribute(
                    pk=colliding_pk,
                    db_key="impostor",
                    db_model="accountdb",
                    db_strvalue="wrong",
                    db_lock_storage="",
                )
            ]
        )
        # Clear first, or the live row is already cached at this pk and the
        # archive read hands that back instead — the defect, in the fixture.
        Attribute.__dbclass__.__instance_cache__.clear()
        # Instantiating it is the poisoning: the idmapper keys on pk with no
        # database in the key, so this now answers for the live row too.
        poison = Attribute.objects.using("archive").get(pk=colliding_pk)
        self.assertEqual(poison.pk, colliding_pk)
        self.assertNotEqual(poison.db_key, "hometown")

        _copy_attributes(ObjectDB, "default", obj.pk, "archive", archived_pk)

        keys = self._archived_keys(archived_pk)
        self.assertIn("hometown", keys)
        self.assertNotIn("impostor", keys)


# --- CS: check_settings() ----------------------------------------------------
_VALID_DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "game.db3"},
    "archive": {"ENGINE": "django.db.backends.sqlite3", "NAME": "archive.db3"},
}


class TestCheckSettings(PlainTestCase):
    """What the library refuses to boot without — `CS-01` to `CS-07`.

    Each case overrides the one entry it is about and leaves the rest valid:
    a case that broke two at once could not tell which produced the message.
    """

    def check(self, **overrides):
        """Run check_settings() under a valid settings module plus overrides."""
        base = {"DATABASES": _VALID_DATABASES,
                "LOCK_FUNC_MODULES": _LOCK_FUNC_MODULES}
        base.update(overrides)
        with override_settings(**base):
            check_settings()

    def test_complete_settings_pass(self):
        """CS-01"""
        self.check()

    def test_missing_archive_alias_raises(self):
        """CS-02"""
        with self.assertRaises(ImproperlyConfigured) as raised:
            self.check(DATABASES={"default": _VALID_DATABASES["default"]})
        self.assertIn("archive", str(raised.exception))
        self.assertIn("DATABASES", str(raised.exception))

    def test_archive_sharing_the_game_database_raises(self):
        """CS-03"""
        same = dict(_VALID_DATABASES["default"])
        with self.assertRaises(ImproperlyConfigured) as raised:
            self.check(DATABASES={"default": _VALID_DATABASES["default"], "archive": same})
        self.assertIn("same database", str(raised.exception).lower())

    def test_missing_lockfunc_module_raises(self):
        """CS-04"""
        with self.assertRaises(ImproperlyConfigured) as raised:
            self.check(LOCK_FUNC_MODULES=("evennia.locks.lockfuncs",))
        self.assertIn("evennia_archive.lockfuncs", str(raised.exception))

    def test_every_problem_in_one_raise(self):
        """CS-05"""
        with self.assertRaises(ImproperlyConfigured) as raised:
            self.check(DATABASES={"default": _VALID_DATABASES["default"]},
                       LOCK_FUNC_MODULES=("evennia.locks.lockfuncs",))
        message = str(raised.exception)
        self.assertIn("archive", message)
        self.assertIn("evennia_archive.lockfuncs", message)

    def test_a_rejected_value_does_not_stop_later_clauses(self):
        """CS-06"""
        with self.assertRaises(ImproperlyConfigured) as raised:
            self.check(DATABASES={}, LOCK_FUNC_MODULES=("evennia.locks.lockfuncs",))
        self.assertIn("evennia_archive.lockfuncs", str(raised.exception))

    def test_ready_calls_check_settings(self):
        """CS-07"""
        from evennia_archive.apps import EvenniaArchiveConfig

        with mock.patch("evennia_archive.config.check_settings") as checked:
            EvenniaArchiveConfig.ready(mock.Mock())
        checked.assert_called_once()

    # CS-09 / CS-10 assert delivery by reading LOG_DIR back — a mocked shim
    # passes whether or not a line ever reached a file. See the CS notes in
    # docs/test-plan.md.

    def test_a_refusal_is_logged_to_disk_at_error(self):
        """CS-09"""
        _clear_logs()
        with self.assertRaises(ImproperlyConfigured):
            self.check(DATABASES={"default": _VALID_DATABASES["default"]})
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn("DATABASES", logged)

    def test_the_log_line_and_the_exception_carry_the_same_text(self):
        """CS-10"""
        _clear_logs()
        with self.assertRaises(ImproperlyConfigured) as caught:
            self.check(DATABASES={"default": _VALID_DATABASES["default"]})
        self.assertIn(str(caught.exception), _read_back_logs())


# --- DS: the database spec ---------------------------------------------------
class TestDatabaseSpec(PlainTestCase):
    """The AliasSpec this library declares, and the cascade's answer to it."""

    def test_the_spec_names_the_config_alias(self):
        """DS-01"""
        import inspect

        from evennia_archive import db_spec

        # Comparing the values proves nothing: Python interns short strings, so
        # two independent "archive" literals are the same object. The fact worth
        # pinning is that the spec holds no alias literal of its own.
        source = inspect.getsource(db_spec)
        self.assertIn("from .config import", source)
        self.assertNotIn('alias="archive"', source)
        self.assertEqual(db_spec.SPEC.app_labels, ("evennia_archive",))
        self.assertEqual(db_spec.SPEC.alias, config.ARCHIVE_ALIAS)

    def test_the_spec_refuses_the_shared_rung(self):
        """DS-02"""
        from evennia_archive.db_spec import SPEC

        self.assertFalse(SPEC.allow_sharing_common_db)

    def test_the_spec_accepts_foreign_tables(self):
        """DS-03"""
        from evennia_archive.db_spec import SPEC

        self.assertTrue(SPEC.allow_foreign_tables_in_own_db)

    def test_the_spec_passes_the_cascade_validator(self):
        """DS-05"""
        from evennia_database_cascade import spec_is_valid

        from evennia_archive.db_spec import SPEC

        self.assertTrue(spec_is_valid(SPEC))

    def test_configure_resolves_and_routes_the_alias(self):
        """DS-04"""
        import tempfile

        from evennia_database_cascade import configure

        databases, routers = configure(
            {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            ["evennia_archive"],
            tempfile.gettempdir(),
            {},
        )
        self.assertIn(config.ARCHIVE_ALIAS, databases)
        self.assertTrue(
            databases[config.ARCHIVE_ALIAS]["NAME"].endswith("archive.db3")
        )
        routed = next(
            alias
            for alias in (router.db_for_write(ArchiveRecord) for router in routers)
            if alias is not None
        )
        self.assertEqual(routed, config.ARCHIVE_ALIAS)


# --- CT: config.py -----------------------------------------------------------
class TestConfigConstants(BaseEvenniaTest):
    """The constants every other module imports — `CT-01` to `CT-03`."""

    databases = {"default", "archive"}

    def test_api_routes_through_the_config_alias(self):
        """CT-02"""
        obj = create_object(ArchivableTestObject, key="router-alias")
        record = archive(obj)
        landed = (
            ObjectDB.objects.using(config.ARCHIVE_ALIAS)
            .filter(pk=record.archived_pk)
            .values_list("db_key", flat=True)
            .first()
        )
        self.assertEqual(landed, "router-alias")

    def test_attribute_keys_are_unchanged(self):
        """CT-03"""
        obj = create_object(ArchivableTestObject, key="keys")
        self.assertEqual(
            obj.attributes.get(config.ARCHIVE_ID_KEY, strattr=True), obj.archive_id)
        self.assertEqual(config.ARCHIVE_ID_KEY, "archive_id")
        self.assertEqual(config.OWNER_ACCOUNT_KEY, "owner_account_archive_id")


# --- LO: what the library logs ----------------------------------------------
class TestArchiveLogging(BaseEvenniaTest):
    """The call sites — `LO-01` to `LO-13`.

    The negative cases carry as much weight as the positive ones. Each site
    sits on a path that also runs constantly, so without them nothing stops a
    later change turning one into a line per operation.
    """

    databases = {"default", "archive"}

    def _account(self, key="rowan"):
        return create_account(
            key, f"{key}@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )

    def levels(self, logged):
        """The level of every line emitted, in order."""
        return [call.kwargs.get("level", "INFO") for call in logged.call_args_list]

    def test_self_heal_logs_a_warning(self):
        """LO-01"""
        obj = create_object(ArchivableTestObject, key="healed")
        record = archive(obj)
        # Leave the record pointing at nothing, so the next archive falls
        # through to the insert branch. The attributes and tag links go first,
        # as delete() does — dropping the row alone orphans them, and SQLite's
        # foreign key check catches it at teardown.
        _purge_attributes(ObjectDB, "archive", record.archived_pk)
        _purge_tag_links(ObjectDB, record.archived_pk)
        ObjectDB.objects.using("archive").filter(pk=record.archived_pk)._raw_delete("archive")

        with mock.patch("evennia_archive.api.archive_log") as logged:
            archive(obj)

        self.assertTrue(logged.called)
        self.assertEqual(self.levels(logged), ["WARN"])
        self.assertIn(obj.archive_id, str(logged.call_args))

    def test_an_ordinary_archive_logs_nothing(self):
        """LO-02"""
        obj = create_object(ArchivableTestObject, key="quiet")

        with mock.patch("evennia_archive.api.archive_log") as logged:
            archive(obj)
            archive(obj)

        logged.assert_not_called()

    def test_a_rename_logs_a_warning(self):
        """LO-03"""
        account = self._account()
        archive_id = account.archive_id
        archive(account)
        account.delete()
        create_account("rowan", "squatter@example.com", "sekritpw",
                       typeclass=DefaultAccount)

        with mock.patch("evennia_archive.api.archive_log") as logged:
            restore(archive_id)

        # The restore itself logs an INFO too (LO-09); what this pins is the
        # WARN, and that it names both values.
        self.assertIn("WARN", self.levels(logged))
        renames = [c for c in logged.call_args_list if c.kwargs.get("level") == "WARN"]
        self.assertEqual(len(renames), 1)
        self.assertIn("rowan", str(renames[0]))
        self.assertIn("rowan1", str(renames[0]))

    def test_a_restore_without_a_rename_logs_nothing(self):
        """LO-04"""
        account = self._account("kestrel")
        archive_id = account.archive_id
        archive(account)
        account.delete()

        with mock.patch("evennia_archive.api.archive_log") as logged:
            restore(archive_id)

        # The restore line still fires; the rename line must not.
        self.assertNotIn("WARN", self.levels(logged))

    def test_an_archive_held_username_logs_an_info(self):
        """LO-05"""
        account = self._account("held")
        archive(account)
        account.delete()

        with mock.patch("evennia_archive.mixins.archive_log") as logged:
            valid, _ = ArchivableTestAccount.validate_username("held")

        self.assertFalse(valid)
        self.assertEqual(self.levels(logged), ["INFO"])
        self.assertIn("held", str(logged.call_args))

    def test_a_free_username_logs_nothing(self):
        """LO-06"""
        with mock.patch("evennia_archive.mixins.archive_log") as logged:
            valid, _ = ArchivableTestAccount.validate_username("unclaimed")

        self.assertTrue(valid)
        logged.assert_not_called()

    def test_skipping_account_one_logs_an_info(self):
        """LO-07"""
        account = self._account("first")

        # `#1` is made by Evennia's initial setup; a test cannot arrange one,
        # so the hook is called directly with the key faked — as `AM-22` does.
        with mock.patch.object(type(account), "pk", 1), mock.patch(
            "evennia_archive.mixins.archive_log"
        ) as logged:
            account.at_account_creation()

        self.assertEqual(self.levels(logged), ["INFO"])

    def test_creating_any_other_account_logs_nothing(self):
        """LO-08"""
        with mock.patch("evennia_archive.mixins.archive_log") as logged:
            self._account("ordinary")

        logged.assert_not_called()

    def test_a_successful_restore_logs_an_info(self):
        """LO-09"""
        obj = create_object(ArchivableTestObject, key="returned")
        archive_id = obj.archive_id
        archive(obj)
        obj.delete()

        with mock.patch("evennia_archive.api.archive_log") as logged:
            restored = restore(archive_id)

        self.assertEqual(self.levels(logged), ["INFO"])
        message = str(logged.call_args)
        self.assertIn(archive_id, message)
        self.assertIn(str(restored.pk), message)

    def test_an_idempotent_restore_logs_nothing(self):
        """LO-10"""
        obj = create_object(ArchivableTestObject, key="still-here")
        archive_id = obj.archive_id
        archive(obj)

        # The object is still live, so restore() hands it back rather than
        # rebuilding it. Nothing happened, so nothing is logged.
        with mock.patch("evennia_archive.api.archive_log") as logged:
            restore(archive_id)

        logged.assert_not_called()

    def test_a_successful_delete_logs_an_info(self):
        """LO-11"""
        obj = create_object(ArchivableTestObject, key="doomed")
        archive_id = obj.archive_id
        archive(obj)

        with mock.patch("evennia_archive.api.archive_log") as logged:
            self.assertTrue(delete(archive_id))

        self.assertEqual(self.levels(logged), ["INFO"])
        self.assertIn(archive_id, str(logged.call_args))

    def test_deleting_nothing_logs_nothing(self):
        """LO-12"""
        with mock.patch("evennia_archive.api.archive_log") as logged:
            self.assertFalse(delete(str(uuid.uuid4())))

        logged.assert_not_called()

    def test_a_refusal_for_lack_of_a_mixin_logs_an_error(self):
        """LO-13"""
        _clear_logs()
        obj = create_object(DefaultObject, key="mixinless")
        with self.assertRaises(NotArchivable) as caught:
            archive(obj)
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn(str(caught.exception), logged)

    def test_a_refusal_for_a_missing_identity_logs_an_error(self):
        """LO-14"""
        _clear_logs()
        obj = create_object(ArchivableTestObject, key="uninitialised")
        obj.attributes.remove(ARCHIVE_ID_KEY)
        with self.assertRaises(NotArchivable) as caught:
            archive(obj)
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn(str(caught.exception), logged)

    def test_a_refusal_for_a_missing_owner_logs_an_error(self):
        """LO-15"""
        _clear_logs()
        npc = create_object(ArchivableTestCharacter, key="unowned npc")
        with self.assertRaises(NotArchivable) as caught:
            archive(npc)
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn(str(caught.exception), logged)

    def test_a_restore_of_an_unknown_identity_logs_an_error(self):
        """LO-16"""
        _clear_logs()
        with self.assertRaises(NotArchived) as caught:
            restore(str(uuid.uuid4()))
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn(str(caught.exception), logged)

    def test_rename_exhaustion_logs_an_error(self):
        """LO-18"""
        from evennia_archive.api import _free_the_unique_values

        for key in ("rowan", "rowan1", "rowan2"):
            self._account(key)
        _clear_logs()
        with mock.patch("evennia_archive.api.MAX_RENAME_ATTEMPTS", 2):
            with self.assertRaises(RuntimeError) as caught:
                _free_the_unique_values(AccountDB, {"username": "rowan"})
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn(str(caught.exception), logged)

    def test_a_restore_of_a_dangling_record_logs_an_error(self):
        """LO-17"""
        obj = create_object(ArchivableTestObject, key="dangling")
        record = archive(obj)
        # Leave the record pointing at nothing, as LO-01 does. The
        # attributes and tag links go first — dropping the row alone
        # orphans them, and SQLite's foreign key check catches it at
        # teardown.
        _purge_attributes(ObjectDB, "archive", record.archived_pk)
        _purge_tag_links(ObjectDB, record.archived_pk)
        ObjectDB.objects.using("archive").filter(pk=record.archived_pk)._raw_delete("archive")

        _clear_logs()
        with self.assertRaises(NotArchived) as caught:
            restore(obj.archive_id)
        logged = _read_back_logs()
        self.assertIn("[ERROR]", logged)
        self.assertIn(str(caught.exception), logged)
