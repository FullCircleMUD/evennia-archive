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
| `ID` | `ArchivableMixin` — minting and storing the identity |
| `AR` | `archive()` |
| `RS` | `restore()` |
| `AC` | The same round trip on `AccountDB` rather than `ObjectDB` |
| `FN` | `find()` |
| `DL` | `delete()` |
| `UC` | Restore into a taken unique value |

## Fixtures

| Fixture | Purpose |
|---|---|
| `databases = {"default", "archive"}` on every case class touching the archive | Django only builds test databases for aliases a class declares. Without it the archive alias is never created and every query against it raises `DatabaseOperationForbidden` |
| Distinct `TEST["NAME"]` shared-cache URIs in `tests/test_settings.py` | Two aliases both saying `:memory:` look like one database to Django's runner, which then treats the second as a mirror of the first. The router would appear to work while both pointed at the same file — so a copy landing in the wrong alias would pass |
| `ArchivableTestObject` / `ArchivableTestAccount` | Minimal typeclasses carrying the mixin. Both halves are needed: an account has a different creation hook, a unique username, and Django's `PermissionsMixin` bolted on |

## Smoke

| ID | Case | Test function |
|---|---|---|
| `SM-01` | The package exposes `__version__` | `test_version_is_exposed` |
| `SM-02` | Django loads `evennia_archive` as an installed app | `test_registered_in_installed_apps` |

## Identity — `ArchivableMixin`

| ID | Case | Test function |
|---|---|---|
| `ID-01` | Creating an object carrying the mixin mints an `archive_id` | `test_creation_mints_an_id` |
| `ID-02` | The minted value is a canonical UUID — round-tripping it through `uuid.UUID` and back is a no-op, which is what makes plain string equality a safe lookup | `test_minted_id_is_a_canonical_uuid` |
| `ID-03` | `at_archive_init()` never overwrites: calling it again returns the existing identity unchanged | `test_init_is_idempotent` |
| `ID-04` | Two objects mint different identities | `test_ids_are_unique_across_objects` |
| `ID-05` | The identity is stored unpickled — `db_strvalue` set, `db_value` null. Flipping this would make every lookup a byte comparison whose stability depends on the pickle protocol, and would silently orphan existing installs | `test_stored_unpickled_in_strvalue` |
| `ID-06` | An object without the mixin has no identity, and is therefore not archivable | `test_object_without_the_mixin_has_no_identity` |

## `archive()`

| ID | Case | Test function |
|---|---|---|
| `AR-01` | An object whose typeclass does not carry `ArchivableMixin` raises `NotArchivable` | `test_refuses_an_object_without_identity` |
| `AR-02` | A copy lands in the archive, and the record names the model and the key it landed under | `test_creates_a_copy_in_the_archive` |
| `AR-03` | The copy does not land in the live database — the failure the router exists to prevent, and one that would otherwise look like success | `TestArchive.test_copy_does_not_land_in_the_live_database` |
| `AR-04` | Attributes come across, pickled values included | `test_attributes_come_across` |
| `AR-05` | The identity comes across in `db_strvalue` | `test_identity_comes_across` |
| `AR-06` | A second archive updates the existing copy rather than duplicating it, and the record still points at the same row | `test_second_archive_updates_rather_than_duplicates` |
| `AR-07` | An attribute removed from the live object is removed from the copy | `test_removed_attributes_are_removed_from_the_copy` |
| `AR-08` | The location reference is dropped on the way in | `test_location_reference_is_dropped` |
| `AR-09` | An object exposing `archive_id` without the mixin is refused — the attribute is not the contract | `test_refuses_a_hand_rolled_archive_id` |
| `AR-10` | An object carrying the mixin but never initialised raises `NotArchivable`, naming `at_archive_init()` | `test_refuses_a_mixin_object_never_initialised` |

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

| ID | Case | Test function |
|---|---|---|
| `AC-01` | Creating an account carrying the mixin mints an identity, via `at_account_creation` | `test_account_creation_mints_an_id` |
| `AC-02` | The account round trip closes: username, email, attributes, tags and identity all come back | `test_round_trip_restores_the_account` |
| `AC-03` | The record names `accountdb` as the archived model | `test_record_names_the_account_model` |
| `AC-04` | The account copy does not land in the live database | `TestAccountRoundTrip.test_copy_does_not_land_in_the_live_database` |
| `AC-05` | The restored account has a different primary key | `test_restored_account_has_a_new_primary_key` |

## `find()`

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
