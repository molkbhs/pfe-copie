"""
Script pour créer un utilisateur de test.
Exécuter : python seed_user.py
"""
from db import get_connection
from werkzeug.security import generate_password_hash

EMAIL = "test@example.com"
USERNAME = "testuser"
PASSWORD = "test123"

def main():
    conn = get_connection()
    cursor = conn.cursor()
    hashed = generate_password_hash(PASSWORD)
    try:
        cursor.execute(
            "INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
            (USERNAME, EMAIL, hashed)
        )
        conn.commit()
        print(f"✅ Utilisateur créé : {EMAIL} / {PASSWORD}")
    except Exception as e:
        if "Duplicate" in str(e):
            print("⚠️ L'utilisateur existe déjà.")
        else:
            print(f"❌ Erreur : {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
