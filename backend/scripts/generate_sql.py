"""Generate the Postgres migration from the SQLAlchemy models.

Run: python scripts/generate_sql.py > ../supabase/migrations/0001_init.sql          (all tables)
     python scripts/generate_sql.py products price_rules ... > 0002_x.sql         (only the named tables)
Row level security is enabled on every table with no policies: the browser can never read the
tables directly. All access goes through this API, which uses the service role.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import models  # noqa: F401
from app.db import Base

print("-- Generated from backend/app/models.py. Do not edit by hand; regenerate instead.\n")
only = set(sys.argv[1:])
for table in Base.metadata.sorted_tables:
    if only and table.name not in only:
        continue
    print(str(CreateTable(table).compile(dialect=postgresql.dialect())).strip() + ";\n")
    for index in sorted(table.indexes, key=lambda i: i.name):
        print(str(CreateIndex(index).compile(dialect=postgresql.dialect())).strip() + ";")
    print(f"ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;\n")
