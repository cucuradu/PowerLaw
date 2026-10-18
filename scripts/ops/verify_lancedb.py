import lancedb
import pandas as pd

db = lancedb.connect("./storage/lancedb")
print(f"Tables: {db.table_names()}")

if "structured_rules" in db.table_names():
    tbl = db.open_table("structured_rules")
    df = tbl.to_pandas()
    print(f"Total Rows: {len(df)}")
    print(df[["law_id", "chunk_id", "summary", "rule_type"]].head(10))
else:
    print("Table 'structured_rules' not found yet.")
