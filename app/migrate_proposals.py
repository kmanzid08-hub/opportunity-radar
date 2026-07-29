from sqlalchemy import inspect

from app.database import Base, engine
from app.models import Opportunity  # noqa: F401
from app.proposal_models import Proposal


def migrate() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    if "proposals" in existing_tables:
        print("The proposals table already exists.")
        return

    print("Creating the proposals table...")

    Base.metadata.create_all(
        bind=engine,
        tables=[Proposal.__table__],
    )

    inspector = inspect(engine)

    if "proposals" not in inspector.get_table_names():
        raise RuntimeError(
            "Migration failed: proposals table was not created."
        )

    columns = inspector.get_columns("proposals")

    print("The proposals table was created successfully.")
    print(f"Columns created: {len(columns)}")

    for column in columns:
        print(f"  - {column['name']}")


if __name__ == "__main__":
    migrate()