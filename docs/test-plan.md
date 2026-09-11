# Test plan

Every test case the library commits to covering, and the test function that covers it. The library is
built test-first: cases are agreed here, tests are written against them, then the implementation is
written to pass. The **Test function** column is the auditable trail — it is filled in as each test is
written, so an empty cell means the case is agreed but not yet covered.

Case IDs are stable and referenceable. Do not renumber; retire an ID rather than reuse it. Every test
function carries its case ID as its docstring, so the trail reads in both directions.

All test functions live in `src/evennia_archive/tests.py`.

| Prefix | Covers |
|---|---|
| `SM` | Smoke — the package installs and Django loads it |
| `LG` | Logging |
| `ID` | `ArchivableBaseMixin` — minting and storing the identity |
| `OM` | `ArchivableObjectMixin` — the kind-specific mixin for objects |
| `AM` | `ArchivableAccountMixin` — the kind-specific mixin for accounts |
| `CM` | `ArchivableCharacterMixin` — the kind-specific mixin for characters |
| `AR` | `archive()` |
| `RS` | `restore()` |
| `AC` | The same round trip on `AccountDB` rather than `ObjectDB` |
| `FN` | `find_by_attribute()` |
| `FC` | `find_by_column()` |
| `DL` | `delete()` |
| `UC` | Restore into a taken unique value |
| `PG` | `_purge_attributes()` — clearing one row's attributes in one database |
| `CP` | `_copy_attributes()` — moving one row's attributes between the two databases |
| `LF` | `lockfuncs` — the lock functions the library ships |
| `CS` | `check_settings()` — what the library refuses to boot without |
| `CT` | `config.py` — the constants every other module imports |
| `LO` | What the library logs — the call sites, where `LG` covers the binding |

## Fixtures

| Fixture | Purpose |
|---|---|
| `databases = {"default", "archive"}` on every case class touching the archive | Django only builds test databases for aliases a class declares. Without it the archive alias is never created and every query against it raises `DatabaseOperationForbidden` |
| Distinct `TEST["NAME"]` shared-cache URIs in `tests/test_settings.py` | Two aliases both saying `:memory:` look like one database to Django's runner, which then treats the second as a mirror of the first. The router would appear to work while both pointed at the same file — so a copy landing in the wrong alias would pass |
| `override_settings(LOCK_FUNC_MODULES=...)` plus a `_cache_lockfuncs()` rebuild, on `LF`'s case class | Evennia's `BaseEvenniaTest` applies its own `override_settings` that **replaces** `LOCK_FUNC_MODULES` outright, so a module registered in `tests/test_settings.py` is invisible inside it. And `_LOCKFUNCS` is a process-wide cache built once on the first `LockHandler`, so overriding the setting changes nothing until the cache is rebuilt under it. Both halves are needed; either alone silently leaves the function unregistered |
| `ArchivableTestObject` / `ArchivableTestAccount` / `ArchivableTestCharacter` | Minimal typeclasses carrying the matching kind-specific mixin. All three are needed: an account has a different creation hook, a unique username and Django's `PermissionsMixin` bolted on, and a character is the only kind an account stamps |
| `override_settings(DATABASES=..., LOCK_FUNC_MODULES=...)` on `CS`'s case class | `check_settings()` reads its settings when called, so each case overrides the one entry it is about and leaves the rest valid. A case that broke two at once could not tell which produced the message |

## `check_settings()`

What the library refuses to start without. Every problem is collected and raised together: a consumer
installing this has more than one thing to declare, and stopping at the first turns that into
fix-restart-fix-restart, once per entry.

Two entries are refusals, and the reasoning differs:

- **The `archive` alias** — without it every call raises `ConnectionDoesNotExist` at first use, which
  is the deferred failure boot checking exists to prevent.
- **`LOCK_FUNC_MODULES`** — without `evennia_archive.lockfuncs` the `owns_character()` clause cannot
  resolve, so every ownership check evaluates false and refuses the owning account its own character.
  It fails in the safe direction and looks exactly like a permissions bug.

`CS-03` is the one that is not obvious. An `archive` alias pointing at the same database as `default`
passes every other check and destroys the guarantee the library exists for: the archive tables *are*
the live tables, so the rebuild it is meant to survive takes it with it. It is a dict comparison, no
query.

`CS-08` is retired. A missing router is **not checked**. It does not stop the library working — every
query names the alias explicitly — so refusing to start over it would be disproportionate, and a
warning nobody reads is not worth the call site. Its absence leaves a stray
`evennia_archive_archiverecord` in the game database and nothing else. Stated in `installing.md`
under *what is not checked for you*. Do not reuse the ID.

Not checked, and stated in `installing.md` under *what is not checked for you*: `INSTALLED_APPS`,
because `AppConfig.ready()` never runs without it, and whether the archive has been migrated, because
that needs a database query inside `ready()`.

