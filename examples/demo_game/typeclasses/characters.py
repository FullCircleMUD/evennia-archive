"""
Characters

Characters are (by default) Objects setup to be puppeted by Accounts.
They are what you "see" in game. The Character class in this module
is setup to be the "default" character type created by the default
creation commands.

"""

from evennia.objects.objects import DefaultCharacter

from .objects import ObjectParent


class Character(ObjectParent, DefaultCharacter):
    """
    The Character just re-implements some of the Object's methods and hooks
    to represent a Character entity in-game.

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Object child classes like this.

    """

    pass


# ---------------------------------------------------------------------
# evennia-archive demo
# ---------------------------------------------------------------------
from evennia.typeclasses.attributes import AttributeProperty

from evennia_archive.mixins import ArchivableMixin


class ArchivableCharacter(ArchivableMixin, DefaultCharacter):
    """A character the archive will accept.

    The mixin is the whole of what makes it archivable: it mints an
    archive_id when the character is created and never changes it. An
    object without the mixin is refused by archive(), which is how the
    library avoids guessing what matters.

    wallet_address is here for the demo rather than because the library
    needs it — it stands in for whatever a consumer game uses to
    recognise a returning player, and gives find() something to search
    on.

    strattr=True stores it unpickled, which is the recommended shape for
    anything you intend to search: a pickled comparison is type-sensitive
    and compares serialised bytes, where this is plain string equality.
    """

    wallet_address = AttributeProperty(None, strattr=True)
