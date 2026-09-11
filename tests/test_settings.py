# SPDX-License-Identifier: BSD-3-Clause
"""Minimal Django settings for evennia-archive unit tests.

Imports Evennia's defaults, adds the library to INSTALLED_APPS, and uses an
in-memory sqlite test database. No gamedir required.
"""
import os
import sys
import tempfile

import evennia

# Evennia 6.0.0+ ships migrations that import ``typeclasses.objects``
# (a gamedir module). Put Evennia's game_template on sys.path so the
# import resolves without requiring a real gamedir.
_game_template = os.path.join(os.path.dirname(evennia.__file__), "game_template")
if _game_template not in sys.path:
    sys.path.insert(0, _game_template)

from evennia.settings_default import *  # noqa: F401, F403, E402

# Evennia path bits — point at safe scratch locations so settings_default's
# path-derived defaults resolve without needing a real gamedir.
GAME_DIR = tempfile.gettempdir()
LOG_DIR = os.path.join(tempfile.gettempdir(), "evennia_archive_test_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Library under test, and the cascade that places its alias — including the
# cascade's app, so its boot check runs here the way it would in a consumer.
INSTALLED_APPS = list(INSTALLED_APPS) + [  # noqa: F405
    "evennia_archive",
    "evennia_database_cascade",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": "file:evennia_archive_test_default?mode=memory&cache=shared"},
    },
}

# The archive alias and its router come from the cascade, resolved from this
# library's own db_spec — the suite exercises the real consumer path on every
# run. The environment is {} rather than os.environ so the suite always lands
# on the SQLite rung, whatever DATABASE_URLs the machine carries.
from evennia_database_cascade import configure  # noqa: E402

DATABASES, DATABASE_ROUTERS = configure(DATABASES, INSTALLED_APPS, GAME_DIR, {})

# The TEST names are not decoration. Two aliases both saying ":memory:"
# look like one database to Django's test runner, which then treats the
# second as a mirror of the first — so the router would appear to work
# while both aliases pointed at the same physical database. Distinct
# shared-cache URIs keep them genuinely separate. Re-applied here because
# configure() resolves the alias to a real archive.db3 file, and the suite
# wants it in memory like the game database.
DATABASES["archive"]["NAME"] = ":memory:"
DATABASES["archive"]["TEST"] = {
    "NAME": "file:evennia_archive_test_archive?mode=memory&cache=shared"
}

# check_settings() runs at django.setup(), so the suite has to boot on a
# settings module the library accepts. Registered here as well as per-class,
# because the LF cases override LOCK_FUNC_MODULES inside BaseEvenniaTest and
# that override does not exist yet when ready() fires.
LOCK_FUNC_MODULES = list(LOCK_FUNC_MODULES) + ["evennia_archive.lockfuncs"]  # noqa: F405

# Required Django bits
SECRET_KEY = "test-only-secret"
TEST_ENVIRONMENT = True
ROOT_URLCONF = "tests.urls"