| ID | Case | Test function |
|---|---|---|
| CS-01 | A complete settings module passes — `check_settings()` returns without raising | `TestCheckSettings.test_complete_settings_pass` |
| CS-02 | No `archive` entry in `DATABASES` raises `ImproperlyConfigured`, and the message names the key to add | `TestCheckSettings.test_missing_archive_alias_raises` |
| CS-03 | An `archive` entry naming the same database as `default` raises. Same engine and same name means the archive tables are the live tables, and the rebuild the library exists to survive destroys it | `TestCheckSettings.test_archive_sharing_the_game_database_raises` |
| CS-04 | `LOCK_FUNC_MODULES` without `evennia_archive.lockfuncs` raises, and the message says ownership checks refuse everyone without it | `TestCheckSettings.test_missing_lockfunc_module_raises` |
| CS-05 | Two faults produce one exception naming both. A consumer gets the whole list or a clean start, never one restart per mistake | `TestCheckSettings.test_every_problem_in_one_raise` |
| CS-06 | A `DATABASES` that is missing entirely does not mask the lock-function check — the clause that follows a rejected value still runs and still reports | `TestCheckSettings.test_a_rejected_value_does_not_stop_later_clauses` |
| CS-07 | `AppConfig.ready()` calls `check_settings()`, so the refusal happens at boot rather than at first use | `TestCheckSettings.test_ready_calls_check_settings` |

## `config.py`

The library's constants, in one file, imported wherever they are needed. `ARCHIVE_ALIAS` is a constant
rather than a setting: the alias is always `archive`, a consumer declares a `DATABASES` entry under
that key, and nothing reads the name back from them.

| ID | Case | Test function |
|---|---|---|
| CT-01 | `ArchiveRouter.alias` is `config.ARCHIVE_ALIAS`, not a second literal. The two were independent strings holding `"archive"`, with nothing to say they were the same thing | `TestConfigConstants.test_router_uses_the_config_alias` |
| CT-02 | `api` routes through `config.ARCHIVE_ALIAS` — the archived copy lands under the alias the router steers | `TestConfigConstants.test_api_routes_through_the_config_alias` |
| CT-03 | The attribute keys `ARCHIVE_ID_KEY` and `OWNER_ACCOUNT_KEY` resolve to the same values the mixins store, so moving them broke no stored identity | `TestConfigConstants.test_attribute_keys_are_unchanged` |

## What the library logs

`LG` covers the binding; this covers the call sites. A line earns its place where something happens that
nobody would otherwise see — so the negative cases below are as load-bearing as the positive ones. A
log an operator has learned to scroll past is worse than no log, and the ordinary operations here run
at every character creation and every scheduled sweep.

Four sites, all silent today:

- **`archive()` rewriting a record whose row is gone.** A self-heal. It costs nothing and turns a hard
  failure into a repair, which is why it is right — and why nothing currently says it happened.
- **`restore()` renaming a taken unique value.** A player's name changed and only that player knows.
  Somebody will ask why their character is `rowan2` long after the restore.
- **`validate_username` refusing a name the archive holds.** An ordinary username collision is
  discoverable: an admin looks in `accountdb` and sees it. This one is not — the account is not there,
  and without a line the answer to "why was that character not created" exists nowhere.
- **Account `#1` skipped at creation.** Deliberate and silent, and the first thing to look for on
  finding that `root` has no archived copy.

Successful `archive()` calls are deliberately not logged: the hook fires at every character creation,
and the volume would bury the four above.

| ID | Case | Test function |
|---|---|---|
| `LO-01` | `archive()` rewriting a record whose archived row is gone logs a `WARN` naming the identity | `TestArchiveLogging.test_self_heal_logs_a_warning` |
| `LO-02` | An ordinary archive logs nothing. The line marks the repair, not the operation | `TestArchiveLogging.test_an_ordinary_archive_logs_nothing` |
| `LO-03` | A restore that renames logs a `WARN` naming the value that was taken and the one used instead, alongside the `INFO` from `LO-09` | `TestArchiveLogging.test_a_rename_logs_a_warning` |
| `LO-04` | A restore that needed no rename logs no `WARN`. The restore line still fires — what must not is the rename line | `TestArchiveLogging.test_a_restore_without_a_rename_logs_nothing` |
| `LO-05` | `validate_username` refusing an archive-held name logs an `INFO` naming it | `TestArchiveLogging.test_an_archive_held_username_logs_an_info` |
| `LO-06` | A username free in both databases logs nothing | `TestArchiveLogging.test_a_free_username_logs_nothing` |
| `LO-07` | Skipping account `#1` at creation logs an `INFO` saying why it is exempt | `TestArchiveLogging.test_skipping_account_one_logs_an_info` |
| `LO-08` | Creating any other account logs nothing | `TestArchiveLogging.test_creating_any_other_account_logs_nothing` |
| `LO-09` | A successful `restore()` logs an `INFO` naming the identity and the primary key it came back under | `TestArchiveLogging.test_a_successful_restore_logs_an_info` |
| `LO-10` | A restore that returned an object already live logs nothing — nothing was rebuilt | `TestArchiveLogging.test_an_idempotent_restore_logs_nothing` |
| `LO-11` | A successful `delete()` logs an `INFO` naming the identity it destroyed | `TestArchiveLogging.test_a_successful_delete_logs_an_info` |
| `LO-12` | `delete()` finding nothing to remove logs nothing. That is its documented normal case, and the reason the other path is worth a line | `TestArchiveLogging.test_deleting_nothing_logs_nothing` |

`LO-09` and `LO-11` are the two operations worth a record beyond the four above.

