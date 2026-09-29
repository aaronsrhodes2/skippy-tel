import sqlite3
con = sqlite3.connect("/drive-state/photo_tags.db")
rows = con.execute(
    "SELECT drive_name, description FROM photo_tags WHERE description != '' LIMIT 3"
).fetchall()
for name, desc in rows:
    print(f"--- {name} ---")
    print(desc)
    print()
con.close()
