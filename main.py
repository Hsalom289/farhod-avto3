# -*- coding: utf-8 -*-
"""
Forward bot (har siklda eski + yangi postlarni aylantiradi; guruhlarga va shaxsiylarga)
- Ish vaqti: 10:00–23:00 (Asia/Samarkand, UTC+5)
- SOURCE kanalni har sikl o'qib, post/albomlarni guruhlarga va TARGET_USERS dagi shaxslarga forward qiladi
"""

import asyncio
import logging
import os
from datetime import datetime, time as dt_time, timezone, timedelta
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    PhoneNumberBannedError,
    SessionRevokedError,
    RPCError,
)
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import (
    InputPeerEmpty,
    Channel, Chat, ChannelForbidden, ChatForbidden
)

# ====================== SETTINGS ======================
POST_DELAY_SECONDS = 8            # har post/albomdan keyin kutish
GROUP_DELAY_SECONDS = 30          # har targetdan keyin kutish
CHECK_EVERY_SECONDS = 20          # sikllar orasida kutish
REPLAY_LAST_N_GROUPS = None       # None -> hammasi; masalan 300 qo'ysangiz, faqat oxirgi 300 guruh

# Telegram API credentials (o'zingizniki bilan to'ldiring)
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

# Manba kanal
SOURCE_CHANNEL = "https://t.me/Navoiy_uy_barcha_elonlar_bazasi"

# Guruhlar (majburiy qo'shiladigan username’lar)
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

# Shaxsiy (private) targetlar — username yoki user ID
TARGET_USERS = [
    # "@username1",
    # 123456789,
]

# Hech qachon yuborilmaydigan targetlar (kanal/guruh)
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

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# ====================== UTILITIES ======================
def normalize_channel(value: str) -> str:
    """URL yoki @username ni username/path ga keltiradi."""
    value = value.strip()
    if value.startswith("http"):
        path = urlparse(value).path.strip("/")
        return path
    return value.lstrip("@")

def is_working_time() -> bool:
    """Ish vaqti: 10:00–23:00 (Asia/Samarkand)."""
    uz_timezone = timezone(timedelta(hours=5))
    now = datetime.now(uz_timezone).time()
    return dt_time(10, 0) <= now <= dt_time(23, 0)

async def ensure_connection(client: TelegramClient) -> bool:
    """Client uzilgan bo'lsa qayta ulaydi."""
    try:
        if not client.is_connected():
            logging.info("Client disconnected. Reconnecting...")
            await client.connect()
            logging.info("Reconnected.")
        return True
    except Exception as e:
        logging.error(f"Reconnect failed: {e}")
        return False

async def resolve_excluded_ids(client: TelegramClient) -> set:
    """
    EXCLUDED_TARGETS va SOURCE_CHANNEL'ni IDlarga resolve qiladi.
    Private `+invite` bo'lsa tashlab yuboriladi.
    """
    excluded_ids = set()
    for item in EXCLUDED_TARGETS + [SOURCE_CHANNEL]:
        try:
            username_or_path = normalize_channel(item)
            if username_or_path.startswith("+"):  # private invite
                logging.warning(f"Skip exclude (private invite not joined): {item}")
                continue
            ent = await client.get_entity(username_or_path)
            excluded_ids.add(ent.id)
            title = getattr(ent, "title", getattr(ent, "username", str(ent.id)))
            logging.info(f"[Exclude] {title} (id={ent.id})")
        except Exception as e:
            logging.warning(f"Could not resolve excluded: {item} -> {e}")
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
        key = msg.grouped_id if getattr(msg, "grouped_id", None) else msg.id
        grouped.setdefault(key, []).append(msg)

    out = []
    for key in sorted(grouped.keys()):
        out.append(sorted(grouped[key], key=lambda m: m.id))
    return out

async def get_all_posts_grouped(client: TelegramClient, limit=100000):
    """
    SOURCE kanalidan barcha xabarlarni olib, albom/oddiy bo'yicha guruhlab qaytaradi.
    from_peer sifatida **entity** qaytaradi.
    """
    if not await ensure_connection(client):
        return [], None

    source_username = normalize_channel(SOURCE_CHANNEL)
    source_ent = await client.get_entity(source_username)
    messages = await client.get_messages(source_ent, limit=limit)  # entity bilan
    grouped = group_messages(messages)

    # Floodni kamaytirish: faqat oxirgi N ta guruhni aylantirish
    if isinstance(REPLAY_LAST_N_GROUPS, int) and REPLAY_LAST_N_GROUPS > 0:
        grouped = grouped[-REPLAY_LAST_N_GROUPS:]

    logging.info(f"Fetched {len(grouped)} post groups from source.")
    return grouped, source_ent

def _is_valid_group(ent) -> bool:
    """
    Faqat guruhlarni qoldiramiz:
    - Channel with megagroup=True  (supergroup)
    - Chat (basic group)
    Kanal (broadcast), user/bot, forbidden turlari chiqarib tashlanadi.
    """
    if isinstance(ent, (ChannelForbidden, ChatForbidden)):
        return False
    if isinstance(ent, Channel):
        return bool(getattr(ent, 'megagroup', False))
    if isinstance(ent, Chat):
        return True
    return False

