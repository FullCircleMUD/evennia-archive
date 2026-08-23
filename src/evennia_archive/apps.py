# SPDX-License-Identifier: BSD-3-Clause
"""Django AppConfig for evennia-archive.

Only loaded when the consumer adds ``evennia_archive`` to
``INSTALLED_APPS``.

Deliberately empty of a ``ready()`` hook for now. Whether the library
appends its own router here — the pattern evennia-shards uses for
middleware — or leaves the consumer to declare it is an open spike; see
docs/archive-settings.md.
"""

from django.apps import AppConfig


class EvenniaArchiveConfig(AppConfig):
    name = "evennia_archive"
    default_auto_field = "django.db.models.BigAutoField"
