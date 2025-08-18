from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneNumberBannedError, SessionRevokedError
import asyncio
from datetime import datetime, time as dt_time, timezone, timedelta
import logging
import os
from urllib.parse import urlparse

# === SETTINGS ===
POST_DELAY_SECONDS = 8            # Har postdan keyin kutish
GROUP_DELAY_SECONDS = 30          # Guruhdan keyin kutish
CHECK_NEW_POSTS_EVERY = 30        # Yangi postlarni tekshirish oraliği (sekund)
SEND_HISTORY_ON_FIRST_RUN = False # True qilsangiz birinchi ishga tushganda tarix ham ketadi

LAST_SEEN_FILE = "last_seen_id.txt"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# Telegram API credentials (o'zingizniki)
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

# SOURCE channel
SOURCE_CHANNEL = "https://t.me/Navoiy_uy_barcha_elonlar_bazasi"

# TARGET groups (majburiy qo‘shiladiganlar)
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

# ❗️UMUMAN TARQATILMAYDIGAN targetlar (siz berganlar + xohlasangiz yana qo‘shing)
EXCLUDED_TARGETS = [
    "https://t.me/Navoiy_uy_barcha_elonlar_bazasi",
    "https://t.me/Navoiy_uy_joy_kv_barcha_elonlar",
    "https://t.me/+6e1vw3QlFLg1Zjcy",
    "https://t.me/+N9nRAUGPEl43YmFi",
    "https://t.me/Navoiy_1_xona_kvartira",
    "https://t.me/Navoiy_2_xona_kvartira",
    "https://t.me/Navoiy_3_4_5_xona_kvartira",
    "https://t.me/Navoiy_ijaraga_kv_uy",
    "https://t.me/Navoiy_hovli_katedj_dacha",
]

