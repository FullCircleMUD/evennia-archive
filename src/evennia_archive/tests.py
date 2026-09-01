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
from evennia.objects.objects import DefaultObject
from evennia.utils.create import create_account, create_object
from evennia.utils.test_resources import BaseEvenniaTest

import evennia_archive
from evennia_archive.api import (
    NotArchivable,
    NotArchived,
    RENAMED_FROM_KEY,
    archive,
    delete,
    find,
    restore,
)
from evennia_archive import log as log_module
from evennia_archive.log import archive_log
from evennia_archive.mixins import ARCHIVE_ID_KEY, ArchivableMixin
from evennia_archive.models import ArchiveRecord


class ArchivableTestObject(ArchivableMixin, DefaultObject):
    """A minimal typeclass carrying the mixin, for tests only."""


class LookalikeTestObject(DefaultObject):
    """Exposes ``archive_id`` without the mixin, and is refused anyway.

    AR-09 turns on this distinction. The attribute alone says nothing about
    how the value was minted or whether it is unique, and ``restore()``
    matches live rows on it.
    """

    @property
    def archive_id(self):
        return "hand-rolled-identity"


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


class TestArchivableMixin(BaseEvenniaTest):
    """Identity is minted at creation, canonical, immutable and unpickled."""

    def _make(self):
        return create_object(ArchivableTestObject, key="subject")

    def test_creation_mints_an_id(self):
        """ID-01"""
        obj = self._make()
        self.assertTrue(obj.archive_id)

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

        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        values = {a.db_key: a.value for a in copy.db_attributes.all()}
        self.assertEqual(values["level"], 12)
        self.assertEqual(values["skills"], {"blades": 3})

    def test_identity_comes_across(self):
        """AR-05"""
        obj = self._make()
        record = archive(obj)
        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        stored = {a.db_key: a.db_strvalue for a in copy.db_attributes.all()}
        self.assertEqual(stored[ARCHIVE_ID_KEY], obj.archive_id)

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

        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        self.assertNotIn("doomed", [a.db_key for a in copy.db_attributes.all()])

    def test_location_reference_is_dropped(self):
        """AR-08"""
        room = create_object(DefaultObject, key="somewhere")
        obj = self._make(location=room)
        record = archive(obj)

        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        self.assertIsNone(copy.db_location_id)


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


class ArchivableTestAccount(ArchivableMixin, DefaultAccount):
    """A minimal account typeclass carrying the mixin, for tests only."""


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
        """AC-01"""
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
