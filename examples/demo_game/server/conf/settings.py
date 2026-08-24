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
# Copied verbatim from the library's docs/archive-settings.md. This
# gamedir exists to test that those instructions work as written, so if
# they need adjusting, adjust the document first and re-copy.

import os

# 1. The app
INSTALLED_APPS += ["evennia_archive"]

# Everything created in this demo should be archivable, so the base
# typeclasses are the ones carrying ArchivableMixin.
BASE_CHARACTER_TYPECLASS = "typeclasses.characters.ArchivableCharacter"
BASE_ACCOUNT_TYPECLASS = "typeclasses.accounts.ArchivableAccount"

# 2. The archive database — a second Evennia schema, never run as a game
DATABASES["archive"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.path.join(GAME_DIR, "server", "archive.db3"),
}

# 3. The router — append, never assign.
_ARCHIVE_ROUTER = "evennia_archive.db_router.ArchiveRouter"
DATABASE_ROUTERS = list(globals().get("DATABASE_ROUTERS", []))
if _ARCHIVE_ROUTER not in DATABASE_ROUTERS:
    DATABASE_ROUTERS.append(_ARCHIVE_ROUTER)


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")
