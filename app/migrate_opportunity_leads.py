from sqlalchemy import inspect, text

from app.database import engine


COLUMNS_TO_ADD = {
    "is_lead": "BOOLEAN NOT NULL DEFAULT 0",
    "assigned_to": "VARCHAR(255)",
    "lead_status": "VARCHAR(50)",
    "lead_priority": "VARCHAR(20)",
    "next_action": "TEXT",
    "next_action_due": "DATE",
    "last_follow_up_date": "DATE",
    "lead_notes": "TEXT",
}


def migrate() -> None:
    inspector = inspect(engine)

    if "opportunities" not in inspector.get_table_names():
        raise RuntimeError(
            "The opportunities table does not exist."
        )

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("opportunities")
    }

    added_columns = []

    with engine.begin() as connection:
        for column_name, column_definition in COLUMNS_TO_ADD.items():
            if column_name in existing_columns:
                print(f"Column already exists: {column_name}")
                continue

            print(f"Adding column: {column_name}")

            connection.execute(
                text(
                    f"ALTER TABLE opportunities "
                    f"ADD COLUMN {column_name} {column_definition}"
                )
            )

            added_columns.append(column_name)

    inspector = inspect(engine)

    final_columns = {
        column["name"]
        for column in inspector.get_columns("opportunities")
    }

    missing_columns = set(COLUMNS_TO_ADD) - final_columns

    if missing_columns:
        raise RuntimeError(
            "Migration incomplete. Missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    if added_columns:
        print("")
        print("Lead-tracking columns added successfully:")

        for column_name in added_columns:
            print(f"  - {column_name}")
    else:
        print("")
        print("All lead-tracking columns already exist.")


if __name__ == "__main__":
    migrate()