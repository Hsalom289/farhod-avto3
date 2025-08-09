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

# TARGET groups
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
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

async def get_admin_groups(client):
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
                admin_groups.append(dialog)
                logging.info(f"Found admin group: {dialog.title}")

        for group_username in ADDITIONAL_GROUPS:
            try:
                group = await client.get_entity(group_username)
                admin_groups.append(group)
                logging.info(f"Added additional group: {group.title}")
            except Exception as e:
                logging.error(f"Error adding group {group_username}: {str(e)}")

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
        source_posts = await get_all_source_posts(client)

        while True:
            if not is_working_time():
                logging.info("Outside working hours. Waiting...")
                await asyncio.sleep(60)
                continue

            admin_groups = await get_admin_groups(client)
            if not admin_groups:
                logging.warning("No admin groups found. Waiting...")
                await asyncio.sleep(60)
                continue

            for group in admin_groups:
                logging.info(f"⬇️ Forwarding to {group.title}...")

                for group_messages in source_posts:
                    message_ids = [msg.id for msg in group_messages if msg.id]

                    if message_ids:
                        try:
                            if not await ensure_connection(client):
                                continue
                            await client.forward_messages(
                                group.id,
                                message_ids,
                                normalize_channel(SOURCE_CHANNEL)
                            )
                            logging.info(f"✅ Forwarded to {group.title}: {message_ids}")
                            await asyncio.sleep(POST_DELAY_SECONDS)
                        except FloodWaitError as e:
                            logging.warning(f"FloodWait: Waiting {e.seconds} seconds")
                            await asyncio.sleep(e.seconds + 5)
                        except Exception as e:
                            logging.error(f"Error forwarding: {str(e)}")
                            continue

                logging.info(f"✅ Done with {group.title}. Waiting {GROUP_DELAY_SECONDS} sec.")
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
