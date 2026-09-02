CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    due_date TEXT,
    status TEXT DEFAULT 'OPEN',
    priority TEXT DEFAULT 'MEDIUM',
    project TEXT,
    owner TEXT,
    created_at TEXT,
    completed_at TEXT
);

CREATE TABLE issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    owner TEXT,
    status TEXT DEFAULT 'OPEN',
    next_action TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT NOT NULL,
    category TEXT,
    created_at TEXT
);