def normalize_channel(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        path = urlparse(value).path.strip("/")
        return path
    return value.lstrip("@")

def is_working_time():
    uz_timezone = timezone(timedelta(hours=5))
    now = datetime.now(uz_timezone).time()
    return dt_time(10, 0) <= now <= dt_time(23, 0)

async def ensure_connection(client):
    try:
        if not client.is_connected():
            logging.info("Client is disconnected. Attempting to reconnect...")
            await client.connect()
            logging.info("Reconnected successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to reconnect: {str(e)}")
        return False

async def resolve_excluded_ids(client):
    """
    EXCLUDED_TARGETS ichidagi link/username’larni entity ID ga aylantirib, set qaytaradi.
    Join bo‘lmagan private linklar resolve bo‘lmasligi mumkin – log’da ko‘rasiz.
    """
    excluded_ids = set()
    for item in EXCLUDED_TARGETS:
        try:
            ent = await client.get_entity(item)
            excluded_ids.add(ent.id)
            title = getattr(ent, "title", getattr(ent, "username", str(ent.id)))
            logging.info(f"Excluded resolved: {title} (id={ent.id})")
        except Exception as e:
            logging.warning(f"Could not resolve EXCLUDED target: {item} -> {e}")
    return excluded_ids

def group_messages(messages):
    """
    Xabarlar ro‘yxatini albom (grouped_id) bo‘yicha birlashtiradi.
    Natija: [ [msg,msg,...], [msg], ... ] eski->yangi tartibda.
    """
    grouped = {}
    for msg in messages:
        if not getattr(msg, "id", None):
            continue
        key = msg.grouped_id if msg.grouped_id else msg.id
        grouped.setdefault(key, []).append(msg)

    out = []
    for key in sorted(grouped.keys()):
        out.append(sorted(grouped[key], key=lambda m: m.id))
    return out

async def get_latest_message_id(client):
    """
    SOURCE ichidagi eng oxirgi xabar ID.
    """
    if not await ensure_connection(client):
        return 0
    channel_username = normalize_channel(SOURCE_CHANNEL)
    channel = await client.get_entity(channel_username)
    latest = await client.get_messages(channel, limit=1)
    if latest and latest[0]:
        return latest[0].id
    return 0

def load_last_seen():
    if os.path.exists(LAST_SEEN_FILE):
        try:
            with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
                return int(f.read().strip() or "0")
        except Exception:
            return 0
    return None  # fayl yo‘q

def save_last_seen(value: int):
    try:
        with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
            f.write(str(value))
    except Exception as e:
        logging.warning(f"Could not save last_seen_id: {e}")

async def fetch_new_groups_since(client, since_id):
    """
    since_id dan katta bo‘lgan YANGI xabarlarni olib, albom bo‘yicha guruhlab qaytaradi.
    """
    try:
        if not await ensure_connection(client):
            return [], since_id

        channel_username = normalize_channel(SOURCE_CHANNEL)
        channel = await client.get_entity(channel_username)

        # iter_messages: min_id=since_id => id > since_id (yangi->eski oqimda keladi)
        new_msgs = []
        async for msg in client.iter_messages(channel, min_id=since_id):
            if getattr(msg, "id", None):
                new_msgs.append(msg)

        if not new_msgs:
            return [], since_id

        new_groups = group_messages(new_msgs)
        new_last_seen = max(m.id for m in new_msgs)
        return new_groups, new_last_seen

    except FloodWaitError as e:
        logging.warning(f"FloodWaitError in fetch_new_groups_since: Waiting {e.seconds} seconds")
        await asyncio.sleep(e.seconds + 5)
        return [], since_id
    except Exception as e:
        logging.error(f"Error fetching new posts: {str(e)}")
        return [], since_id

async def get_admin_groups(client, excluded_ids):
    """
    Dialoglardan admin bo‘lganlarni + ADDITIONAL_GROUPS dagilarni yig‘adi, excluded_ids bo‘yicha filtrlaydi.
    """
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
        # Admin bo‘lgan chatlar
        for dialog in dialogs.chats:
            try:
                if hasattr(dialog, 'admin_rights') and dialog.admin_rights:
                    if dialog.id not in excluded_ids:
                        admin_groups.append(dialog)
                        logging.info(f"Found admin target: {dialog.title} (id={dialog.id})")
                    else:
                        logging.info(f"Skipped excluded (admin list): {dialog.title} (id={dialog.id})")
            except Exception:
                continue

        # Majburiy qo‘shiladiganlar
        for group_username in ADDITIONAL_GROUPS:
            try:
                group = await client.get_entity(group_username)
                if group.id not in excluded_ids:
                    admin_groups.append(group)
                    logging.info(f"Added additional target: {getattr(group,'title',group_username)} (id={group.id})")
                else:
                    logging.info(f"Skipped excluded (additional): {getattr(group,'title',group_username)} (id={group.id})")
            except Exception as e:
                logging.error(f"Error adding target {group_username}: {str(e)}")

        # Dublikatlarni ID bo‘yicha yo‘qotish
        uniq = {}
        for g in admin_groups:
            uniq[g.id] = g
        admin_groups = list(uniq.values())

        return admin_groups

    except FloodWaitError as e:
        logging.warning(f"FloodWaitError in get_admin_groups: Waiting {e.seconds} seconds")
        await asyncio.sleep(e.seconds + 5)
        return []
    except Exception as e:
        logging.error(f"Error fetching groups: {str(e)}")
        return []

async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    # Session tekshiruvi
    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logging.warning("Session invalid, removing...")
                os.remove(session_file)
        except Exception:
            logging.warning("Session file corrupted, removing...")
            os.remove(session_file)

    # Start
    try:
        await client.start(phone)
        logging.info("Successfully connected to Telegram!")
    except SessionPasswordNeededError:
        logging.error("Two-factor authentication required.")
        return
    except PhoneNumberBannedError:
        logging.error("Phone number is banned.")
        return
    except SessionRevokedError:
        logging.error("Session revoked.")
        return
    except Exception as e:
        logging.error(f"Error connecting: {str(e)}")
        return

    try:
        excluded_ids = await resolve_excluded_ids(client)
        logging.info(f"Excluded IDs: {excluded_ids if excluded_ids else 'none'}")

        # last_seen_id ni aniqlash (fayldan yoki konfiguratsiyaga ko‘ra)
        stored = load_last_seen()
        if stored is None:
            if SEND_HISTORY_ON_FIRST_RUN:
                last_seen_id = 0
                logging.info("No last_seen file. Will send HISTORY on first run.")
            else:
                last_seen_id = await get_latest_message_id(client)
                logging.info("No last_seen file. Will start from current latest (no history).")
        else:
            last_seen_id = stored
            logging.info(f"Loaded last_seen_id from file: {last_seen_id}")

        logging.info(f"Starting with last_seen_id={last_seen_id}")

        while True:
            if not is_working_time():
                logging.info("Outside working hours. Waiting...")
                await asyncio.sleep(60)
                continue

            # 1) Yangi postlar bormi?
            new_groups, new_last_seen = await fetch_new_groups_since(client, last_seen_id)
            if not new_groups:
                await asyncio.sleep(CHECK_NEW_POSTS_EVERY)
                continue

            # 2) Targetlar
            admin_groups = await get_admin_groups(client, excluded_ids)
            if not admin_groups:
                logging.warning("No admin groups found. Waiting...")
                await asyncio.sleep(60)
                continue

            # 3) Yangi postlarni hamma targetlarga forward qilish
            for group in admin_groups:
                # 🔒 qo‘shimcha xavfsizlik: excluded targetni send loopda ham tekshiramiz
                if getattr(group, "id", None) in excluded_ids:
                    logging.info(f"Skipped excluded target at send loop: id={group.id}")
                    continue

                title = getattr(group, "title", getattr(group, "username", str(group.id)))
                logging.info(f"⬇️ Forwarding NEW posts to {title} (id={group.id})...")

                for group_messages in new_groups:
                    message_ids = [msg.id for msg in group_messages if getattr(msg, "id", None)]
                    if not message_ids:
                        continue
                    try:
                        if not await ensure_connection(client):
                            continue
                        await client.forward_messages(
                            group,  # entity obyektini berish xavfsiz
                            message_ids,
                            normalize_channel(SOURCE_CHANNEL)
                        )
                        logging.info(f"✅ Forwarded to {title}: {message_ids}")
                        await asyncio.sleep(POST_DELAY_SECONDS)
                    except FloodWaitError as e:
                        logging.warning(f"FloodWait: Waiting {e.seconds} seconds")
                        await asyncio.sleep(e.seconds + 5)
                    except Exception as e:
                        logging.error(f"Error forwarding to {title}: {str(e)}")
                        continue

                logging.info(f"✅ Done with {title}. Waiting {GROUP_DELAY_SECONDS} sec.")
                await asyncio.sleep(GROUP_DELAY_SECONDS)

            # 4) Muvaffaqiyatli tarqatilgach, last_seen_id ni yangilaymiz va saqlaymiz
            last_seen_id = max(last_seen_id, new_last_seen)
            save_last_seen(last_seen_id)
            logging.info(f"🔄 Cycle finished. Updated last_seen_id={last_seen_id}. Checking again soon...")

    except Exception as e:
        logging.error(f"Main loop error: {str(e)}")
        await asyncio.sleep(60)
    finally:
        if client.is_connected():
            await client.disconnect()
            logging.info("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(main())