**A restore is the audit question people ask** — which characters came back after the rebuild, and
when. The primary key belongs in the line because it is the one fact nobody can reconstruct
afterwards: identity survives a restore, the dbref does not. It also pairs with `LO-03`, which
otherwise logs a rename with no record of the restore that caused it.

**`delete()` is the library's only irreversible operation.** A hard delete rather than a flag, so
that a soft-deleted row cannot resurrect — which means once it is gone the archived copy cannot be
recovered. Its quiet path is the common one, so a line only appears when something was destroyed.

`[TBD — needs discussion: a bulk restore call is listed in design.md under "not yet examined at all".
If it lands, `LO-09` becomes a line per object and needs a summary form instead.]`

## Smoke

| ID | Case | Test function |
|---|---|---|
| `SM-01` | The package exposes `__version__` | `test_version_is_exposed` |
| `SM-02` | Django loads `evennia_archive` as an installed app | `test_registered_in_installed_apps` |

## Logging

The library logs to a file of its own rather than into the main server log. The mechanism —
delivery, levels, trace handling — belongs to `evennia-logging-extension` and is tested there;
`log.py` here only binds `archive_log` to `archive.log`, and one case proves the binding. What the
library logs, and from where, is the `LO` section.

| ID | Case | Test function |
|---|---|---|
| `LG-01` | The shim binds through `make_logger` and a call returns `None` without raising | `test_the_log_shim_binds_and_a_call_returns_none` |

## Identity — `ArchivableBaseMixin`

The parent of the three kind-specific mixins. It owns the identity — `archive_id` and
`at_archive_init()` — and nothing else. Its creation hooks exist only to refuse, so a consumer who
mixes the base in directly finds out at creation rather than at the first archive.

`ID-01` is retired. It covered a creation hook, which is `ArchivableObjectMixin`'s — see `OM-01`.

| ID | Case | Test function |
|---|---|---|
| `ID-02` | The minted value is a canonical UUID — round-tripping it through `uuid.UUID` and back is a no-op, which is what makes plain string equality a safe lookup | `test_minted_id_is_a_canonical_uuid` |
| `ID-03` | `at_archive_init()` never overwrites: calling it again returns the existing identity unchanged | `test_init_is_idempotent` |
| `ID-04` | Two objects mint different identities | `test_ids_are_unique_across_objects` |
| `ID-05` | The identity is stored unpickled — `db_strvalue` set, `db_value` null. Flipping this would make every lookup a byte comparison whose stability depends on the pickle protocol, and would silently orphan existing installs | `test_stored_unpickled_in_strvalue` |
| `ID-06` | An object without the mixin has no identity, and is therefore not archivable | `test_object_without_the_mixin_has_no_identity` |
| `ID-07` | `at_object_creation` on the base raises `NotImplementedError`, naming the kind-specific mixins | `test_base_refuses_object_creation` |
| `ID-08` | `at_account_creation` on the base raises the same way | `test_base_refuses_account_creation` |
| `ID-09` | `at_post_create_character` on the base raises the same way. Only `ArchivableAccountMixin` implements it, so reaching the base's version means an account was declared with the wrong mixin | `test_base_refuses_to_stamp_a_character` |

`ID-07` to `ID-09` are the guard. A true abstract base is not available — Evennia's typeclasses carry
the `TypeclassBase` metaclass, and adding `ABCMeta` to that raises a metaclass conflict at class
definition, while `@abstractmethod` without `ABCMeta` enforces nothing. Refusing from the hooks is
what is left, and it fires at the moment the mistake is made.

The cost is that a subclass implementing a creation hook cannot call plain `super()` — that resolves
to the base's refusal, because the mixin chain precedes Evennia's class in the MRO. It must skip the
base with `super(ArchivableBaseMixin, self)`. `OM-01` and `AM-01` are the worked examples, and they
fail loudly if anyone writes the plain form.

## `ArchivableObjectMixin`

The kind-specific mixin for anything descending from `ObjectDB` — and the parent of
`ArchivableCharacterMixin`, since a Character is an Object and mints its identity the same way.

| ID | Case | Test function |
|---|---|---|
| `OM-01` | Creating an object carrying the mixin mints an `archive_id` | `TestArchivableObjectMixin.test_creation_mints_an_id` |
| `OM-02` | An object carrying `ArchivableObjectMixin` is archivable — `_identity_of` tests the base, so every child qualifies | `test_an_object_mixin_object_is_archivable` |
| `OM-03` | A consumer typeclass overriding `at_object_creation` and calling plain `super()` still mints. That is the documented usage, and it works because the consumer's `super()` lands on this mixin rather than the base — the grandparent rule binds only on children of the base | `test_a_consumer_override_calling_plain_super_still_mints` |
| `OM-04` | A mixin sitting between this one and Evennia still gets its `at_object_creation` called. `super(ArchivableBaseMixin, self)` has to skip exactly one class, not everything up to Evennia | `test_a_mixin_below_ours_still_gets_its_hook` |

`OM-04` is the only case that catches a grandparent call skipping too far. `OM-01` proves the base's
refusal is skipped; it cannot tell whether anything else was skipped with it.

## `ArchivableAccountMixin`

Accounts mint through a different hook, and they are the only kind with a second job: an account
knows which characters are its own, and it is the only object holding that knowledge at the moment a
character is created.

