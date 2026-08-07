-- Service-account Drive access mode: dedicated column for the per-company
-- service-account private key (Fernet-encrypted at the app layer, like every
-- other credential in this table). Kept SEPARATE from the OAuth user token in
-- token_json_encrypted so both credentials coexist on one google_drive
-- connection: the OAuth token drives the individual-file Picker, the SA key
-- drives the shared-folder enumeration/ingest. Never serialized to the client
-- (the connectors API uses an explicit allowlist that excludes this column).
alter table connections add column if not exists sa_key_encrypted text;
