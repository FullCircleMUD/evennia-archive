# SPDX-License-Identifier: BSD-3-Clause
"""The library's constants, and what it refuses to boot without.

Every module-level constant the library declares lives here and is imported
where it is needed, so a session about to declare one checks a single file
first and finds the existing name rather than minting a second for the same
value. See design/library-standards.md § Where constants are declared.

``check_settings()`` is called from ``AppConfig.ready()``. It collects every
problem and raises once: a consumer installing this has more than one entry to
declare, and stopping at the first turns that into fix-restart-fix-restart.
"""

# The Django database alias the archive lives under. A constant rather than a
# setting: it is always `archive`, a consumer declares a DATABASES entry under
# that key, and nothing reads the name back from them.
ARCHIVE_ALIAS = "archive"

# The Attribute key holding an object's archive identity. Changing this orphans
# every archived row in every existing install — it is the one name in this
# library that cannot be revised after release.
ARCHIVE_ID_KEY = "archive_id"

# The Attribute key naming the account a character belongs to, holding that
# account's archive_id. The archive drops db_account on the way in — it is a
# primary key, and those mean nothing in the other database — so this is the
# only link from a character back to its owner that survives a restore.
OWNER_ACCOUNT_KEY = "owner_account_archive_id"

# Where a restored object records a value it could not keep. The game can read
# it whenever it likes — at restore, or the next time the player logs in — and
# offer them a rename. Deleting it is the consumer's business.
RENAMED_FROM_KEY = "archive_renamed_from"

# A restore that cannot find a free value after this many tries is stuck rather
# than unlucky.
MAX_RENAME_ATTEMPTS = 1000

# Foreign keys into the live database. Dropped rather than copied — a primary
# key means nothing across two databases. Rebuilding the relationships they
# describe is the reference-translation work, which needs a disposition table
# that does not exist yet. See docs/design.md § Reference translation.
DROPPED_REFERENCES = {"db_location", "db_home", "db_destination", "db_account"}

# The settings a consumer declares. Named here so the checks and the messages
# cannot drift from each other.
SETTING_DATABASES = "DATABASES"
SETTING_LOCK_FUNC_MODULES = "LOCK_FUNC_MODULES"
LOCKFUNC_MODULE = "evennia_archive.lockfuncs"

# The fields that decide whether two DATABASES entries name the same database.
_IDENTITY_FIELDS = ("ENGINE", "NAME", "HOST", "PORT")


def _database_identity(entry):
    """What decides whether two DATABASES entries are one database.

    ``TEST["NAME"]`` is part of it. Under Django's test runner that is the
    database an alias actually uses, and two aliases can share a ``NAME`` of
    ``:memory:`` while pointing at genuinely separate test databases — which is
    exactly what this library's own suite does.
    """
    test = entry.get("TEST") or {}
    return tuple(entry.get(f) for f in _IDENTITY_FIELDS) + (test.get("NAME"),)


PROBLEM_PREFIX = "\n  - "


def check_settings() -> None:
    """Refuse to start when the consumer's settings are missing or unusable.

    Called from ``AppConfig.ready()``. Collects every problem and raises
    ``ImproperlyConfigured`` once, so a consumer gets the whole list rather
    than one restart per mistake.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    problems = []

    # getattr with a default rather than settings.NAME, so an undeclared
    # setting reaches the message that says what to add instead of raising
    # AttributeError from inside the check.
    databases = getattr(settings, SETTING_DATABASES, None) or {}
    archive = databases.get(ARCHIVE_ALIAS)
    if not archive:
        problems.append(
            f"{SETTING_DATABASES} has no {ARCHIVE_ALIAS!r} entry. The archive is a second "
            f"Evennia database on the same schema — declare it under that exact key, then "
            f"run `evennia migrate --database {ARCHIVE_ALIAS}`."
        )
    else:
        # A rejected value gets a harmless stand-in above, so this clause runs
        # either way rather than crashing on what the first one just refused.
        default = databases.get("default") or {}
        if _database_identity(archive) == _database_identity(default):
            problems.append(
                f"{SETTING_DATABASES}[{ARCHIVE_ALIAS!r}] names the same database as "
                f"'default'. Both carry Evennia's schema, so the archived rows would be the "
                f"live rows — and the world rebuild this library exists to survive would "
                f"take the archive with it."
            )

    lockfuncs = tuple(getattr(settings, SETTING_LOCK_FUNC_MODULES, None) or ())
    if LOCKFUNC_MODULE not in lockfuncs:
        problems.append(
            f"{SETTING_LOCK_FUNC_MODULES} does not include {LOCKFUNC_MODULE!r}. Without it "
            f"`owns_character()` cannot resolve, so every ownership check evaluates false "
            f"and an account is refused its own character — which reads as a permissions "
            f"problem rather than a missing registration."
        )

    if problems:
        raise ImproperlyConfigured(
            "evennia-archive cannot start:"
            + PROBLEM_PREFIX
            + PROBLEM_PREFIX.join(problems)
        )
