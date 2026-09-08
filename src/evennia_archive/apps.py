# SPDX-License-Identifier: BSD-3-Clause
"""Django AppConfig for evennia-archive.

Only loaded when the consumer adds ``evennia_archive`` to
``INSTALLED_APPS``.

``ready()`` runs the boot check. The consumer declares the router in their
own settings rather than the library appending it here: ``django.db.router.routers``
is a ``cached_property``, so anything touching the ORM before ``ready()``
snapshots the list without us in it. See docs/installing.md.
"""

from django.apps import AppConfig


class EvenniaArchiveConfig(AppConfig):
    name = "evennia_archive"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """Refuse to start on a settings module this library cannot work with."""
        from .config import check_settings

        check_settings()
