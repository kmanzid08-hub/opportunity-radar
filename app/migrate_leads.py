from sqlalchemy import inspect, text

from app.database import Base, engine
from app.models import Opportunity  # noqa: F401
from app.lead_models import Lead


def migrate() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    if "leads" in existing_tables:
        print("The leads table already exists.")
        return

    if "proposals" in existing_tables:
        with engine.begin() as connection:
            proposal_count = connection.execute(
                text("SELECT COUNT(*) FROM proposals")
            ).scalar_one()

            if proposal_count > 0:
                raise RuntimeError(
                    "The proposals table contains data and cannot be "
                    "removed automatically."
                )

            print("Removing the unused empty proposals table...")
            connection.execute(text("DROP TABLE proposals"))

    print("Creating the leads table...")

    Base.metadata.create_all(
        bind=engine,
        tables=[Lead.__table__],
    )

    inspector = inspect(engine)

    if "leads" not in inspector.get_table_names():
        raise RuntimeError(
            "Migration failed: the leads table was not created."
        )

    columns = inspector.get_columns("leads")

    print("The leads table was created successfully.")
    print(f"Columns created: {len(columns)}")

    for column in columns:
        print(f"  - {column['name']}")


if __name__ == "__main__":
    migrate()