| ID | Case | Test function |
|---|---|---|
| `AM-01` | Creating an account carrying the mixin mints an `archive_id`, via `at_account_creation` | `test_account_creation_mints_an_id` |
| `AM-02` | An account carrying the mixin is archivable | `test_an_account_mixin_account_is_archivable` |
| `AM-03` | `at_post_create_character` stamps the character with the account's `archive_id` | `test_stamps_the_character_with_its_owner` |
| `AM-04` | The stamp is stored unpickled, so `find_by_attribute()` matches it by string equality rather than by pickled bytes | `test_the_stamp_is_stored_unpickled` |
| `AM-05` | The stamp is never overwritten — an account creating a character that already carries one leaves it alone | `test_the_stamp_is_never_overwritten` |
| `AM-06` | A character typeclass that does not carry `ArchivableCharacterMixin` is left unstamped, and creating one does not raise. Not every Character in a game is a player's — and an object-mixin character is archivable but has nowhere to read the stamp back from | `test_a_character_without_the_mixin_is_left_alone` |
| `AM-07` | Evennia's own `at_post_create_character` still runs: the character joins `_playable_characters`, and `_last_puppet` is set for the first one | `test_evennias_own_hook_still_runs` |
| `AM-08` | The character's `puppet` lock is `owns_character() or perm(Developer) or pperm(Developer)` | `test_the_puppet_lock_uses_the_lockfunc` |
| `AM-09` | Its `edit` and `delete` locks are `owns_character() or perm(Admin)` | `test_the_edit_and_delete_locks_use_the_lockfunc` |
| `AM-10` | No `id()` or `pid()` clause survives on any of the three. A leftover one grants nothing and goes stale silently | `test_no_primary_key_clause_survives` |
| `AM-11` | The permission clauses survive — a Developer can still puppet and an Admin can still edit and delete. Replacing the whole lockstring rather than the three access types would take an operator's way in with it | `test_the_permission_clauses_survive` |
| `AM-12` | Access types the rewrite does not name come through unchanged. `control`, `view`, `tell` and the rest keep what Evennia wrote — the hook replaces three clauses, not the lockstring | `test_unnamed_access_types_survive` |
| `AM-13` | Two characters created by the same account carry the same stamp. It is the account's identity rather than anything derived from the character, which is what lets a consumer find a whole roster by one value | `test_two_characters_share_the_owner_stamp` |
| `AM-14` | Creating a character archives it, so a stamped character always has a row behind it | `test_a_new_character_is_archived` |
| `AM-15` | The archived copy carries the owner stamp and the rewritten locks — it is stored after both, not before | `test_the_archived_copy_carries_the_stamp_and_locks` |
| `AM-16` | A character without `ArchivableCharacterMixin` is not archived | `test_a_character_without_the_mixin_is_not_archived` |
| `AM-17` | Creating an account archives it, so a minted identity always has a row behind it | `test_a_new_account_is_archived` |
| `AM-18` | A username the archive already holds is refused, with an error saying so | `test_a_username_held_in_the_archive_is_refused` |
| `AM-19` | A username free in both the live database and the archive is accepted | `test_a_free_username_is_accepted` |
| `AM-20` | Evennia's own refusal stands, with its own errors — the archive is not consulted instead of the local check, but after it | `test_evennias_own_refusal_stands` |
| `AM-21` | A different case of an archived username is refused too. Evennia authenticates case-insensitively, so `Rowan` and `rowan` are one account to it | `test_an_archived_username_is_refused_whatever_the_case` |
| `AM-22` | Account `#1` is not archived at creation | `test_account_one_is_not_archived_at_creation` |
| `AM-23` | Another superuser is archived like any other account — being a superuser is not the reason `#1` is skipped | `test_another_superuser_is_archived` |

`AM-08` to `AM-12` are why this mixin exists rather than a plain identity stamp. Evennia writes a
character's `puppet`, `edit` and `delete` locks at creation with the account's and the character's
primary keys as literals — `puppet:id(3) or pid(2) or perm(Developer) or pperm(Developer)`. Both keys
change on every restore, so the locks name objects that no longer exist and the owning account is
refused the character it owns, with nothing in any log.

`owns_character()` is the replacement — see the `LF` cases. It reads the owner off the character's
stamp rather than carrying a value in the lockstring, so the lock and the stamp cannot come to name
different accounts.

`[TBD — needs discussion: what an account with no `archive_id` should do when it creates a
character. `at_post_create_character` calls `at_archive_init()`, which mints one. That is right for
the case the mixin was written for — a game installing this library on an existing world, where no
account has an identity yet. It is wrong for an account that was archived and then had its identity
removed, which is an error condition: minting a second identity orphans the archived copy and every
character already stamped with the old value, silently. Nothing distinguishes the two, and there is
no case either way.]`

**`AM-22` keeps `#1` out of it, and `AM-23` says why that is the rule rather than "no superusers".**
Evennia requires account `#1` on every instance and creates one called `root` at first boot. It belongs
to the instance it was made on: restoring it anywhere would displace that instance's own, so there is
nothing an archived copy could be used for.

Without the skip it is worse than useless. A second instance sharing an archive collides on `username`
— and an initial-setup failure stops the Server *and* the Portal, taking every other instance behind
that Portal down with it. Found by booting a second instance in `evennia-scaling`'s demo.

