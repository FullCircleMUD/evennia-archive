r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
SERVERNAME = "demo_game"


######################################################################
# evennia-archive
######################################################################
# Copied verbatim from the library's docs/installing.md. This
# gamedir exists to test that those instructions work as written, so if
# they need adjusting, adjust the document first and re-copy.

import os

# 2. The apps — the library, and the cascade that places its database
INSTALLED_APPS += ["evennia_archive", "evennia_database_cascade"]

# Everything created in this demo should be archivable, so the base
# typeclasses are the ones carrying an archivable mixin.
BASE_CHARACTER_TYPECLASS = "typeclasses.characters.ArchivableCharacter"
BASE_ACCOUNT_TYPECLASS = "typeclasses.accounts.ArchivableAccount"

# 3. The archive database — resolved by the cascade from the library's own
# spec. With no DATABASE_URL_ARCHIVE set it lands in server/archive.db3;
# a second Evennia schema, never run as a game.
from evennia_database_cascade import configure

DATABASES, DATABASE_ROUTERS = configure(
    DATABASES, INSTALLED_APPS, GAME_DIR, os.environ
)

# 4. The lock function. ArchivableAccountMixin writes owns_character() into
# a character's ownership locks; without this the clause cannot resolve and
# every ownership check refuses.
LOCK_FUNC_MODULES = list(LOCK_FUNC_MODULES) + ["evennia_archive.lockfuncs"]


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")
