import sqlite3
con = sqlite3.connect("/drive-state/photo_tags.db")
total = con.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0]
print("Total rows:", total)
tagged = con.execute("SELECT COUNT(*) FROM photo_tags WHERE description != '' AND tagged_at > 0").fetchone()[0]
print("Fully tagged:", tagged)
rows = con.execute("SELECT drive_name, length(description), tagged_at FROM photo_tags ORDER BY tagged_at DESC LIMIT 10").fetchall()
for r in rows:
    print(f"  {r[0][:45]:45s} desc={r[1]}b  tagged={int(r[2])}")
con.close()
