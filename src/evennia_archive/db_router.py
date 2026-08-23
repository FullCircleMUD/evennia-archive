# SPDX-License-Identifier: BSD-3-Clause
"""Database router for the archive.

Routes this library's own models to the ``archive`` database alias, and —
critically — stays out of the way of everything else, so Evennia's models
can be migrated into the same alias to form the schema clone.

Migrate with:  evennia migrate --database archive
"""


class ArchiveRouter:
    """Route evennia_archive models to the archive database."""

    # The app label and the alias are separate here, where sibling
    # routers use one name for both. The app is ``evennia_archive``; the
    # database alias a consumer declares is ``archive``. Conflating them
    # would route this library's models to an alias that does not exist.
    app_label = "evennia_archive"
    alias = "archive"

    # Sibling routers are exclusive: nothing but their own models may
    # enter their database. This one is not, and that is the whole point
    # — Evennia's models are exactly what belongs in the archive. Setting
    # this True would make the router refuse the thing the archive is for.
    exclusive = False

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.alias
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if (
            obj1._meta.app_label == self.app_label
            and obj2._meta.app_label == self.app_label
        ):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            # Our tables belong in the archive and nowhere else.
            return self.alias == db
        if self.exclusive and db == self.alias:
            return False
        # Returning None defers to the other routers, and is what lets
        # this coexist with a consumer's own routers rather than fight
        # them. It is also what allows Evennia's apps into the archive.
        return None
