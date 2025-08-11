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
POST_DELAY_SECONDS = 8       # Har postdan keyin kutish
GROUP_DELAY_SECONDS = 30     # Guruhdan keyin kutish

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# Telegram API credentials
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

# ❗️HECH QACHON TARQATILMAYDIGAN targetlar (invite link yoki username)
EXCLUDED_TARGETS = [
    "https://t.me/+lHIK53jRNKM3NDky",  # siz bergan havola
    # "username_yoki_boshqa_linkni_bu_yerga_qo'shishingiz_mumkin"
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
    """
    excluded_ids = set()
    for item in EXCLUDED_TARGETS:
        try:
            # Telethon get_entity() invite linkni ham, username’ni ham qabul qiladi
            ent = await client.get_entity(item)
            excluded_ids.add(ent.id)
            title = getattr(ent, "title", getattr(ent, "username", str(ent.id)))
            logging.info(f"Excluded resolved: {title} (id={ent.id})")
        except Exception as e:
            logging.warning(f"Could not resolve EXCLUDED target: {item} -> {e}")
    return excluded_ids

async def get_admin_groups(client, excluded_ids):
    """
    Dialoglardan admin bo'lganlarini + ADDITIONAL_GROUPS dagilarni yig‘adi,
    so‘ngra excluded_ids bo‘yicha filtrlaydi.
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
                # admin_rights mavjud bo‘lsa (kanal/guruh bo‘lishi mumkin)
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

        # ID bo‘yicha dublikatlarni olib tashlash
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

async def get_all_source_posts(client, limit=100000):
    """Manbadan eng eski postdan boshlab hammasini olish"""
    try:
        if not await ensure_connection(client):
            return []

        channel_username = normalize_channel(SOURCE_CHANNEL)
        channel = await client.get_entity(channel_username)
        messages = await client.get_messages(channel, limit=limit)

        if not messages:
            logging.info("No posts found.")
            return []

        grouped_posts = {}
        for msg in messages:
            if msg.grouped_id:
                grouped_posts.setdefault(msg.grouped_id, []).append(msg)
            else:
                grouped_posts[msg.id] = [msg]

        sorted_groups = []
        for group_id in sorted(grouped_posts.keys()):
            sorted_groups.append(grouped_posts[group_id])

        logging.info(f"Fetched {len(sorted_groups)} post groups from source.")
        return sorted_groups
    except FloodWaitError as e:
        logging.warning(f"FloodWaitError in get_all_source_posts: Waiting {e.seconds} seconds")
        await asyncio.sleep(e.seconds + 5)
        return []
    except Exception as e:
        logging.error(f"Error fetching posts: {str(e)}")
        return []

async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logging.warning("Session invalid, removing...")
                os.remove(session_file)
        except Exception:
            logging.warning("Session file corrupted, removing...")
            os.remove(session_file)

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
        # ❗️Avval exclusion’larni ID ga aylantirib olamiz
        excluded_ids = await resolve_excluded_ids(client)
        logging.info(f"Excluded IDs: {excluded_ids if excluded_ids else 'none'}")

        source_posts = await get_all_source_posts(client)

        while True:
            if not is_working_time():
                logging.info("Outside working hours. Waiting...")
                await asyncio.sleep(60)
                continue

            admin_groups = await get_admin_groups(client, excluded_ids)
            if not admin_groups:
                logging.warning("No admin groups found. Waiting...")
                await asyncio.sleep(60)
                continue

            for group in admin_groups:
                title = getattr(group, "title", getattr(group, "username", str(group.id)))
                logging.info(f"⬇️ Forwarding to {title} (id={group.id})...")

                for group_messages in source_posts:
                    message_ids = [msg.id for msg in group_messages if msg.id]

                    if message_ids:
                        try:
                            if not await ensure_connection(client):
                                continue
                            await client.forward_messages(
                                group,  # entity’ning o‘zini berish xavfsizroq
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

            logging.info("🔄 Cycle finished. Starting from first post again.")

    except Exception as e:
        logging.error(f"Main loop error: {str(e)}")
        await asyncio.sleep(60)
    finally:
        if client.is_connected():
            await client.disconnect()
            logging.info("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
