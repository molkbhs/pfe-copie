from db import get_connection

conn = get_connection()
cur = conn.cursor(dictionary=True)
try:
    cur.execute('SELECT id, email, role, firstname, lastname FROM users LIMIT 10')
    rows = cur.fetchall()
    print(rows)
finally:
    cur.close()
    conn.close()
