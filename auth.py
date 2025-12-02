from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import time

auth = Blueprint("auth", __name__)

# In-Memory DB
USERS = {}        # { username: password_hash }
USER_HISTORY = {} # { username: [ {input, result} ] }
TOKENS = {}       # { token: {"username": str, "ts": int} }


# ---------------- SERVICE FUNCTIONS (app.py için) ----------------

def register_user(username: str, password: str):
    username = (username or '').strip()
    password = (password or '').strip()
    if not username or not password:
        raise ValueError('Kullanıcı adı ve şifre zorunludur.')
    if username in USERS:
        raise ValueError('Bu kullanıcı adı zaten var.')
    USERS[username] = generate_password_hash(password)
    USER_HISTORY.setdefault(username, [])
    return { 'username': username }


def login_user(username: str, password: str) -> str:
    username = (username or '').strip()
    password = (password or '').strip()
    if username not in USERS:
        raise ValueError('Kullanıcı bulunamadı.')
    if not check_password_hash(USERS[username], password):
        raise ValueError('Şifre hatalı.')
    token = secrets.token_urlsafe(24)
    TOKENS[token] = { 'username': username, 'ts': int(time.time()) }
    return token


def verify_token(token: str):
    info = TOKENS.get(token)
    if not info:
        raise ValueError('Geçersiz veya süresi dolmuş token.')
    return { 'username': info['username'] }


def logout_token(token: str):
    if token in TOKENS:
        del TOKENS[token]
        return True
    return False


def push_history(username: str, entry: dict):
    USER_HISTORY.setdefault(username, []).append(entry)

def clear_history(username: str):
    USER_HISTORY[username] = []

# ---------------- REGISTER ----------------

@auth.route("/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"error": "Kullanıcı adı ve şifre zorunludur."}), 400

    if username in USERS:
        return jsonify({"error": "Bu kullanıcı adı zaten var."}), 400

    USERS[username] = generate_password_hash(password)
    USER_HISTORY.setdefault(username, [])

    session["user"] = username
    return jsonify({"message": "Kayıt başarılı.", "username": username}), 200


# ---------------- LOGIN ----------------

@auth.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if username not in USERS:
        return jsonify({"error": "Kullanıcı bulunamadı."}), 400

    if not check_password_hash(USERS[username], password):
        return jsonify({"error": "Şifre hatalı."}), 400

    session["user"] = username
    return jsonify({"message": "Giriş başarılı.", "username": username})


# ---------------- LOGOUT ----------------

@auth.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    return jsonify({"message": "Çıkış yapıldı."})


# ---------------- ME ----------------

@auth.route("/me", methods=["GET"])
def me():
    username = session.get("user")
    if not username:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "username": username})


# ---------------- HISTORY ----------------

@auth.route("/history", methods=["GET"])
def history():
    username = session.get("user")
    if not username:
        return jsonify({"history": []})
    return jsonify({"history": USER_HISTORY.get(username, [])})
