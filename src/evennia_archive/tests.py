# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for evennia-archive.

Run via ``python runtests.py`` from the library root.
"""
import uuid
from unittest import TestCase as PlainTestCase

from django.conf import settings
from evennia.objects.models import ObjectDB
from evennia.objects.objects import DefaultObject
from evennia.utils.create import create_object
from evennia.utils.test_resources import BaseEvenniaTest

import evennia_archive
from evennia_archive.api import NotArchivable, archive
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
