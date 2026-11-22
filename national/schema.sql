CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  official_authority TEXT NOT NULL,
  jurisdiction TEXT,
  runner TEXT NOT NULL,
  rights_class TEXT NOT NULL,
  license TEXT NOT NULL,
  robots_status TEXT NOT NULL,
  registry_sha256 TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  kind TEXT NOT NULL,
  jurisdiction TEXT,
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  case_number TEXT,
  event_date TEXT,
  authority TEXT,
  official_url TEXT NOT NULL,
  provider_url TEXT,
  fetched_at TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  license TEXT,
  rights_class TEXT NOT NULL,
  proposer TEXT,
  cosigners TEXT,
  committee TEXT,
  outcome TEXT,
  UNIQUE(source_id, external_id)
);
CREATE INDEX IF NOT EXISTS records_jurisdiction_date ON records(jurisdiction, event_date);
CREATE INDEX IF NOT EXISTS records_kind_date ON records(kind, event_date);
CREATE INDEX IF NOT EXISTS records_date_id ON records(event_date, id);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
  title, case_number, content='records', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
  INSERT INTO records_fts(rowid, title, case_number) VALUES (new.id, new.title, new.case_number);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
  INSERT INTO records_fts(records_fts, rowid, title, case_number)
    VALUES ('delete', old.id, old.title, old.case_number);
END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
  INSERT INTO records_fts(records_fts, rowid, title, case_number)
    VALUES ('delete', old.id, old.title, old.case_number);
  INSERT INTO records_fts(rowid, title, case_number) VALUES (new.id, new.title, new.case_number);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS bill_people_fts USING fts5(
  proposer, cosigners, committee, content='', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS bill_people_ai AFTER INSERT ON records WHEN new.proposer IS NOT NULL BEGIN
  INSERT INTO bill_people_fts(rowid, proposer, cosigners, committee)
    VALUES (new.id, new.proposer, new.cosigners, new.committee);
END;
CREATE TRIGGER IF NOT EXISTS bill_people_ad AFTER DELETE ON records WHEN old.proposer IS NOT NULL BEGIN
  INSERT INTO bill_people_fts(bill_people_fts, rowid, proposer, cosigners, committee)
    VALUES ('delete', old.id, old.proposer, old.cosigners, old.committee);
END;
CREATE TRIGGER IF NOT EXISTS bill_people_au_delete AFTER UPDATE ON records WHEN old.proposer IS NOT NULL BEGIN
  INSERT INTO bill_people_fts(bill_people_fts, rowid, proposer, cosigners, committee)
    VALUES ('delete', old.id, old.proposer, old.cosigners, old.committee);
END;
CREATE TRIGGER IF NOT EXISTS bill_people_au_insert AFTER UPDATE ON records WHEN new.proposer IS NOT NULL BEGIN
  INSERT INTO bill_people_fts(rowid, proposer, cosigners, committee)
    VALUES (new.id, new.proposer, new.cosigners, new.committee);
END;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('OK','BUDGET_STOPPED','CAPACITY_BLOCKED','ERROR','NOT_CONFIGURED')),
  fetched INTEGER NOT NULL,
  changed INTEGER NOT NULL,
  rows_written INTEGER NOT NULL,
  cursor_before TEXT,
  cursor_after TEXT,
  error TEXT
);
