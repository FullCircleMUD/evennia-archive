# SPDX-License-Identifier: BSD-3-Clause
"""Lock functions the library ships.

Register them the way Evennia registers any others::

    LOCK_FUNC_MODULES = list(LOCK_FUNC_MODULES) + ["evennia_archive.lockfuncs"]

Without that line a clause naming one of these cannot resolve and the check
is false — so a missing registration refuses everyone rather than admitting
them, which is the direction a mistake here should fail in.
"""


def _as_account(accessing_obj):
    """The account behind an accessor, or the accessor itself.

    Mirrors what Evennia's ``pid()`` does through its own helper: a
    puppeted character stands in for the account controlling it. Needed
    because ``edit`` and ``delete`` are checked with the character as
    accessor where ``puppet`` is checked with the account, and the owner
    should pass all three.
    """
    account = getattr(accessing_obj, "account", None)
    return account if account is not None else accessing_obj


def owns_character(accessing_obj, accessed_obj, *args, **kwargs):
    """True when the accessor is the account that owns ``accessed_obj``.

    Usage::

        puppet:owns_character() or perm(Developer) or pperm(Developer)

    Takes no arguments. The owner is read off the character's own stamp
    rather than written into the lockstring, so the lock and the stamp
    cannot come to name different accounts — which is the failure the
    primary-key version had, in slower motion.

    Evennia's ``pid()`` cannot do this job. It compares primary keys, and
    a restore mints new ones for both the account and the character, so a
    lock written at creation stops matching the moment either is rebuilt.
    An ``archive_id`` is minted once and never reissued.

    Strict, and false whenever it cannot say yes:

    - a character with no owner is nobody's, so nobody owns it
    - an accessor with no identity cannot match one

    Both matter because the two absences would otherwise compare equal,
    and every unowned character would be open to every account. Developers
    and superusers still get in — on their own clauses in the lockstring,
    and on Evennia's superuser bypass, neither of which is this function's
    business.
    """
    owner = getattr(accessed_obj, "owner_account_archive_id", None)
    if not owner:
        return False

    identity = getattr(_as_account(accessing_obj), "archive_id", None)
    if not identity:
        return False

    return identity == owner
