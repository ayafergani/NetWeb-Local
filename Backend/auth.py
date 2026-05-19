from flask import Blueprint, jsonify, request

from Database.db import get_db_connection
from utils.security import check_password, hash_password

auth_bp = Blueprint("auth", __name__)
LOCAL_BASE_URL = "http://127.0.0.1:5000"


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "Nom d'utilisateur et mot de passe requis"}), 400

    try:
        username = username.strip()
        password = password.strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id_user, username, password, role, email FROM utilisateur WHERE LOWER(username) = LOWER(%s)",
            (username,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Erreur de base de donnees : {str(e)}"}), 500

    if not user:
        return jsonify({"error": "Identifiants incorrects"}), 401

    stored_hash = user[2]

    if isinstance(stored_hash, str):
        stored_hash = stored_hash.encode("utf-8")
    elif not isinstance(stored_hash, bytes):
        return jsonify({"error": "Format de mot de passe invalide"}), 500

    if check_password(password, stored_hash):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE utilisateur SET last_login = NOW() WHERE id_user = %s",
                (user[0],),
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Erreur mise a jour last_login: {e}")

        user_role = user[3].upper() if user[3] else "AUDITOR"

        return jsonify(
            {
                "message": "login success",
                "username": user[1],
                "email": user[4] or "",
                "role": user_role,
            }
        ), 200

    return jsonify({"error": "Identifiants incorrects"}), 401


@auth_bp.route("/logout", methods=["POST"])
def logout():
    try:
        data = request.json or {}
        current_user = data.get("username", "").strip()

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE utilisateur SET last_logout = NOW() WHERE username = %s",
            (current_user,),
        )
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "Deconnexion reussie"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    return jsonify(
        {
            "message": "Fonctionnalite de reinitialisation de mot de passe desactivee."
        }
    ), 200


@auth_bp.route("/api/check-email", methods=["POST"])
def check_email():
    data = request.json
    identity = (data.get("identity") or "").strip()
    if not identity:
        return jsonify({"error": "identity requis"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id_user, username, email FROM utilisateur WHERE LOWER(email) = LOWER(%s) OR LOWER(username) = LOWER(%s)",
            (identity, identity),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Erreur base de donnees: {str(e)}"}), 500

    if not user:
        return jsonify({"error": "Utilisateur non trouve"}), 404

    return jsonify({"id": user[0], "username": user[1], "email": user[2]}), 200


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    return jsonify({"error": "Fonctionnalite desactivee"}), 400


@auth_bp.route("/reset-password-final", methods=["POST"])
def reset_password_final():
    data = request.json
    username = data.get("username")
    new_password = data.get("new_password")

    if not username or not new_password:
        return jsonify({"error": "username et new_password requis"}), 400

    username = username.strip()
    new_password = new_password.strip()

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        new_hash_bytes = hash_password(new_password)
        new_hash_str = new_hash_bytes.decode("utf-8")

        cursor.execute(
            "UPDATE utilisateur SET password = %s WHERE username = %s",
            (new_hash_str, username),
        )

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({"error": "Utilisateur introuvable"}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Mot de passe mis a jour avec succes."}), 200

    except Exception as e:
        return jsonify({"error": f"Erreur base de donnees: {str(e)}"}), 500


@auth_bp.route("/verify-password-storage", methods=["POST"])
def verify_password_storage():
    data = request.json
    username = data.get("username")

    if not username:
        return jsonify({"error": "username requis"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, LENGTH(password) as pwd_length, LEFT(password, 10) as pwd_start FROM utilisateur WHERE username = %s",
            (username,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user:
            return jsonify({"error": "Utilisateur non trouve"}), 404

        return jsonify(
            {
                "username": user[0],
                "password_length": user[1],
                "password_start": user[2],
                "expected_bcrypt_length": 60,
                "is_valid_length": user[1] == 60,
            }
        ), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
