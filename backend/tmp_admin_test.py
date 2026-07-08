from app import app, create_access_token
from db import get_connection
import json

conn = get_connection()
cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, email, firstname, lastname, role FROM users WHERE role='admin' LIMIT 1")
admin = cur.fetchone()
cur.close()
conn.close()
if not admin:
    raise SystemExit('No admin user found')

with app.test_client() as client:
    token = create_access_token(admin)
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    data = {
        'firstname': 'Test',
        'lastname': 'User',
        'email': 'test.user+admin@example.com',
        'password': 'Aa1!test123',
        'role': 'user'
    }
    res = client.post('/api/admin/users', headers=headers, data=json.dumps(data))
    print('status', res.status_code)
    print(res.get_data(as_text=True))