**Being a superuser is not the reason.** A second superuser account is an ordinary account as far as
this library is concerned: it is not what Evennia demands be present, its name collides with nothing,
and a consumer may well want one that can be archived and restored like any other. `#1` is the
constraint; superuser-ness was standing in for it.

**`AM-18` keeps a departed player's name.** The archive is a clone of Evennia's schema, so it carries
Evennia's `UNIQUE` on `username`. An account archived and then deleted leaves that name held in the
archive while it is free in the live database — so the next person to take it cannot be archived, and
with `AM-17` that failure lands on registration.

Refusing the name is the better answer than renaming on the way in, which is what `restore()` does on
the way out. A player who leaves keeps their name for as long as the archive holds them, and the person
being turned away has not lost anything yet.

`validate_username` is the seam: a classmethod whose whole job is already "is this name usable",
running before the account exists, and one a consumer writing bespoke chargen keeps.

**`AM-14` is the one place this library archives something without being asked.** Everywhere else a
consumer calls `archive()` when they decide something is worth storing. Here, creating a character
stores it.

The reason is that the hook already mints an `archive_id` for the character, and an id with no row
behind it is a half-state: it names an archive entry that does not exist, and `restore()` on it raises.
Storing at creation makes **a stamped character always has a row** true from the first moment, which is
the invariant a consumer reading that stamp is entitled to assume.

Adding `ArchivableCharacterMixin` is the opt-in. The hook checks for it before doing anything, so a
game that has not asked for archivable characters gets no rows.

It takes nothing away: `archive()` is unchanged, and a consumer who archives at the end of chargen
simply overwrites this copy with a better one.

`AM-08` and `AM-09` prove the named clause is replaced outright rather than merged — the stale
`id(3) or pid(2)` would still be there otherwise. `AM-12` proves the other direction: replacing three
access types does not disturb the other eleven. `AM-10` catches a rewrite that removes too little,
`AM-11` one that removes too much.

## `ArchivableCharacterMixin`

For player characters — the ones an account creates and owns. Not for NPCs or mobs, which most
games type as Character subclasses to inherit combat and movement and which have no owner; those
declare `ArchivableObjectMixin` if their state is worth archiving.

A Character is an Object, so this extends `ArchivableObjectMixin` and mints through the same hook.
What it adds is the owner stamp — the only link back to an account that survives a restore, since
`db_account` is a primary key and the archive drops it.

| ID | Case | Test function |
|---|---|---|
| `CM-01` | Creating a character carrying the mixin mints an `archive_id`, inherited from the object mixin | `TestArchivableCharacterMixin.test_creation_mints_an_id` |
| `CM-02` | `owner_account_archive_id` returns the owner account's identity | `test_owner_accessor_returns_the_stamp` |
| `CM-03` | A character created by code or a builder rather than through an account has no owner, and reading `owner_account_archive_id` returns `None` rather than raising. Reading is not archiving — `AR-11` is where the misdeclaration is refused | `test_a_character_with_no_owner_reads_as_none` |
| `CM-04` | A character carrying the mixin and naming an owner account is archivable | `test_a_character_mixin_character_is_archivable` |

## Lock functions

The library ships one lock function, in `evennia_archive/lockfuncs.py`. A consumer registers it with
`LOCK_FUNC_MODULES += ["evennia_archive.lockfuncs"]`; without that the clause cannot resolve and every
check using it is false, which refuses everyone rather than admitting them.

`owns_character()` answers one question — is the accessor the account that owns this character. It
reads the owner off the character's stamp rather than taking it as an argument, so the lock and the
stamp cannot drift apart. Evennia's own `pid()` cannot be used: the identity has to survive a restore,
and a primary key does not.

| ID | Case | Test function |
|---|---|---|
| `LF-01` | The owning account is granted — its `archive_id` matches the character's `owner_account_archive_id` | `test_the_owning_account_is_granted` |
| `LF-02` | A different account is refused | `test_another_account_is_refused` |
| `LF-03` | A character with no owner: the function returns false for every accessor, including one that also has no identity. Two absent values must not compare equal and let the world in. Developers and superusers still get in, on their own clauses and on Evennia's bypass — that is the lockstring's business, not this function's | `test_a_character_with_no_owner_refuses_everyone` |
| `LF-04` | A puppeted character as accessor resolves to its controlling account and is granted, as `pid()` does. `edit` and `delete` are checked with the character as accessor, so without this the owner would be refused | `test_a_puppeted_character_resolves_to_its_account` |
| `LF-05` | An unpuppeted character as accessor is refused — it falls back to itself, and its own identity is not its owner's | `test_an_unpuppeted_character_is_refused` |
| `LF-06` | Evaluated through a real lockstring: `character.access(account, "puppet")` is true with `puppet:owns_character()` on it | `test_resolves_out_of_a_real_lockstring` |

`LF-03` is the one that matters. Every other case fails in the safe direction if it is written wrong;
that one, written carelessly, grants every account access to every unowned character.

`LF-06` is the only case that proves it works as a lock function rather than as a function — parsed
out of a string, resolved through the registry, and called with the arguments Evennia chooses.

## `archive()`

