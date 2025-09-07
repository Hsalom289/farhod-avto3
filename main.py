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

from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat, User

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
    "https://t.me/+6e1vw3QlFLg1Zjcy",
    "https://t.me/+N9nRAUGPEl43YmFi",
    "https://t.me/Navoiy_1_xona_kvartira",
    "https://t.me/Navoiy_2_xona_kvartira",
    "https://t.me/Navoiy_3_4_5_xona_kvartira",
    "https://t.me/+HclTfLD4W5o0NmNi",
    "https://t.me/Navoiy_ijaraga_kv_uy",
    "https://t.me/Navoiy_hovli_katedj_dacha",
    "https://t.me/Navoiy_uyjoy_savdo",
]

# Shaxsiy (username’siz) guruhlar uchun aniq ID bo‘yicha qat’iy blok
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
    """URL yoki @username ni path/username ga keltiradi (lowercase)."""
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

async def safe_get_entity(client: TelegramClient, ref: str):
    """Entity ni xavfsiz resolve qiladi."""
    try:
        return await client.get_entity(ref)
    except Exception as e:
        logging.warning(f"get_entity failed for {ref}: {e}")
        return None

async def build_excluded_sets(client: TelegramClient):
    """
    EXCLUDED_TARGETS va SOURCE_CHANNEL'ni IDlarga resolve qiladi.
    Shuningdek, username/path’larni ham qaytaradi.
    """
    excluded_ids = set(EXCLUDED_IDS)
    excluded_usernames = set()

    all_targets = list(EXCLUDED_TARGETS) + [SOURCE_CHANNEL]
    for item in all_targets:
        path = normalize_channel(item)
        if path:
            excluded_usernames.add(path)

        # private invite bo'lsa, resolve qilmaymiz
        if path.startswith("+"):
            logging.warning(f"Skip resolve (private invite not joined): {item}")
            continue

        ent = await safe_get_entity(client, path)
        if ent is None:
            continue
        ent_id = getattr(ent, "id", None)
        if ent_id:
            excluded_ids.add(ent_id)
            title = getattr(ent, "title", getattr(ent, "username", str(ent_id)))
            logging.info(f"[Exclude] {title} (id={ent_id})")

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
    source_ent = await safe_get_entity(client, source_username)
    if source_ent is None:
        logging.error("Source channel resolve bo'lmadi.")
        return [], None

    try:
        messages = await client.get_messages(source_ent, limit=limit)
    except errors.FloodWaitError as e:
        logging.warning(f"FloodWait on source fetch: {e.seconds}s")
        await asyncio.sleep(e.seconds + 5)
        messages = await client.get_messages(source_ent, limit=limit)

    grouped = group_messages(messages)

    if isinstance(REPLAY_LAST_N_GROUPS, int) and REPLAY_LAST_N_GROUPS > 0:
        grouped = grouped[-REPLAY_LAST_N_GROUPS:]

    logging.info(f"Fetched {len(grouped)} post groups from source.")
    return grouped, source_ent

def is_me_admin_dialog(dlg) -> bool:
    """Dialog obyektida adminlikni aniqlash."""
    # Channel (megagroup) uchun admin_rights bor
    if isinstance(dlg, Channel):
        if getattr(dlg, 'creator', False):
            return True
        ar = getattr(dlg, 'admin_rights', None)
        return bool(ar)
    # Oddiy Chat:
    if isinstance(dlg, Chat):
        # Chatlarda aniq tekshirish cheklangan. Dialoglar ro‘yxatida admin bo‘lish ko‘rsatilmaydi.
        # Fallback: Chat’larni o‘tkazib yuborish yoki sinab ko‘rish mumkin.
        return False
    return False

def dialog_username_lower(entity) -> str:
    """Entity’dan username/path (lowercase)."""
    uname = getattr(entity, "username", None)
    return uname.lower() if uname else ""

