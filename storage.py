import json
import os
import asyncio
from typing import Optional

CONFIG_FILE = "config.json"
USERS_FILE = "users.json"

DEFAULT_CONFIG = {
    "log_channel_id": None,
    "force_join_channel": None,
    "auto_delete_time": 0,
    "delete_mode": "off"
}

_lock = asyncio.Lock()

# ─────────────────────────────────────────────
# CONFIG HANDLERS
# ─────────────────────────────────────────────

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_config_value(key: str):
    return load_config().get(key)

def set_config_value(key: str, value):
    config = load_config()
    config[key] = value
    save_config(config)

# ─────────────────────────────────────────────
# USER HANDLERS
# ─────────────────────────────────────────────

def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        save_users({})
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def get_user(user_id: int) -> Optional[dict]:
    users = load_users()
    return users.get(str(user_id))

def save_user(user_id: int, data: dict):
    users = load_users()
    users[str(user_id)] = data
    save_users(users)

def get_or_create_user(user_id: int, name: str, username: str) -> dict:
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "name": name,
            "username": username or "",
            "log_msg_id": None,
            "messages": [],
            "auto_delete_time": None,
            "blocked": False,
            "last_msg_time": 0
        }
    else:
        users[uid]["name"] = name
        users[uid]["username"] = username or ""
        if "blocked" not in users[uid]:
            users[uid]["blocked"] = False
        if "last_msg_time" not in users[uid]:
            users[uid]["last_msg_time"] = 0
        if "auto_delete_time" not in users[uid]:
            users[uid]["auto_delete_time"] = None
    save_users(users)
    return users[uid]

def block_user(user_id: int):
    users = load_users()
    uid = str(user_id)
    if uid in users:
        users[uid]["blocked"] = True
        save_users(users)

def unblock_user(user_id: int):
    users = load_users()
    uid = str(user_id)
    if uid in users:
        users[uid]["blocked"] = False
        save_users(users)

def get_all_users() -> dict:
    return load_users()

def count_users() -> int:
    return len(load_users())

def count_blocked() -> int:
    users = load_users()
    return sum(1 for u in users.values() if u.get("blocked", False))

# ─────────────────────────────────────────────
# MESSAGE TRACKING (for auto-delete)
# ─────────────────────────────────────────────

TRACKED_FILE = "tracked.json"

def load_tracked() -> list:
    if not os.path.exists(TRACKED_FILE):
        return []
    try:
        with open(TRACKED_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_tracked(tracked: list):
    with open(TRACKED_FILE, "w") as f:
        json.dump(tracked, f, indent=2)

def add_tracked_message(msg_id: int, chat_id: int, user_id: int,
                        timestamp: float, delete_after: int):
    tracked = load_tracked()
    tracked.append({
        "msg_id": msg_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "timestamp": timestamp,
        "delete_after": delete_after,
        "deleted": False
    })
    save_tracked(tracked)

def remove_tracked_message(msg_id: int, chat_id: int):
    tracked = load_tracked()
    tracked = [t for t in tracked
               if not (t["msg_id"] == msg_id and t["chat_id"] == chat_id)]
    save_tracked(tracked)