| ID | Case | Test function |
|---|---|---|
| `AR-01` | An object whose typeclass carries none of the archivable mixins raises `NotArchivable` | `test_refuses_an_object_without_identity` |
| `AR-02` | A copy lands in the archive, and the record names the model and the key it landed under | `test_creates_a_copy_in_the_archive` |
| `AR-03` | The copy does not land in the live database — the failure the router exists to prevent, and one that would otherwise look like success | `TestArchive.test_copy_does_not_land_in_the_live_database` |
| `AR-04` | Attributes come across, pickled values included | `test_attributes_come_across` |
| `AR-05` | The identity comes across in `db_strvalue` | `test_identity_comes_across` |
| `AR-06` | A second archive updates the existing copy rather than duplicating it, and the record still points at the same row | `test_second_archive_updates_rather_than_duplicates` |
| `AR-07` | An attribute removed from the live object is removed from the copy | `test_removed_attributes_are_removed_from_the_copy` |
| `AR-08` | The location reference is dropped on the way in | `test_location_reference_is_dropped` |
| `AR-09` | An object exposing `archive_id` without carrying one of the archivable mixins is refused — the attribute is not the contract | `test_refuses_a_hand_rolled_archive_id` |
| `AR-10` | An object carrying an archivable mixin but never initialised raises `NotArchivable`, naming `at_archive_init()` | `test_refuses_a_mixin_object_never_initialised` |
| `AR-11` | An object carrying `ArchivableCharacterMixin` with no owner account raises `NotArchivable`, naming `ArchivableObjectMixin`. The mixin declares that an account owns the object, and an account stamps every character it creates — so no stamp means no account created it, and the declaration is wrong | `test_refuses_a_character_with_no_owner` |
| `AR-12` | The returned record's `archive_id` is a string whether the copy was created or updated, so it compares equal to the object's own | `test_the_returned_identity_is_a_string` |

`AR-12` keeps the column and the API apart. `ArchiveRecord.archive_id` is a `UUIDField`, which is the
right storage type — on Postgres it is 16 bytes against 36 for the text form, on a primary key
everything joins through. But a record loaded from the database reads that back as a `uuid.UUID`, while
one just created still holds the string it was given, so the same call returned two types depending on
whether a copy already existed. Everything else in this library speaks strings: the mixins mint and
store them, and both finders coerce. So the string is put back on the record before it is returned.

`AR-09` is the case that makes the contract the mixin rather than the attribute, and it exists because
the looser check is unsafe rather than merely untidy. `restore()` finds a live row **by `archive_id`**
(`_live_pk_for`), so two objects sharing a hand-minted value means a restore returns the wrong object —
no exception, nothing in a log. The mixin is what guarantees a uuid4 that is minted once and never
reissued; an arbitrary attribute guarantees neither, and the library has no way to check after the
fact. `AR-10` is the other half: the mixin present but its identity never minted, which is what an
object created before the mixin was added looks like.

## `restore()`

| ID | Case | Test function |
|---|---|---|
| `RS-01` | An unknown identity raises `NotArchived` | `test_refuses_an_unknown_identity` |
| `RS-02` | The round trip closes: key and attributes come back after the live object is deleted | `test_round_trip_restores_the_object` |
| `RS-03` | The identity survives the round trip | `test_identity_survives_the_round_trip` |
| `RS-04` | Tags survive the round trip | `test_tags_survive_the_round_trip` |
| `RS-05` | The restored object has a different primary key — identity survives, dbrefs do not | `test_restored_object_has_a_new_primary_key` |
| `RS-06` | The restored object comes back stripped of every dbref it held — location and home are null | `test_restored_object_comes_back_stripped_of_dbrefs` |
| `RS-07` | Restoring twice returns the same object rather than duplicating it | `test_restoring_twice_does_not_duplicate` |
| `RS-08` | `return_object=False` yields the primary key instead of the instance | `test_return_object_false_yields_a_key` |
| `RS-09` | A restore stamps `last_restored`, which was null before | `test_restore_stamps_last_restored` |

## Accounts

The same round trip on `AccountDB` rather than `ObjectDB`.

`AC-01` is retired. It covered a creation hook, which is `ArchivableAccountMixin`'s — see `AM-01`.

| ID | Case | Test function |
|---|---|---|
| `AC-02` | The account round trip closes: username, email, attributes, tags and identity all come back | `test_round_trip_restores_the_account` |
| `AC-03` | The record names `accountdb` as the archived model | `test_record_names_the_account_model` |
| `AC-04` | The account copy does not land in the live database | `TestAccountRoundTrip.test_copy_does_not_land_in_the_live_database` |
| `AC-05` | The restored account has a different primary key | `test_restored_account_has_a_new_primary_key` |

## `find_by_attribute()`

