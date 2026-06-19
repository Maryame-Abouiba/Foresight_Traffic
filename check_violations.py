import sqlite3

conn = sqlite3.connect('violations.db')
cursor = conn.cursor()

print("Violations par vidéo:")
cursor.execute('SELECT source_video, violation_type, COUNT(*) FROM violations GROUP BY source_video, violation_type ORDER BY source_video, violation_type')
for row in cursor.fetchall():
    print(f"Vidéo: {row[0]}, Type: {row[1]}, Count: {row[2]}")

print("\nDétails des violations téléphone:")
cursor.execute('SELECT source_video, violation_type, distraction_label, timestamp FROM violations WHERE violation_type = "phone" OR distraction_label = "phone"')
for row in cursor.fetchall():
    print(f"Vidéo: {row[0]}, Type: {row[1]}, Label: {row[2]}, Time: {row[3]}")

print("\nDétails des violations ceinture:")
cursor.execute('SELECT source_video, violation_type, distraction_label, timestamp FROM violations WHERE violation_type = "no_seatbelt" OR distraction_label = "no_seatbelt"')
for row in cursor.fetchall():
    print(f"Vidéo: {row[0]}, Type: {row[1]}, Label: {row[2]}, Time: {row[3]}")

conn.close()
