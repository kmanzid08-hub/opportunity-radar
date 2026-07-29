from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app.database import engine


NEW_COLUMNS: dict[str, str] = {
    "status": (
        "ALTER TABLE opportunities "
        "ADD COLUMN status VARCHAR(50) "
        "NOT NULL DEFAULT 'New'"
    ),
    "is_expired": (
        "ALTER TABLE opportunities "
        "ADD COLUMN is_expired BOOLEAN "
        "NOT NULL DEFAULT 0"
    ),
    "user_notes": (
        "ALTER TABLE opportunities "
        "ADD COLUMN user_notes TEXT"
    ),
    "first_discovered_at": (
        "ALTER TABLE opportunities "
        "ADD COLUMN first_discovered_at DATETIME"
    ),
    "last_seen_at": (
        "ALTER TABLE opportunities "
        "ADD COLUMN last_seen_at DATETIME"
    ),
    "updated_at": (
        "ALTER TABLE opportunities "
        "ADD COLUMN updated_at DATETIME"
    ),
}


def migrate() -> None:
    inspector = inspect(engine)

    if "opportunities" not in inspector.get_table_names():
        print(
            "The opportunities table does not exist. "
            "Run the scanner first."
        )
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns(
            "opportunities"
        )
    }

    added_columns: list[str] = []

    with engine.begin() as connection:
        for column_name, statement in NEW_COLUMNS.items():
            if column_name in existing_columns:
                print(
                    f"[EXISTS] {column_name}"
                )
                continue

            connection.execute(text(statement))
            added_columns.append(column_name)

            print(
                f"[ADDED] {column_name}"
            )

        current_time = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

        connection.execute(
            text(
                """
                UPDATE opportunities
                SET first_discovered_at =
                    COALESCE(
                        first_discovered_at,
                        created_at,
                        :current_time
                    )
                """
            ),
            {
                "current_time": current_time,
            },
        )

        connection.execute(
            text(
                """
                UPDATE opportunities
                SET last_seen_at =
                    COALESCE(
                        last_seen_at,
                        created_at,
                        :current_time
                    )
                """
            ),
            {
                "current_time": current_time,
            },
        )

        connection.execute(
            text(
                """
                UPDATE opportunities
                SET updated_at =
                    COALESCE(
                        updated_at,
                        created_at,
                        :current_time
                    )
                """
            ),
            {
                "current_time": current_time,
            },
        )

        connection.execute(
            text(
                """
                UPDATE opportunities
                SET status = 'New'
                WHERE status IS NULL
                   OR TRIM(status) = ''
                """
            )
        )

        connection.execute(
            text(
                """
                UPDATE opportunities
                SET is_expired = 0
                WHERE is_expired IS NULL
                """
            )
        )

    print()
    print("=" * 60)
    print("Phase 2 database migration completed")
    print("=" * 60)

    if added_columns:
        print(
            "Columns added: "
            + ", ".join(added_columns)
        )
    else:
        print(
            "No new columns were required."
        )

    print(
        "Existing opportunities were preserved."
    )


if __name__ == "__main__":
    migrate()