| ID | Case | Test function |
|---|---|---|
| `FN-01` | An empty archive yields an empty list rather than raising | `test_finds_nothing_in_an_empty_archive` |
| `FN-02` | An unpickled attribute is found — the `db_strvalue` half of the query | `test_finds_by_unpickled_attribute` |
| `FN-03` | A pickled attribute is found — the `db_value` half | `test_finds_by_pickled_attribute` |
| `FN-04` | A pickled match is type-sensitive: the same logical value of a different type does not match. Documented behaviour, not a defect | `test_pickled_match_is_type_sensitive` |
| `FN-05` | Every match is returned, because the library cannot know whether a consumer's field is unique | `test_returns_every_match` |
| `FN-06` | The key and the value must be the same attribute — chaining two filters would match an object holding the key on one attribute and the value on another | `test_key_and_value_must_be_the_same_attribute` |
| `FN-07` | An unnarrowed search covers accounts and objects together | `test_searches_accounts_and_objects_together` |
| `FN-08` | `model` narrows the search to one archived model | `test_model_narrows_the_search` |
| `FN-09` | An unpickled attribute matches whatever its case — the default | `test_an_unpickled_match_ignores_case` |
| `FN-10` | `case_insensitive=False` matches exactly, and a different case does not match | `test_case_can_be_required` |
| `FN-11` | A pickled value is matched exactly whatever the flag says — there is no case in a pickled byte string | `test_a_pickled_match_is_unaffected_by_the_flag` |

## `find_by_column()`

Searches a real column on the archived model rather than a row in the Attribute table, so a consumer
can look an account up by `username` without duplicating it into an attribute. Returns the same thing
`find_by_attribute()` does — the archive identifiers of the matching rows, ready for `restore()`.

`model` is required here rather than optional, because columns are not shared the way attribute keys
are: `username` exists on `accountdb` and nowhere else, so an unnarrowed search would have to either
raise on the models that lack the column or swallow the miss silently.

| ID | Case | Test function |
|---|---|---|
| `FC-01` | A name that names no model raises rather than returning an empty list. Distinct from a real model the archive happens to hold nothing of, which is `FC-05` | `test_unknown_model_raises` |
| `FC-02` | A model given as the class behaves the same as the string, matching `find_by_attribute()` | `test_a_model_class_behaves_like_its_name` |
| `FC-03` | A column the model does not have raises — `username` against `objectdb`. This is the case that made `model` required rather than optional | `test_a_column_the_model_lacks_raises` |
| `FC-04` | A relation is not a column: `db_attributes` is refused rather than filtered on. `_meta.get_field()` answers for it, so the check has to be against concrete fields | `test_a_relation_is_not_a_column` |
| `FC-05` | An empty archive yields an empty list rather than raising | `TestFindByColumn.test_finds_nothing_in_an_empty_archive` |
| `FC-06` | An account is found by `username`, and the identifier returned restores it. The motivating case, end to end | `test_finds_an_account_by_username_and_restores_it` |
| `FC-07` | Every match is returned — `db_key` is not unique, so one value can hit several rows | `TestFindByColumn.test_returns_every_match` |
| `FC-08` | The search runs against the archive alias, proved by letting the two copies diverge: the archived username is found and the live one is not. `username` is a column on the live table too, so an alias leak answers from the live row. Same class of defect as `PG-05` | `test_searches_the_archive_not_the_live_database` |
| `FC-09` | A column comparison is not type-sensitive, because Django coerces the term to the field's type. The opposite of `FN-04`, and worth pinning so the pickled behaviour is not assumed to carry over | `test_a_column_match_is_not_type_sensitive` |
| `FC-10` | A text column matches whatever the case — the default | `test_a_text_column_ignores_case` |
| `FC-11` | `case_insensitive=False` matches a text column exactly | `test_case_can_be_required_on_a_column` |
| `FC-12` | A non-text column is matched exactly whatever the flag says. `iexact` on a boolean or an integer is meaningless, and asking for it must not break a search that works | `test_a_non_text_column_is_unaffected_by_the_flag` |

## `delete()`

| ID | Case | Test function |
|---|---|---|
| `DL-01` | An unknown identity is quiet and returns false. The natural caller is a delete hook, which fires for objects that were never archived | `test_unknown_identity_is_quiet` |
| `DL-02` | The archived copy and its record are both removed | `test_removes_the_copy_and_the_record` |
| `DL-03` | The archived attributes go with it | `test_removes_the_archived_attributes` |
| `DL-04` | The live object is untouched — nothing in this library ever deletes a live object | `test_leaves_the_live_object_alone` |
| `DL-05` | A deleted identity can no longer be restored | `test_deleted_identity_can_no_longer_be_restored` |
| `DL-06` | Deleting twice is idempotent: true then false | `test_is_idempotent` |
| `DL-07` | A tag shared with another archived object survives that object's delete | `test_shared_tags_survive_a_delete` |

## Restore into a taken unique value

| ID | Case | Test function |
|---|---|---|
| `UC-01` | An account whose username was taken while it was away restores under a numbered name, keeping its identity | `test_restores_under_a_numbered_name` |
| `UC-02` | State survives the rename — the name is the recoverable part, the progression behind it is not | `test_state_survives_the_rename` |
| `UC-03` | The original value is recorded on the restored object under `archive_renamed_from` | `test_original_name_is_recorded_on_the_restored_object` |
| `UC-04` | The counter climbs past several taken names | `test_counts_up_past_several_taken_names` |
| `UC-05` | Nothing is recorded when the name was still free | `test_nothing_recorded_when_the_name_was_free` |
| `UC-06` | Objects never collide — `ObjectDB` declares no unique fields, so a character restore can never be blocked | `test_objects_never_collide` |

## `_purge_attributes()`

