import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote, unquote

import yaml
from flask import current_app, g
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


def init_db() -> None:
    os.makedirs("users/admin/configs", exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS json_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        data TEXT NOT NULL
    )
    """
    )
    conn.commit()
    cursor.execute("SELECT * FROM json_data WHERE username='admin'")
    row = cursor.fetchone()

    if not row:  # If no row is found
        # Load the default JSON data
        default_json = {
            "username": "admin",
            "password": generate_password_hash("password"),
            "devices": {
                "9abe2858": {
                    "id": "9abe2858",
                    "name": "Tronbyt 1",
                    "default_interval": 18,
                    "brightness": 40,
                    "night_brightness": 10,
                    "night_mode_enabled": False,
                    "night_start": 22,
                    "night_end": 6,
                    "timezone": 100,
                    "img_url": "",
                    "night_mode_app": "None",
                    "api_key": "CHANGEME",
                    "notes": "",
                    "apps": {
                        "994": {
                            "iname": "994",
                            "name": "fireflies",
                            "uinterval": 1,
                            "display_time": 0,
                            "notes": "",
                            "enabled": True,
                            "last_render": 1739393487,
                            "path": "system-apps/apps/fireflies/fireflies.star",
                            "order": 0,
                        }
                    },
                }
            },
        }

        # Insert default JSON
        cursor.execute(
            "INSERT INTO json_data (data, username) VALUES (?, 'admin')",
            (json.dumps(default_json),),
        )
        conn.commit()
        current_app.logger.debug("Default JSON inserted for admin user")

        # Copy the default files to the expected locations
        if not current_app.testing:
            os.makedirs("tronbyt_server/webp/9abe2858", exist_ok=True)
            shutil.copyfile(
                "tronbyt_server/static/images/fireflies.webp",
                "tronbyt_server/webp/9abe2858/fireflies-994.webp",
            )
    conn.commit()


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(current_app.config["DB_FILE"])
    return db


def delete_device_dirs(device_id: str) -> None:
    # Get the name of the current app
    app_name = current_app.name

    # Construct the path to the directory to delete
    dir_to_delete = os.path.join(app_name, "webp", device_id)

    # Delete the directory recursively
    try:
        shutil.rmtree(dir_to_delete)
        current_app.logger.debug(f"Successfully deleted directory: {dir_to_delete}")
    except FileNotFoundError:
        current_app.logger.error(f"Directory not found: {dir_to_delete}")
    except Exception as e:
        current_app.logger.error(f"Error deleting directory {dir_to_delete}: {str(e)}")


def server_tz_offset() -> int:
    output = subprocess.check_output(["date", "+%z"]).decode().strip()
    # Convert the offset to a timedelta
    sign = 1 if output[0] == "+" else -1
    hours = int(output[1:3])
    minutes = int(output[3:5])
    offset = sign * (hours * 3600 + minutes * 60)
    return offset


def get_last_app_index(device_id: str) -> int:
    device = get_device_by_id(device_id)
    if device is None:
        return
    return device.get("last_app_index", 0)


def save_last_app_index(device_id: str, index: int) -> None:
    user = get_user_by_device_id(device_id)
    if user is None:
        return
    device = user["devices"].get(device_id)
    if device is None:
        return
    device["last_app_index"] = index
    save_user(user)


def get_night_mode_is_active(device: Dict[str, Any]) -> bool:
    if not device.get("night_mode_enabled", False):
        return False
    # configured, adjust current hour to set device timezone
    if "timezone" in device and device["timezone"] != 100:
        current_hour = (datetime.now(timezone.utc).hour + device["timezone"]) % 24
    else:
        current_hour = datetime.now().hour
    # current_app.logger.debug(f"current_hour:{current_hour} -- ",end="")
    if device.get("night_start", -1) > -1:
        start_hour = device["night_start"]
        end_hour = device.get("night_end", 6)  # default to 6 am if not set
        if start_hour <= end_hour:  # Normal case (e.g., 9 to 17)
            if start_hour <= current_hour <= end_hour:
                current_app.logger.debug("nightmode active")
                return True
        else:  # Wrapped case (e.g., 22 to 6 - overnight)
            if current_hour >= start_hour or current_hour <= end_hour:
                current_app.logger.debug("nightmode active")
                return True
    return False


def get_device_brightness(device: Dict[str, Any]) -> int:
    if "night_brightness" in device and get_night_mode_is_active(device):
        return int(device["night_brightness"] * 2)
    else:
        return int(device.get("brightness", 30) * 2)


def brightness_int_from_string(brightness_string: str) -> int:
    brightness_mapping = {"dim": 10, "low": 20, "medium": 40, "high": 80}
    # Get the numerical value from the dictionary, default to 50 if not found
    brightness_value = brightness_mapping[brightness_string]
    return brightness_value


def get_users_dir() -> str:
    # current_app.logger.debug(f"users dir : {current_app.config['USERS_DIR']}")
    return current_app.config["USERS_DIR"]


# Ensure all apps have an "order" attribute
# Earlier releases did not have this attribute,
# so we need to ensure it exists to allow reordering of the app list.
# Eventually, this function should be deleted.
def ensure_app_order(user: Dict[str, Any]) -> None:
    modified = False
    for device in user.get("devices", {}).values():
        apps = device.get("apps", {})
        for idx, app in enumerate(apps.values()):
            if "order" not in app:
                app["order"] = idx
                modified = True

    if modified:
        save_user(user)


def get_user(username: str) -> Optional[Dict[str, Any]]:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT data FROM json_data WHERE username = ?", (str(username),)
        )
        row = cursor.fetchone()
        if row:
            user = json.loads(row[0])
            ensure_app_order(user)
            return user
        else:
            current_app.logger.error(f"{username} not found")
            return None
    except Exception as e:
        current_app.logger.error("problem with get_user" + str(e))
        return None


def auth_user(username: str, password: str) -> Union[Dict[str, Any], bool]:
    user = get_user(username)
    if user:
        if check_password_hash(user.get("password"), password):
            current_app.logger.debug(f"returning {user}")
            return user
        else:
            current_app.logger.info("bad password")
            return False
    return None


def save_user(user: Dict[str, Any], new_user: bool = False) -> bool:
    if "username" not in user:
        current_app.logger.warning("no username in user")
        return False
    if current_app.testing:
        current_app.logger.debug(f"user data passed to save_user : {user}")
    username = user["username"]
    try:
        conn = get_db()
        cursor = conn.cursor()
        # json
        if new_user:
            cursor.execute(
                "INSERT INTO json_data (data, username) VALUES (?, ?)",
                (json.dumps(user), str(username)),
            )
            create_user_dir(username)
        else:
            cursor.execute(
                "UPDATE json_data SET data = ? WHERE username = ?",
                (json.dumps(user), str(username)),
            )
        conn.commit()

        if current_app.config.get("PRODUCTION") == "0":
            current_app.logger.debug("writing to json file for visibility")
            with open(
                f"{get_users_dir()}/{username}/{username}_debug.json", "w"
            ) as file:
                json_string = json.dumps(user, indent=4)
                if current_app.testing:
                    current_app.logger.debug(f"writing json of {user}")
                else:
                    json_string.replace(
                        user["username"], "DO NOT EDIT THIS FILE, FOR DEBUG ONLY"
                    )
                file.write(json_string)

        return True
    except Exception as e:
        current_app.logger.error("couldn't save {} : {}".format(user, str(e)))
        return False


def delete_user(username: str) -> bool:
    try:
        conn = get_db()
        conn.cursor().execute("DELETE FROM json_data WHERE username = ?", (username,))
        conn.commit()
        user_dir = os.path.join(get_users_dir(), username)
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)
        current_app.logger.info(f"User {username} deleted successfully")
        return True
    except Exception as e:
        current_app.logger.error(f"Error deleting user {username}: {e}")
        return False


def create_user_dir(user: str) -> None:
    # create the user directory if it doesn't exist
    user_dir = os.path.join(get_users_dir(), user)
    os.makedirs(os.path.join(user_dir, "configs"), exist_ok=True)
    os.makedirs(os.path.join(user_dir, "apps"), exist_ok=True)


def get_apps_list(user: str) -> List[Dict[str, Any]]:
    app_list = list()
    # test for directory named dir and if not exist create it
    if user == "system" or user == "":
        list_file = "system-apps.json"
        if not os.path.exists(list_file):
            current_app.logger.info("Generating apps.json file...")
            subprocess.run(["python3", "clone_system_apps_repo.py"])
            current_app.logger.debug("apps.json file generated.")

        with open(list_file, "r") as f:
            return json.load(f)

    dir = os.path.join(get_users_dir(), user, "apps")
    if os.path.exists(dir):
        for root, _, files in os.walk(dir):
            for file in files:
                if file.endswith(".star"):
                    app_path = os.path.join(root, file)
                    app_dict = dict()
                    app_dict["path"] = app_path
                    app_dict["name"] = os.path.splitext(os.path.basename(app_path))[0]
                    app_dict["image_url"] = app_dict["name"] + ".gif"
                    # look for a yaml file
                    app_base_path = ("/").join(app_dict["path"].split("/")[0:-1])
                    yaml_path = os.path.join(app_base_path, "manifest.yaml")
                    current_app.logger.debug(
                        "checking for manifest.yaml in {}".format(yaml_path)
                    )
                    # check for existence of yaml_path
                    if os.path.exists(yaml_path):
                        with open(yaml_path, "r") as f:
                            yaml_dict = yaml.safe_load(f)
                            app_dict.update(yaml_dict)
                    else:
                        app_dict["summary"] = "Custom App"
                    app_list.append(app_dict)
        return app_list
    else:
        current_app.logger.warning("no apps list found for {}".format(user))
        return []


def get_app_details(user: str, name: str) -> Dict[str, Any]:
    # first look for the app name in the custom apps
    custom_apps = get_apps_list(user)
    current_app.logger.debug(user, name)
    for app in custom_apps:
        current_app.logger.debug(app)
        if app["name"] == name:
            # we found it
            return app
    # if we get here then the app is not in custom apps
    # so we need to look in the system-apps directory
    apps = get_apps_list("system")
    for app in apps:
        if app["name"] == name:
            return app
    return {}


def sanitize(string: str) -> str:
    string = string.replace(" ", "_")
    string = string.replace("-", "")
    string = string.replace(".", "")
    string = string.replace("/", "")
    string = string.replace("\\", "")
    string = string.replace("%", "")
    string = string.replace("'", "")
    return string


def sanitize_url(url: str) -> str:
    # Decode any percent-encoded characters
    url = unquote(url)
    # Replace spaces with underscores
    url = url.replace(" ", "_")
    # Remove unwanted characters
    for char in ["'", "\\"]:
        url = url.replace(char, "")
    # Encode back into a valid URL
    url = quote(url, safe="/:.?&=")  # Allow standard URL characters
    return url


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ["star"]


def save_user_app(file: Any, path: str) -> bool:
    filename = sanitize(file.filename)
    filename = secure_filename(filename)

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(path, filename))
        return True
    else:
        return False


def delete_user_upload(user: Dict[str, Any], filename: str) -> bool:
    user_apps_path = os.path.join(get_users_dir(), user["username"], "apps")

    try:
        filename = secure_filename(filename)
        folder_name = os.path.splitext(filename)[0]
        file_path = os.path.join(user_apps_path, folder_name, filename)
        folder_path = os.path.join(user_apps_path, folder_name)

        if not file_path.startswith(user_apps_path):
            current_app.logger.warning("Security warning: Attempted path traversal")
            return False

        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)

        return True
    except OSError as e:
        current_app.logger.error(f"couldn't delete file: {e}")
        return False


def get_all_users() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM json_data")
    return [json.loads(row[0]) for row in cursor.fetchall()]


def get_user_render_port(username: str) -> int:
    base_port = int(current_app.config.get("PIXLET_RENDER_PORT1")) or 5100
    users = get_all_users()
    for i in range(len(users)):
        if users[i]["username"] == username:
            current_app.logger.debug(f"got port {i} for {username}")
            return base_port + i


def get_is_app_schedule_active(app: Dict[str, Any]) -> bool:
    # Check if the app should be displayed based on start and end times and active days
    current_time = datetime.now().time()
    current_day = datetime.now().strftime("%A").lower()
    start_time_str = app.get("start_time", "00:00") or "00:00"
    end_time_str = app.get("end_time", "23:59") or "23:59"
    start_time = datetime.strptime(start_time_str, "%H:%M").time()
    end_time = datetime.strptime(end_time_str, "%H:%M").time()
    active_days = app.get(
        "days",
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
    )
    if not active_days:
        active_days = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]

    schedule_active = False
    if (
        (start_time <= current_time <= end_time)
        or (
            start_time > end_time
            and (current_time >= start_time or current_time <= end_time)
        )
    ) and current_day in active_days:
        schedule_active = True

    return schedule_active


def get_device_by_name(user: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for device in user.get("devices", {}).values():
        if device.get("name") == name:
            return device
    return None


def get_device_webp_dir(device_id: str, create: bool = True) -> str:
    path = os.path.join(os.getcwd(), "tronbyt_server", "webp", device_id)
    if not os.path.exists(path) and create:
        os.makedirs(path)
    return path


def get_device_by_id(device_id: str) -> Optional[Dict[str, Any]]:
    for user in get_all_users():
        device = user.get("devices", {}).get(device_id)
        if device:
            return device
    return None


def get_user_by_device_id(device_id: str) -> Optional[Dict[str, Any]]:
    for user in get_all_users():
        device = user.get("devices", {}).get(device_id)
        if device:
            return user
    return None


def generate_firmware(
    label: str, url: str, ap: str, pw: str, gen2: bool, swap_colors: bool
) -> Dict[str, Union[str, int]]:
    # Usage
    if gen2:
        file_path = "firmware/gen2.bin"
        new_path = f"firmware/gen2_{label}.bin"
    elif swap_colors:
        file_path = "firmware/gen1_swap.bin"
        new_path = f"firmware/gen1_swap_{label}.bin"
    else:
        file_path = "firmware/gen1.bin"
        new_path = f"firmware/gen1_{label}.bin"

    if not os.path.exists(file_path):
        return {"error": f"Firmware file {file_path} not found."}

    shutil.copy(file_path, new_path)

    dict = {
        "XplaceholderWIFISSID________________________________": ap,
        "XplaceholderWIFIPASSWORD____________________________": pw,
        "XplaceholderREMOTEURL_________________________________________________________________________________________": url,
    }
    bytes_written = None
    with open(new_path, "r+b") as f:
        # Read the binary file into memory
        content = f.read()

        for old_string, new_string in dict.items():
            # Ensure the new string is not longer than the original
            if len(new_string) > len(old_string):
                return {
                    "error": "Replacement string cannot be longer than the original string."
                }

            # Find the position of the old string
            position = content.find(old_string.encode("ascii") + b"\x00")
            if position == -1:
                return {"error": f"String '{old_string}' not found in the binary."}

            # Create the new string, null-terminated, and padded to match the original length
            padded_new_string = new_string + "\x00"
            padded_new_string = padded_new_string.ljust(
                len(old_string) + 1, "\x00"
            )  # Add padding if needed

            # Replace the string
            f.seek(position)
            bytes_written = f.write(padded_new_string.encode("ascii"))
    if bytes_written:
        # run the correct checksum/hash script
        result = subprocess.run(
            [
                "python3",
                "firmware/correct_firmware_esptool.py",
                f"{new_path}",
            ],
            capture_output=True,
            text=True,
        )
        current_app.logger.debug(result.stdout)
        current_app.logger.debug(result.stderr)
        return {"file_path": os.path.join(os.getcwd(), new_path)}
    else:
        return {"error": "no bytes written"}


def add_pushed_app(device_id: str, path: str) -> None:
    user = get_user_by_device_id(device_id)
    if user is None:
        return
    # Get the base name of the file
    filename = os.path.basename(path)
    # Remove the extension
    installation_id = os.path.splitext(filename)[0]
    apps = user["devices"][device_id].setdefault("apps", {})
    if installation_id in apps:
        # already in there
        return
    app = {
        "iname": installation_id,
        "name": "pushed",
        "uinterval": 10,
        "display_time": 0,
        "notes": "",
        "enabled": True,
        "pushed": 1,
        "order": len(apps),
    }
    apps[installation_id] = app
    save_user(user)
