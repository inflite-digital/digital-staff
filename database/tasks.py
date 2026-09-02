import sqlite3

DB_PATH = "staff.db"


def create_task(
    title,
    description=None,
    due_date=None,
    project=None,
):

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO tasks (
            title,
            description,
            due_date,
            project,
            created_at
        )
        VALUES (?, ?, ?, ?, datetime('now'))
    """, (
        title,
        description,
        due_date,
        project
    ))

    conn.commit()
    conn.close()
