from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneNumberBannedError, SessionRevokedError
import asyncio
from datetime import datetime, time as dt_time, timezone, timedelta
import logging
import os
from urllib.parse import urlparse

# -------------------- Logging --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# -------------------- Telegram API --------------------
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

# -------------------- Manba kanal (faqat username) --------------------
NAVOIY_UY_JOY_CHANNEL_USERNAME = "Navoiy_uy_barcha_elonlar_bazasi"

# -------------------- Qo‘shimcha guruhlar --------------------
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

# -------------------- Exclude ro‘yxati --------------------
EXCLUDED_LINKS_OR_USERNAMES = [
    "https://t.me/Navoiy_uy_barcha_elonlar_bazasi",
    "https://t.me/Navoiy_1_xona_kvartira",
    "https://t.me/Navoiy_2_xona_kvartira",
    "https://t.me/Navoiy_3_4_5_xona_kvartira",
    "https://t.me/Navoiy_hovli_katedj_dacha",
    "https://t.me/Navoiy_ijaraga_kv_uyFarxod",
    "https://t.me/Navoiy_uy_joy_kv_barcha_elonlar",
    "https://t.me/+PMAqGE3CAmJmNjcy",
    "https://t.me/+lHIK53jRNKM3NDky",
    "https://t.me/Navoiy_uyjoy_savdo",
    "https://t.me/+UwOCqvDQ4VpkNWUy",
    "https://t.me/UyTopdim_Navoiy",
]

# -------------------- Ish vaqti --------------------
def is_working_time():
    uz_timezone = timezone(timedelta(hours=5))
    now = datetime.now(uz_timezone).time()
    return dt_time(10, 0) <= now <= dt_time(23, 0)

# -------------------- Linkdan username ajratish --------------------
def extract_username_from_link(link: str):
    if not link:
        return None
    if link.startswith("http"):
        try:
            parsed = urlparse(link)
            if parsed.netloc not in ("t.me", "telegram.me"):
                return None
            path = parsed.path.strip("/")
            if not path or path.startswith("+"):
                return None
            return path
        except Exception:
            return None
    return link.replace("@", "").strip()

# -------------------- Ulanish --------------------
async def ensure_connection(client):
    try:
        if not client.is_connected():
            logging.info("Client disconnected. Reconnecting...")
            await client.connect()
            logging.info("Reconnected.")
        return True
    except Exception as e:
        logging.error(f"Reconnect failed: {e}")
        return False

# -------------------- Exclude setlarini tayyorlash --------------------
async def build_excluded_sets(client):
    excluded_usernames = set()
    excluded_ids = set()

    for item in EXCLUDED_LINKS_OR_USERNAMES:
        username = extract_username_from_link(item)
        if username:
            excluded_usernames.add(username.lower())
            try:
                ent = await client.get_entity(username)
                excluded_ids.add(int(ent.id))
            except Exception:
                pass
            continue
        if item.startswith("http"):
            try:
                ent = await client.get_entity(item)
                excluded_ids.add(int(ent.id))
            except Exception:
                pass

    logging.info(f"Excluded usernames: {excluded_usernames}")
    logging.info(f"Excluded IDs: {excluded_ids}")
    return excluded_usernames, excluded_ids

# -------------------- Admin guruhlar --------------------
async def get_admin_groups(client, excluded_usernames, excluded_ids):
    try:
        if not await ensure_connection(client):
            return []

        dialogs = await client(GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=200,
            hash=0
        ))

        admin_groups = []
        for dialog in dialogs.chats:
            if hasattr(dialog, 'admin_rights') and dialog.admin_rights:
                chat_id = int(getattr(dialog, "id", 0))
                chat_username = getattr(dialog, "username", None)
                if chat_id in excluded_ids:
                    logging.info(f"⛔ Excluded by ID: {getattr(dialog, 'title', chat_username)}")
                    continue
                if chat_username and chat_username.lower() in excluded_usernames:
                    logging.info(f"⛔ Excluded by username: {getattr(dialog, 'title', chat_username)}")
                    continue
                admin_groups.append(dialog)
                logging.info(f"✅ Added admin group: {getattr(dialog, 'title', chat_username)}")

        # Qo‘shimcha guruhlar
        for group_ref in ADDITIONAL_GROUPS:
            try:
                ref_username = extract_username_from_link(group_ref)
                if ref_username and ref_username.lower() in excluded_usernames:
                    continue
                group_ent = await client.get_entity(group_ref)
                if int(group_ent.id) in excluded_ids:
                    continue
                admin_groups.append(group_ent)
            except Exception as e:
                logging.error(f"Error adding additional group {group_ref}: {e}")

        return admin_groups
    except FloodWaitError as e:
        logging.warning(f"Flood wait: {e.seconds}s")
        await asyncio.sleep(e.seconds + 5)
        return []

# -------------------- Postlarni olish --------------------
async def get_navoiy_uy_joy_posts(client, min_id=0, limit=100000):
    try:
        if not await ensure_connection(client):
            return [], min_id

        channel = await client.get_entity(NAVOIY_UY_JOY_CHANNEL_USERNAME)
        messages = await client.get_messages(channel, limit=limit, min_id=min_id)

        if not messages:
            return [], min_id

        grouped_posts = {}
        for msg in messages:
            key = msg.grouped_id if msg.grouped_id else msg.id
            grouped_posts.setdefault(key, []).append(msg)

        sorted_groups = [grouped_posts[k] for k in sorted(grouped_posts.keys())]
        next_min_id = max(m.id for m in messages) if messages else min_id
        return sorted_groups, next_min_id
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 5)
        return [], min_id

# -------------------- Main --------------------
async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                os.remove(session_file)
        except Exception:
            os.remove(session_file)

    try:
        await client.start(phone)
        logging.info("Connected to Telegram!")
    except Exception as e:
        logging.error(f"Connection error: {e}")
        return

    excluded_usernames, excluded_ids = await build_excluded_sets(client)

    navoiy_uy_joy_posts, next_min_id = await get_navoiy_uy_joy_posts(client, min_id=0)

    while True:
        if not is_working_time():
            await asyncio.sleep(60)
            continue

        admin_groups = await get_admin_groups(client, excluded_usernames, excluded_ids)
        if not admin_groups:
            await asyncio.sleep(60)
            continue

        for group in admin_groups:
            for group_messages in navoiy_uy_joy_posts:
                message_ids = [msg.id for msg in group_messages if msg]
                if message_ids:
                    try:
                        await client.forward_messages(group.id, message_ids, NAVOIY_UY_JOY_CHANNEL_USERNAME)
                        await asyncio.sleep(10)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 5)
                    except Exception:
                        continue
            await asyncio.sleep(30)

        navoiy_uy_joy_posts, next_min_id = await get_navoiy_uy_joy_posts(client, min_id=next_min_id)
        if not navoiy_uy_joy_posts:
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
