-- A write's reply, kept a day under its request_id.
CREATE TABLE IF NOT EXISTS rpc_replies (
  caller     TEXT    NOT NULL,
  request_id TEXT    NOT NULL,
  method     TEXT    NOT NULL,
  params     TEXT    NOT NULL,
  reply      TEXT    NOT NULL,
  ts         INTEGER NOT NULL,
  PRIMARY KEY (caller, request_id)
);
CREATE INDEX IF NOT EXISTS rpc_replies_ts ON rpc_replies(ts);
