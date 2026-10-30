-- LastAccountActivity reads MAX(ts) over one account's turn events; events had no account index, so it went through every turn event.
CREATE INDEX IF NOT EXISTS events_account_ts ON events(account, ts);
