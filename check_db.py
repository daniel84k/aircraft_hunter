import psycopg
import os
from psycopg.rows import dict_row

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("DATABASE_URL not set")
    exit(1)

with psycopg.connect(db_url, row_factory=dict_row) as conn:
    with conn.cursor() as cur:
        print("--- Table Sizes ---")
        cur.execute("SELECT relname, n_live_tup FROM pg_stat_user_tables;")
        for row in cur.fetchall():
            print(f"{row['relname']}: {row['n_live_tup']} rows")
        
        print("\n--- Indexes ---")
        cur.execute("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public';")
        for row in cur.fetchall():
            print(f"{row['tablename']}.{row['indexname']}: {row['indexdef']}")
