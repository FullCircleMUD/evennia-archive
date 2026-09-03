# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for evennia-archive.

Run via ``python runtests.py`` from the library root.
"""
import uuid
from unittest import TestCase as PlainTestCase
from unittest import mock

from django.conf import settings
from evennia.accounts.accounts import DefaultAccount
from evennia.accounts.models import AccountDB
from evennia.objects.models import ObjectDB
from evennia.typeclasses.models import Attribute
from evennia.objects.objects import DefaultCharacter, DefaultObject
from evennia.utils.create import create_account, create_object
from evennia.utils.test_resources import BaseEvenniaTest

import evennia_archive
from evennia_archive.api import (
    NotArchivable,
    NotArchived,
    RENAMED_FROM_KEY,
    _copy_attributes,
    _purge_attributes,
    archive,
    delete,
    find,
    restore,
)
from evennia_archive import log as log_module
from evennia_archive.log import archive_log
from evennia_archive.mixins import (
    ARCHIVE_ID_KEY,
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


class TestPackageInstalls(PlainTestCase):
    """Smoke test: the package imports and Django loads it as an app."""

    def test_version_is_exposed(self):
        """SM-01"""
        self.assertEqual(evennia_archive.__version__, "0.1.0")

    def test_registered_in_installed_apps(self):
        """SM-02"""
        self.assertIn("evennia_archive", settings.INSTALLED_APPS)


class TestLogShim(PlainTestCase):
    """LG — the logging shim."""

    def _capture(self):
        """Return a fake Evennia logger recording every log_file call."""
        fake = mock.Mock()
        fake.log_file = mock.Mock()
        return fake

    def test_writes_to_the_library_log_file(self):
        """LG-01"""
        fake = self._capture()
        with mock.patch.dict("sys.modules", {"evennia.utils": mock.Mock(logger=fake)}):
            archive_log("restored Rowan", level="INFO")
        fake.log_file.assert_called_once_with(
            "[INFO] restored Rowan", filename="archive.log"
        )

    def test_unknown_level_coerces_to_info(self):
        """LG-02"""
        fake = self._capture()
        with mock.patch.dict("sys.modules", {"evennia.utils": mock.Mock(logger=fake)}):
            archive_log("something", level="CRITICAL")
        fake.log_file.assert_called_once_with(
            "[INFO] something", filename="archive.log"
        )

    def test_is_a_silent_noop_without_evennia(self):
        """LG-03"""
        real_import = __import__

        def refuse_evennia(name, *args, **kwargs):
            if name == "evennia.utils":
                raise ImportError("no evennia here")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=refuse_evennia):
            self.assertIsNone(archive_log("nobody hears this"))

    def test_trace_outside_an_except_block_adds_nothing(self):
        """LG-04"""
        fake = self._capture()
        with mock.patch.dict("sys.modules", {"evennia.utils": mock.Mock(logger=fake)}):
            archive_log("no exception here", trace=True)
        fake.log_file.assert_called_once_with(
            "[INFO] no exception here", filename="archive.log"
        )

    def test_trace_inside_an_except_block_appends_the_traceback(self):
        """LG-05"""
        fake = self._capture()
        with mock.patch.dict("sys.modules", {"evennia.utils": mock.Mock(logger=fake)}):
            try:
                raise ValueError("archive alias unreachable")
            except ValueError:
                archive_log("archive failed", level="ERROR", trace=True)
        (line,), kwargs = fake.log_file.call_args
        self.assertTrue(line.startswith("[ERROR] archive failed\n"))
        self.assertIn("ValueError: archive alias unreachable", line)
        self.assertEqual(kwargs["filename"], "archive.log")

    def test_log_filename_is_the_libraries_own(self):
        """LG-06"""
        self.assertEqual(log_module._LOG_FILENAME, "archive.log")


class TestArchivableBaseMixin(BaseEvenniaTest):
    """Identity is minted at creation, canonical, immutable and unpickled.

    Exercised through a concrete child, since the base refuses to create.
    """

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


class TestArchivableAccountMixin(BaseEvenniaTest):
    """The kind-specific mixin for accounts."""

    databases = {"default", "archive"}

    def test_an_account_mixin_account_is_archivable(self):
        """AM-02"""
        account = create_account(
            "rowan", "rowan@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        self.assertEqual(archive(account).archive_id, account.archive_id)


class TestArchivableCharacterMixin(BaseEvenniaTest):
    """The kind-specific mixin for characters, extending the object one."""

    databases = {"default", "archive"}

    def test_creation_mints_an_id(self):
        """CM-01"""
        # Inherited from ArchivableObjectMixin: a Character is an Object and
        # mints through the same hook.
        character = create_object(ArchivableTestCharacter, key="Rowan")
        self.assertTrue(character.archive_id)

    def test_a_character_mixin_character_is_archivable(self):
        """CM-04"""
        character = create_object(ArchivableTestCharacter, key="Rowan")
        self.assertEqual(archive(character).archive_id, character.archive_id)


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
    """find() locates archive identifiers by attribute."""

    databases = {"default", "archive"}

    def _archived_object(self, key="subject", **attrs):
        obj = create_object(ArchivableTestObject, key=key)
        for name, value in attrs.items():
            obj.attributes.add(name, value)
        archive(obj)
        return obj.archive_id

    def test_finds_nothing_in_an_empty_archive(self):
        """FN-01"""
        self.assertEqual(find("wallet", "rXYZ"), [])

    def test_finds_by_unpickled_attribute(self):
        """FN-02"""
        archive_id = self._archived_object()
        # archive_id itself is stored with strattr, so this exercises the
        # db_strvalue half of the query.
        self.assertEqual(find(ARCHIVE_ID_KEY, archive_id), [archive_id])

    def test_finds_by_pickled_attribute(self):
        """FN-03"""
        archive_id = self._archived_object(level=12)
        self.assertEqual(find("level", 12), [archive_id])

    def test_pickled_match_is_type_sensitive(self):
        """FN-04"""
        # Documented behaviour rather than a defect: the same logical
        # value of a different type pickles to different bytes.
        self._archived_object(level=12)
        self.assertEqual(find("level", "12"), [])

    def test_returns_every_match(self):
        """FN-05"""
        first = self._archived_object(key="a", cohort="founder")
        second = self._archived_object(key="b", cohort="founder")
        self.assertCountEqual(find("cohort", "founder"), [first, second])

    def test_key_and_value_must_be_the_same_attribute(self):
        """FN-06"""
        # Chaining two filters would let an object match when one
        # attribute has the key and a different one has the value.
        self._archived_object(level=12, other="founder")
        self.assertEqual(find("level", "founder"), [])

    def test_searches_accounts_and_objects_together(self):
        """FN-07"""
        obj_id = self._archived_object(cohort="founder")
        account = create_account(
            "rowan", "rowan@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        account.attributes.add("cohort", "founder")
        archive(account)
        self.assertCountEqual(find("cohort", "founder"), [obj_id, account.archive_id])

    def test_model_narrows_the_search(self):
        """FN-08"""
        self._archived_object(cohort="founder")
        account = create_account(
            "rowan", "rowan@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        account.attributes.add("cohort", "founder")
        archive(account)
        self.assertEqual(
            find("cohort", "founder", model="accountdb"), [account.archive_id]
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

    def test_restores_under_a_numbered_name(self):
        """UC-01"""
        archive_id = self._archived_account()
        # Someone else took the name while it was free.
        create_account(
            "rowan", "squatter@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )

        restored = restore(archive_id)

        self.assertEqual(restored.username, "rowan1")
        self.assertEqual(restored.archive_id, archive_id)

    def test_state_survives_the_rename(self):
        """UC-02"""
        # The point of renaming rather than refusing: the name is the
        # recoverable part, the progression behind it is not.
        archive_id = self._archived_account()
        create_account(
            "rowan", "squatter@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        self.assertEqual(restore(archive_id).db.level, 12)

    def test_original_name_is_recorded_on_the_restored_object(self):
        """UC-03"""
        archive_id = self._archived_account()
        create_account(
            "rowan", "squatter@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        restored = restore(archive_id)
        self.assertEqual(
            restored.attributes.get(RENAMED_FROM_KEY), {"username": "rowan"}
        )

    def test_counts_up_past_several_taken_names(self):
        """UC-04"""
        archive_id = self._archived_account()
        for taken in ("rowan", "rowan1", "rowan2"):
            create_account(
                taken, f"{taken}@example.com", "sekritpw",
                typeclass=ArchivableTestAccount,
            )
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
