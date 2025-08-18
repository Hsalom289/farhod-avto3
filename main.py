# -*- coding: utf-8 -*-
"""
Forward bot (har siklda eski + yangi postlarni aylantiradi)
- Ish vaqti: 10:00–23:00 (Asia/Samarkand, UTC+5)
- Har siklda SOURCE kanal to'liq o'qiladi (yangi postlar ro'yxatga qo'shiladi)
- Barcha (yoki oxirgi N ta) post/albomlar target guruhlarga forward qilinadi
- EXCLUDE: username/URL va aniq ID bo‘yicha qat’iy blok
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
)
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

# ====================== SETTINGS ======================
POST_DELAY_SECONDS = 8            # har post/albomdan keyin kutish
GROUP_DELAY_SECONDS = 30          # har target guruhdan keyin kutish
CHECK_EVERY_SECONDS = 20          # sikllar orasidagi kutish (kanalni qayta o‘qishdan oldin)
REPLAY_LAST_N_GROUPS = None       # None -> hammasi; masalan 300 qo'ysangiz, faqat oxirgi 300 ta guruh

# Telegram API credentials (o'zingizniki bilan to'ldiring)
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

# Manba kanal
SOURCE_CHANNEL = "https://t.me/Navoiy_uy_barcha_elonlar_bazasi"

# Majburiy target guruhlar (username)
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

# Hech qachon yuborilmaydigan targetlar (URL yoki @username)
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
    "https://t.me/Navoiy_uyjoy_savdo",      # << qo'shildi
]

# Shaxsiy (username’siz) guruhlar uchun aniq ID bo‘yicha qat’iy blok
# Masalan: -1001839437480 va hokazo. ID’larni logdan oling.
EXCLUDED_IDS = {
    # -1001839437480,
    # -1002387184511,
}

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# ====================== UTILITIES ======================
def normalize_channel(value: str) -> str:
    """URL yoki @username ni username/path ga keltiradi (kichik harf)."""
    value = value.strip()
    if value.startswith("http"):
        path = urlparse(value).path.strip("/")
        return path.lower()
    return value.lstrip("@").lower()

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

async def build_excluded_sets(client: TelegramClient):
    """
    EXCLUDED_TARGETS va SOURCE_CHANNEL'ni IDlarga resolve qiladi.
    Shuningdek, username/path’larni ham qaytaradi (fallback uchun).
    Private +invite bo'lsa (joinchat/+), resolve qilinmasa ham username set’da turadi.
    """
    excluded_ids = set(EXCLUDED_IDS)  # aniq ID’lar darrov qo'shiladi
    excluded_usernames = set()        # lowercased path/usernames

    all_targets = list(EXCLUDED_TARGETS) + [SOURCE_CHANNEL]
    for item in all_targets:
        try:
            path = normalize_channel(item)
            excluded_usernames.add(path)

            # Private invite bo'lsa — resolve qilmasdan skip (agar a'zo bo'lmasangiz baribir forward bo'lmaydi)
            if path.startswith("+"):
                logging.warning(f"Skip resolve (private invite not joined): {item}")
                continue

            ent = await client.get_entity(path)
            excluded_ids.add(ent.id)
            title = getattr(ent, "title", getattr(ent, "username", str(ent.id)))
            logging.info(f"[Exclude] {title} (id={ent.id})")
        except Exception as e:
            logging.warning(f"Could not resolve excluded: {item} -> {e}")

    # Aniq ID’lar ro‘yxatini ham logda ko‘rsatamiz
    if EXCLUDED_IDS:
        logging.info(f"[Exclude IDs explicit] {sorted(list(EXCLUDED_IDS))}")

    return excluded_ids, excluded_usernames

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

async def get_all_posts_grouped(client: TelegramClient, limit=100000):
    """
    SOURCE kanalidan barcha xabarlarni olib, albom/oddiy bo'yicha guruhlab qaytaradi.
    from_peer sifatida **entity** qaytadi.
    """
    if not await ensure_connection(client):
        return [], None

    source_username = normalize_channel(SOURCE_CHANNEL)
    source_ent = await client.get_entity(source_username)

    messages = await client.get_messages(source_ent, limit=limit)  # entity bilan
    grouped = group_messages(messages)

    # Floodni yengillashtirish: faqat oxirgi N ta guruhni aylantirish
    if isinstance(REPLAY_LAST_N_GROUPS, int) and REPLAY_LAST_N_GROUPS > 0:
        grouped = grouped[-REPLAY_LAST_N_GROUPS:]

    logging.info(f"Fetched {len(grouped)} post groups from source.")
    return grouped, source_ent

def dialog_username_lower(dialog) -> str:
    """Dialogdan username/path (lowercase) ni olish (bo'lsa)."""
    uname = getattr(dialog, "username", None)
    if uname:
        return uname.lower()
    # Channel/Chat turlari uchun .username bo'lmasligi mumkin
    # Boshqa aniqlash yo'qligi sababli faqat username bo'lsa tekshiramiz.
    return ""

async def get_admin_groups(client: TelegramClient, excluded_ids: set, excluded_usernames: set):
    """
    Siz admin bo'lgan guruhlar + ADDITIONAL_GROUPS
    (kanallar/broadcast chetlanadi), excluded_ids/username bilan filtrlanadi.
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
    # Men admin bo'lgan chatlar
    for dialog in dialogs.chats:
        try:
            # Kanal bo‘lsa o‘tkazib yuboramiz
            if getattr(dialog, 'broadcast', False):
                continue

            # Exclude by ID (qat’iy)
            if getattr(dialog, "id", None) in excluded_ids:
                logging.info(f"Skipped excluded (admin list, by id): {getattr(dialog,'title',dialog.id)} (id={dialog.id})")
                continue

            # Exclude by username/path (fallback)
            uname = dialog_username_lower(dialog)
            if uname and uname in excluded_usernames:
                logging.info(f"Skipped excluded (admin list, by username): {getattr(dialog,'title',dialog.id)} (id={dialog.id})")
                continue

            # Admin ekanligimizni tekshirish
            is_admin = False
            if hasattr(dialog, 'admin_rights') and dialog.admin_rights:
                is_admin = True
            if getattr(dialog, 'creator', False):
                is_admin = True

            if is_admin:
                admin_groups.append(dialog)
                logging.info(f"Admin target: {getattr(dialog,'title',dialog.id)} (id={dialog.id})")
        except Exception:
            continue

    # Majburiy qo'shiladiganlar
    for group_username in ADDITIONAL_GROUPS:
        try:
            group = await client.get_entity(group_username)
            if getattr(group, 'broadcast', False):
                logging.info(f"Skip additional (it's a channel): {group_username}")
                continue

            # Exclude tekshiruvlari
            if getattr(group, "id", None) in excluded_ids:
                logging.info(f"Skipped excluded (additional, by id): {getattr(group,'title',group_username)} (id={group.id})")
                continue
            g_uname = getattr(group, "username", None)
            if g_uname and g_uname.lower() in excluded_usernames:
                logging.info(f"Skipped excluded (additional, by username): {getattr(group,'title',group_username)} (id={group.id})")
                continue

            admin_groups.append(group)
            logging.info(f"Added additional target: {getattr(group,'title',group_username)} (id={group.id})")
        except Exception as e:
            logging.error(f"Error adding target {group_username}: {e}")

    # Dublikatlarni ID bo‘yicha yo‘qotamiz
    uniq = {}
    for g in admin_groups:
        uniq[getattr(g, "id", id(g))] = g
    return list(uniq.values())

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
        excluded_ids, excluded_usernames = await build_excluded_sets(client)
        logging.info(f"Excluded IDs count: {len(excluded_ids)} | Excluded usernames count: {len(excluded_usernames)}")

        while True:
            # Ish vaqti nazorati
            if not is_working_time():
                logging.info("Outside working hours. Waiting...")
                await asyncio.sleep(60)
                continue

            # 1) Har siklda SOURCE ni to'liq o‘qish (eski + yangi hammasi)
            source_groups, source_ent = await get_all_posts_grouped(client)
            if not source_groups or not source_ent:
                logging.info("No source groups found. Waiting...")
                await asyncio.sleep(CHECK_EVERY_SECONDS)
                continue

            # 2) Targetlar (admin bo'lganlar + qo'shimcha), exclude bilan filtrlangan
            admin_groups = await get_admin_groups(client, excluded_ids, excluded_usernames)
            if not admin_groups:
                logging.warning("No admin groups found after exclude. Waiting...")
                await asyncio.sleep(60)
                continue

            # 3) Barcha (yoki oxirgi N) guruhlarni har bir targetga forward qilish
            for group in admin_groups:
                gid = getattr(group, "id", None)
                gtitle = getattr(group, "title", getattr(group, "username", str(gid)))
                guname = getattr(group, "username", None)
                guname_l = guname.lower() if guname else ""

                # Oxirgi himoya: forward oldidan ham exclude tekshiruv
                if gid in excluded_ids or (guname_l and guname_l in excluded_usernames):
                    logging.info(f"Skipped excluded at send loop: {gtitle} (id={gid})")
                    continue

                logging.info(f"⬇️ Forwarding ALL (old+new) to {gtitle} (id={gid})...")

                for msg_group in source_groups:
                    message_ids = [m.id for m in msg_group if getattr(m, "id", None)]
                    if not message_ids:
                        continue
                    try:
                        if not await ensure_connection(client):
                            continue
                        # MUHIM: from_peer sifatida entity (source_ent) berilmoqda
                        await client.forward_messages(
                            group,        # target entity
                            message_ids,  # IDs of messages in the group
                            source_ent    # from_peer: entity
                        )
                        logging.info(f"✅ Forwarded to {gtitle}: {message_ids}")
                        await asyncio.sleep(POST_DELAY_SECONDS)
                    except FloodWaitError as e:
                        logging.warning(f"FloodWait: Waiting {e.seconds} seconds")
                        await asyncio.sleep(e.seconds + 5)
                    except Exception as e:
                        logging.error(f"Error forwarding to {gtitle}: {e}")
                        continue

                logging.info(f"✅ Done with {gtitle}. Waiting {GROUP_DELAY_SECONDS} sec.")
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
