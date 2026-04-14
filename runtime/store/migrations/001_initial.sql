CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    severity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id_created_at
    ON run_events(run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_run_events_event_type
    ON run_events(event_type);