async def get_admin_groups(client: TelegramClient, excluded_ids: set, excluded_usernames: set):
    """
    Siz admin bo'lgan megagruppalar + ADDITIONAL_GROUPS,
    excluded_ids/username bilan filtrlanadi.
    """
    if not await ensure_connection(client):
        return []

    admin_groups = []

    async for dialog in client.iter_dialogs(limit=None):
        ent = dialog.entity
        if not isinstance(ent, (Channel, Chat)):
            continue

        # Kanal-broadcast ni chetga suramiz
        if isinstance(ent, Channel) and getattr(ent, "broadcast", False):
            continue

        # Exclude by ID
        if getattr(ent, "id", None) in excluded_ids:
            logging.info(f"Skipped excluded (by id): {getattr(ent,'title',ent)} (id={ent.id})")
            continue

        # Exclude by username/path
        uname = dialog_username_lower(ent)
        if uname and uname in excluded_usernames:
            logging.info(f"Skipped excluded (by username): {getattr(ent,'title',ent)} (id={ent.id})")
            continue

        # Adminlik
        if is_me_admin_dialog(ent):
            admin_groups.append(ent)
            logging.info(f"Admin target: {getattr(ent,'title',getattr(ent,'username',ent.id))} (id={ent.id})")

    # Majburiy qo'shiladiganlar
    for group_username in ADDITIONAL_GROUPS:
        ref = group_username.lstrip("@")
        ent = await safe_get_entity(client, ref)
        if ent is None:
            continue
        if isinstance(ent, Channel) and getattr(ent, "broadcast", False):
            logging.info(f"Skip additional (channel): {group_username}")
            continue

        # Exclude tekshiruvi
        if getattr(ent, "id", None) in excluded_ids:
            logging.info(f"Skipped excluded (additional, by id): {group_username} (id={ent.id})")
            continue
        if getattr(ent, "username", None) and ent.username.lower() in excluded_usernames:
            logging.info(f"Skipped excluded (additional, by username): {group_username} (id={ent.id})")
            continue

        admin_groups.append(ent)
        logging.info(f"Added additional target: {group_username} (id={ent.id})")

    # Dublikatlarni ID bo‘yicha yo‘qotamiz
    uniq = {}
    for g in admin_groups:
        uniq[getattr(g, "id", id(g))] = g
    return list(uniq.values())

# ====================== MAIN LOOP ======================
async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    # Mavjud sessiya tekshiruvi
    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logging.warning("Session invalid, removing...")
                await client.disconnect()
                os.remove(session_file)
        except Exception:
            logging.warning("Session file corrupted, removing...")
            try:
                await client.disconnect()
            except Exception:
                pass
            os.remove(session_file)

    # Start (login)
    try:
        await client.start(phone=phone)
        logging.info("Connected to Telegram.")
    except errors.SessionPasswordNeededError:
        logging.error("Two-factor authentication required.")
        return
    except errors.PhoneNumberBannedError:
        logging.error("Phone number is banned.")
        return
    except errors.SessionRevokedError:
        logging.error("Session revoked.")
        return
    except Exception as e:
        logging.error(f"Error connecting: {e}")
        return

    try:
        excluded_ids, excluded_usernames = await build_excluded_sets(client)
        logging.info(f"Excluded IDs: {len(excluded_ids)} | Excluded usernames: {len(excluded_usernames)}")

        while True:
            # Ish vaqti nazorati
            if not is_working_time():
                logging.info("Outside working hours. Waiting 60s...")
                await asyncio.sleep(60)
                continue

            # 1) SOURCE ni to'liq o‘qish
            source_groups, source_ent = await get_all_posts_grouped(client)
            if not source_groups or not source_ent:
                logging.info("No source groups found. Waiting...")
                await asyncio.sleep(CHECK_EVERY_SECONDS)
                continue

            # 2) Targetlar (admin bo'lganlar + qo'shimcha), exclude bilan filtrlangan
            admin_groups = await get_admin_groups(client, excluded_ids, excluded_usernames)
            if not admin_groups:
                logging.warning("No admin groups found after exclude. Waiting 60s...")
                await asyncio.sleep(60)
                continue

            # 3) Forward
            for target in admin_groups:
                gid = getattr(target, "id", None)
                gtitle = getattr(target, "title", getattr(target, "username", str(gid)))
                guname = getattr(target, "username", None)
                guname_l = guname.lower() if guname else ""

                # Oxirgi himoya
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

                        await client.forward_messages(
                            entity=target,          # target entity
                            messages=message_ids,   # IDs list
                            from_peer=source_ent    # from entity
                        )
                        logging.info(f"✅ Forwarded to {gtitle}: {message_ids}")
                        await asyncio.sleep(POST_DELAY_SECONDS)

                    except errors.FloodWaitError as e:
                        wait_s = int(e.seconds) + 5
                        logging.warning(f"FloodWait: Waiting {wait_s} seconds")
                        await asyncio.sleep(wait_s)

                    except (errors.ChatWriteForbiddenError,
                            errors.UserBannedInChannelError,
                            errors.ChannelPrivateError) as e:
                        logging.error(f"Write/Access forbidden for {gtitle}: {e}. Skipping this target.")
                        break  # bu targetga yozib bo'lmaydi, keyingisiga o'tamiz

                    except errors.RPCError as e:
                        logging.error(f"RPCError forwarding to {gtitle}: {e}")
                        await asyncio.sleep(5)
                        continue

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
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        logging.info("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