An internal, called by both directions of the copy, and covered on its own because two of its
properties are invisible from `archive()` and `restore()`: which database it reaches, and whether it
builds objects on the way. Both are only checkable at this level.

| ID | Case | Test function |
|---|---|---|
| `PG-01` | The owner's attribute rows are gone. Also pins the ordering: a raw delete runs no cascades, so the links go first — and the attribute ids have to be materialised before that, or the lazy subquery evaluates against rows that no longer exist and nothing is deleted | `test_deletes_the_owners_attribute_rows` |
| `PG-02` | The owner's link rows are gone. Neither half may be left behind — orphan links would be restored as broken references, orphan attributes would accumulate invisibly | `test_deletes_the_owners_link_rows` |
| `PG-03` | A second owner in the same table keeps its attributes and links | `test_leaves_another_owner_in_the_same_table_alone` |
| `PG-04` | The same pk under the other model is untouched — purging `accountdb` 1 leaves `objectdb` 1 alone. The two share a number and nothing but the through table separates them | `test_leaves_the_same_pk_under_the_other_model_alone` |
| `PG-05` | The other database is untouched — purging the archive leaves live rows of the same pks alone. Both databases number attributes from 1, so an alias that leaked would delete live player state | `test_leaves_the_other_database_alone` |
| `PG-06` | An owner with no attributes is a quiet no-op, not an error | `test_an_owner_with_no_attributes_is_a_no_op` |
| `PG-07` | Given the default alias it purges the live database — the parameter is real, not decoration. `restore()` calls it that way, where the destination is empty and the purge is therefore invisible from outside | `test_the_default_alias_purges_the_live_database` |
| `PG-08` | Nothing is instantiated: with the idmapper cleared, a purge leaves it empty | `TestPurgeAttributes.test_instantiates_nothing` |

`PG-05` and `PG-08` are the two the defect turns on; the rest are the ordinary contract.

Attributes are owned outright — Evennia's `AttributeHandler` creates a new row per object and
`do_delete_attribute` deletes the row rather than unlinking — so there is no attribute equivalent of
`DL-07`. Tags are the ones that get shared, because `TagHandler` reaches for `create_tag`, which is a
get-or-create.

## `_copy_attributes()`

The one function both directions share. It replaces `_replace_attributes` (live→archive) and
`_restore_attributes` (archive→live), which did the same job and differed only in which database each
end pointed at — and in that the archive-bound one read its source as model instances.

| ID | Case | Test function |
|---|---|---|
| `CP-01` | Every attribute comes across — same keys, same count | `test_every_attribute_comes_across` |
| `CP-02` | A pickled value survives intact (`db_value`) | `test_a_pickled_value_survives` |
| `CP-03` | An unpickled value survives in `db_strvalue`. This is the half `find_by_attribute()` and `_live_pk_for` match on, so losing it breaks identity lookup rather than merely losing data | `test_an_unpickled_value_survives` |
| `CP-04` | Category, lock string, `db_model` and `db_attrtype` all come across — the whole row, not just key and value. A wrong field list would silently strip categories | `test_the_whole_row_comes_across` |
| `CP-05` | The destination mints its own ids: copying into a database already holding an attribute at one of the source's ids leaves that row alone and lands the copy beside it. If the id travelled with the data, this is where it collides | `test_the_destination_mints_its_own_ids` |
| `CP-06` | The destination is replaced, not merged — an attribute on the destination row and absent from the source is gone afterwards | `test_the_destination_is_replaced_not_merged` |
| `CP-07` | The source row is untouched: same ids, same values, still linked | `test_the_source_is_untouched` |
| `CP-08` | The same function copies archive→live as well as live→archive. Being direction-agnostic is the point of merging the two | `test_copies_in_both_directions` |
| `CP-09` | A second owner's attributes are not swept in — the read selects by owner, not by table | `test_another_owners_attributes_are_not_swept_in` |
| `CP-10` | An owner with no attributes copies nothing, quietly | `test_an_owner_with_no_attributes_copies_nothing` |
| `CP-11` | Nothing from the source database enters the idmapper: with the cache cleared, a copy leaves it empty | `TestCopyAttributes.test_instantiates_nothing` |
| `CP-12` | Correct under a poisoned cache. With an `Attribute` from the destination database already cached at a pk the source uses, holding different content, the copy still writes the source's values | `test_survives_a_poisoned_cache` |

`CP-11` and `CP-12` are the pair the defect turns on — the first says the library does not cause it,
the second says a copy survives it when something else does. `CP-12` asserts its own precondition,
same pk and different content, before copying: without that it passes while testing nothing.

There is no case for `db_date_created`. It travels in the row like every other column and is then
overwritten by `auto_now_add` on the way in, so there is nothing to assert that would not be pinning
Django's behaviour rather than this library's.

`PG-08` is why the purge cannot use `QuerySet.delete()`. Django only fast-deletes a model with no
signal listeners and no inbound cascades, and `Attribute` fails both — Evennia connects `pre_delete`
with no sender, so it listens for every model, and the through tables hold `CASCADE` keys back to it.
So `delete()` builds every row as an object, and Evennia's idmapper caches `Attribute` instances on pk
alone with no database in the key. An archive row and a live row of the same number are one cache
entry.
