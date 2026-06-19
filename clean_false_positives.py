import sqlite3

conn = sqlite3.connect('violations.db')
cursor = conn.cursor()

# Delete distraction violations that are likely false positives
cursor.execute('DELETE FROM violations WHERE violation_type IN ("phone", "no_seatbelt", "cigarette")')
deleted = cursor.rowcount
conn.commit()

print(f'Supprimé {deleted} violations de distraction (faux positifs probables)')

# Verify remaining violations
cursor.execute('SELECT source_video, violation_type, COUNT(*) FROM violations GROUP BY source_video, violation_type ORDER BY source_video, violation_type')
print('\nViolations restantes:')
for row in cursor.fetchall():
    print(f"Vidéo: {row[0]}, Type: {row[1]}, Count: {row[2]}")

conn.close()
