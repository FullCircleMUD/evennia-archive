# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for evennia-archive.

Run via ``python runtests.py`` from the library root.
"""
import uuid
from unittest import TestCase as PlainTestCase

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
    archive,
    delete,
    find,
    restore,
)
from evennia_archive.mixins import ARCHIVE_ID_KEY, ArchivableMixin
from evennia_archive.models import ArchiveRecord


class ArchivableTestObject(ArchivableMixin, DefaultObject):
    """A minimal typeclass carrying the mixin, for tests only."""


class TestPackageInstalls(PlainTestCase):
    """Smoke test: the package imports and Django loads it as an app."""

    def test_version_is_exposed(self):
        self.assertEqual(evennia_archive.__version__, "0.0.1")

    def test_registered_in_installed_apps(self):
        self.assertIn("evennia_archive", settings.INSTALLED_APPS)


class TestArchivableMixin(BaseEvenniaTest):
    """Identity is minted at creation, canonical, immutable and unpickled."""

    def _make(self):
        return create_object(ArchivableTestObject, key="subject")

    def test_creation_mints_an_id(self):
        obj = self._make()
        self.assertTrue(obj.archive_id)

    def test_minted_id_is_a_canonical_uuid(self):
        obj = self._make()
        # Round-tripping through uuid.UUID and back must be a no-op. This
        # is what makes plain string equality a safe lookup: a value that
        # differed in case or formatting would fail to match despite
        # being the same identifier.
        self.assertEqual(str(uuid.UUID(obj.archive_id)), obj.archive_id)

    def test_init_is_idempotent(self):
        obj = self._make()
        first = obj.archive_id
        self.assertEqual(obj.at_archive_init(), first)
        self.assertEqual(obj.archive_id, first)

    def test_ids_are_unique_across_objects(self):
        self.assertNotEqual(self._make().archive_id, self._make().archive_id)

    def test_stored_unpickled_in_strvalue(self):
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
        plain = create_object(DefaultObject, key="plain")
        with self.assertRaises(NotArchivable):
            archive(plain)

    def test_creates_a_copy_in_the_archive(self):
        obj = self._make(key="Rowan")
        record = archive(obj)

        key = ObjectDB.objects.using("archive").values_list(
            "db_key", flat=True
        ).get(pk=record.archived_pk)
        self.assertEqual(key, "Rowan")
        self.assertEqual(record.archived_model, "objectdb")

    def test_copy_does_not_land_in_the_live_database(self):
        obj = self._make(key="Rowan")
        archive(obj)
        # Two rows named Rowan in `default` would mean the copy was
        # written to the wrong alias — the failure the router exists to
        # prevent, and one that would otherwise look like success.
        self.assertEqual(ObjectDB.objects.filter(db_key="Rowan").count(), 1)

    def test_attributes_come_across(self):
        obj = self._make()
        obj.db.level = 12
        obj.db.skills = {"blades": 3}
        record = archive(obj)

        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        values = {a.db_key: a.value for a in copy.db_attributes.all()}
        self.assertEqual(values["level"], 12)
        self.assertEqual(values["skills"], {"blades": 3})

    def test_identity_comes_across(self):
        obj = self._make()
        record = archive(obj)
        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        stored = {a.db_key: a.db_strvalue for a in copy.db_attributes.all()}
        self.assertEqual(stored[ARCHIVE_ID_KEY], obj.archive_id)

    def test_second_archive_updates_rather_than_duplicates(self):
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
        obj = self._make()
        obj.db.doomed = "here"
        archive(obj)

        del obj.db.doomed
        record = archive(obj)

        copy = ObjectDB.objects.using("archive").get(pk=record.archived_pk)
        self.assertNotIn("doomed", [a.db_key for a in copy.db_attributes.all()])

    def test_location_reference_is_dropped(self):
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
        with self.assertRaises(NotArchived):
            restore(uuid.uuid4())

    def test_round_trip_restores_the_object(self):
        archive_id = self._archive_then_wipe(level=12, skills={"blades": 3})

        restored = restore(archive_id)

        self.assertEqual(restored.db_key, "Rowan")
        self.assertEqual(restored.db.level, 12)
        self.assertEqual(restored.db.skills, {"blades": 3})

    def test_identity_survives_the_round_trip(self):
        archive_id = self._archive_then_wipe()
        self.assertEqual(restore(archive_id).archive_id, archive_id)

    def test_tags_survive_the_round_trip(self):
        archive_id = self._archive_then_wipe()
        restored = restore(archive_id)
        self.assertTrue(restored.tags.get("veteran", category="rank"))

    def test_restored_object_has_a_new_primary_key(self):
        # The point of the whole design: identity survives, dbrefs do not.
        archive_id = self._archive_then_wipe()
        record = ArchiveRecord.objects.using("archive").get(pk=archive_id)
        self.assertNotEqual(restore(archive_id).pk, record.archived_pk)

    def test_restored_object_comes_back_stripped_of_dbrefs(self):
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
        archive_id = self._archive_then_wipe()
        first = restore(archive_id)
        second = restore(archive_id)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ObjectDB.objects.filter(db_key="Rowan").count(), 1)

    def test_return_object_false_yields_a_key(self):
        archive_id = self._archive_then_wipe()
        result = restore(archive_id, return_object=False)
        self.assertIsInstance(result, int)

    def test_restore_stamps_last_restored(self):
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
        # Covers at_account_creation, which the ObjectDB tests never reach.
        self.assertTrue(self._make().archive_id)

    def test_round_trip_restores_the_account(self):
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
        record = archive(self._make())
        self.assertEqual(record.archived_model, "accountdb")

    def test_copy_does_not_land_in_the_live_database(self):
        archive(self._make())
        self.assertEqual(AccountDB.objects.filter(username="rowan").count(), 1)

    def test_restored_account_has_a_new_primary_key(self):
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
        self.assertEqual(find("wallet", "rXYZ"), [])

    def test_finds_by_unpickled_attribute(self):
        archive_id = self._archived_object()
        # archive_id itself is stored with strattr, so this exercises the
        # db_strvalue half of the query.
        self.assertEqual(find(ARCHIVE_ID_KEY, archive_id), [archive_id])

    def test_finds_by_pickled_attribute(self):
        archive_id = self._archived_object(level=12)
        self.assertEqual(find("level", 12), [archive_id])

    def test_pickled_match_is_type_sensitive(self):
        # Documented behaviour rather than a defect: the same logical
        # value of a different type pickles to different bytes.
        self._archived_object(level=12)
        self.assertEqual(find("level", "12"), [])

    def test_returns_every_match(self):
        first = self._archived_object(key="a", cohort="founder")
        second = self._archived_object(key="b", cohort="founder")
        self.assertCountEqual(find("cohort", "founder"), [first, second])

    def test_key_and_value_must_be_the_same_attribute(self):
        # Chaining two filters would let an object match when one
        # attribute has the key and a different one has the value.
        self._archived_object(level=12, other="founder")
        self.assertEqual(find("level", "founder"), [])

    def test_searches_accounts_and_objects_together(self):
        obj_id = self._archived_object(cohort="founder")
        account = create_account(
            "rowan", "rowan@example.com", "sekritpw", typeclass=ArchivableTestAccount
        )
        account.attributes.add("cohort", "founder")
        archive(account)
        self.assertCountEqual(find("cohort", "founder"), [obj_id, account.archive_id])

    def test_model_narrows_the_search(self):
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
        # The natural caller is a delete hook, which fires for objects
        # that were never archived. Raising there would break it.
        self.assertFalse(delete(uuid.uuid4()))

    def test_removes_the_copy_and_the_record(self):
        obj = self._archived()
        self.assertTrue(delete(obj.archive_id))
        self.assertEqual(ArchiveRecord.objects.using("archive").count(), 0)
        self.assertEqual(ObjectDB.objects.using("archive").count(), 0)

    def test_removes_the_archived_attributes(self):
        obj = self._archived()
        delete(obj.archive_id)
        self.assertEqual(Attribute.objects.using("archive").count(), 0)

    def test_leaves_the_live_object_alone(self):
        obj = self._archived()
        delete(obj.archive_id)
        self.assertTrue(ObjectDB.objects.filter(pk=obj.pk).exists())
        self.assertEqual(obj.db.level, 12)

    def test_deleted_identity_can_no_longer_be_restored(self):
        obj = self._archived()
        archive_id = obj.archive_id
        delete(archive_id)
        obj.delete()
        with self.assertRaises(NotArchived):
            restore(archive_id)

    def test_is_idempotent(self):
        obj = self._archived()
        self.assertTrue(delete(obj.archive_id))
        self.assertFalse(delete(obj.archive_id))

    def test_shared_tags_survive_a_delete(self):
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
