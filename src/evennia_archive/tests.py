# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for evennia-archive.

Run via ``python runtests.py`` from the library root.
"""
import uuid
from unittest import TestCase as PlainTestCase

from django.conf import settings
from evennia.objects.objects import DefaultObject
from evennia.utils.create import create_object
from evennia.utils.test_resources import BaseEvenniaTest

import evennia_archive
from evennia_archive.mixins import ARCHIVE_ID_KEY, ArchivableMixin


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