async def get_admin_groups(client: TelegramClient, excluded_ids: set):
    """
    Siz admin bo'lgan guruhlar + ADDITIONAL_GROUPS
    (kanallar/broadcast chetlanadi), excluded_ids bilan filtrlanadi.
    """
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
    seen = set()

    # Men admin bo'lgan chatlar
    for ent in dialogs.chats:
        try:
            if ent.id in excluded_ids:
                continue
            if not _is_valid_group(ent):
                continue
            if hasattr(ent, 'admin_rights') and ent.admin_rights:
                if ent.id not in seen:
                    admin_groups.append(ent)
                    seen.add(ent.id)
                    logging.info(f"Admin target: {getattr(ent,'title',ent.id)} (id={ent.id})")
        except Exception:
            continue

    # Majburiy qo'shiladiganlar (faqat guruh bo‘lsa)
    for username in ADDITIONAL_GROUPS:
        try:
            ent = await client.get_entity(username)
            if ent.id in excluded_ids:
                continue
            if not _is_valid_group(ent):
                logging.info(f"Skip additional (not a group): {username}")
                continue
            if ent.id not in seen:
                admin_groups.append(ent)
                seen.add(ent.id)
                logging.info(f"Added additional target: {getattr(ent,'title',username)} (id={ent.id})")
        except Exception as e:
            logging.error(f"Error adding target {username}: {e}")

    return admin_groups

# ====================== MAIN LOOP ======================
async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    # Session holati
    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logging.warning("Session invalid, removing...")
                os.remove(session_file)
        except Exception:
            logging.warning("Session file corrupted, removing...")
            os.remove(session_file)

    # Start (login)
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
        logging.error(f"Error connecting: {e}")
        return

    try:
        excluded_ids = await resolve_excluded_ids(client)
        logging.info(f"Excluded IDs count: {len(excluded_ids)}")

        while True:
            # Ish vaqti nazorati
            if not is_working_time():
                logging.info("Outside working hours. Waiting...")
                await asyncio.sleep(60)
                continue

            # 1) SOURCE (eski + yangi) guruhlar
            source_groups, source_ent = await get_all_posts_grouped(client)
            if not source_groups or not source_ent:
                logging.info("No source groups found. Waiting...")
                await asyncio.sleep(CHECK_EVERY_SECONDS)
                continue

            # 2) Guruh targetlar (admin bo'lganlar + qo'shimcha)
            admin_groups = await get_admin_groups(client, excluded_ids)

            # 3) GURUH(LAR)GA forward
            for group in admin_groups:
                gid = getattr(group, "id", None)
                if gid in excluded_ids:
                    logging.info(f"Skipped excluded target at send loop: id={gid}")
                    continue

                # Targetni InputPeerga aylantiramiz (Invalid Peer oldini oladi)
                try:
                    target_peer = await client.get_input_entity(group)
                except Exception as e:
                    logging.error(f"Target resolve failed (skip): {getattr(group,'title',gid)} -> {e}")
                    continue

                title = getattr(group, "title", getattr(group, "username", str(gid)))
                logging.info(f"⬇️ Forwarding ALL (old+new) to GROUP {title} (id={gid})...")

                for msg_group in source_groups:
                    message_ids = [m.id for m in msg_group if getattr(m, "id", None)]
                    if not message_ids:
                        continue
                    try:
                        if not await ensure_connection(client):
                            continue
                        await client.forward_messages(
                            entity=target_peer,   # InputPeer (target group)
                            messages=message_ids,
                            from_peer=source_ent  # Entity (source channel)
                        )
                        logging.info(f"✅ Forwarded to GROUP {title}: {message_ids}")
                        await asyncio.sleep(POST_DELAY_SECONDS)

                    except FloodWaitError as e:
                        logging.warning(f"FloodWait (group): wait {e.seconds}s")
                        await asyncio.sleep(e.seconds + 5)
                    except RPCError as e:
                        logging.error(f"RPCError to GROUP {title}: {e}")
                        break
                    except Exception as e:
                        logging.error(f"Error forwarding to GROUP {title}: {e}")
                        continue

                logging.info(f"✅ Done with GROUP {title}. Waiting {GROUP_DELAY_SECONDS} sec.")
                await asyncio.sleep(GROUP_DELAY_SECONDS)

            # 4) SHAXSIY (PRIVATE) foydalanuvchilarga forward (faqat TARGET_USERS da borlar)
            for u in TARGET_USERS:
                try:
                    user_peer = await client.get_input_entity(u)
                except Exception as e:
                    logging.error(f"Private target resolve failed (skip): {u} -> {e}")
                    continue

                title = str(getattr(user_peer, "user_id", u))
                logging.info(f"⬇️ Forwarding ALL (old+new) to PRIVATE {title} ...")

                for msg_group in source_groups:
                    message_ids = [m.id for m in msg_group if getattr(m, "id", None)]
                    if not message_ids:
                        continue
                    try:
                        if not await ensure_connection(client):
                            continue
                        await client.forward_messages(
                            entity=user_peer,     # private user
                            messages=message_ids,
                            from_peer=source_ent
                        )
                        logging.info(f"✅ Forwarded to PRIVATE {title}: {message_ids}")
                        await asyncio.sleep(POST_DELAY_SECONDS)

                    except FloodWaitError as e:
                        logging.warning(f"FloodWait (private): wait {e.seconds}s")
                        await asyncio.sleep(e.seconds + 5)
                    except RPCError as e:
                        logging.error(f"RPCError to PRIVATE {title}: {e}")
                        break
                    except Exception as e:
                        logging.error(f"Error forwarding to PRIVATE {title}: {e}")
                        continue

                logging.info(f"✅ Done with PRIVATE {title}. Waiting {GROUP_DELAY_SECONDS} sec.")
                await asyncio.sleep(GROUP_DELAY_SECONDS)

            logging.info(f"🔄 Cycle finished. Re-reading source in {CHECK_EVERY_SECONDS}s ...")
            await asyncio.sleep(CHECK_EVERY_SECONDS)

    except Exception as e:
        logging.error(f"Main loop error: {e}")
        await asyncio.sleep(60)
    finally:
        if client.is_connected():
            await client.disconnect()
            logging.info("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
