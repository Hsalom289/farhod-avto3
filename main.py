# -*- coding: utf-8 -*-
"""
Forward bot: bitta post/albom -> barcha targetlarga -> 3 min kutish -> keyingi post
Ish vaqti: 10:00–23:00 (Asia/Samarkand, UTC+5)
"""

import asyncio
import logging
import os
from datetime import datetime, time as dt_time, timezone, timedelta
from urllib.parse import urlparse

from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat

# ============== SETTINGS ==============
# Targetlar orasida juda qisqa pauza (floodni kamaytirish uchun)
TARGET_DELAY_SECONDS = 5           # har targetga yuborgach kutish
PER_POST_SLEEP_SECONDS = 180       # bitta postni hammaga tarqatgach kutish (3 min)
CHECK_EVERY_SECONDS = 20           # sikllar orasida manbani qayta o‘qish
REPLAY_LAST_N_GROUPS = None        # None -> hammasi; masalan 300

api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

SOURCE_CHANNEL = "https://t.me/Navoiy_uy_barcha_elonlar_bazasi"

ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

EXCLUDED_TARGETS = [
    "https://t.me/Navoiy_uy_barcha_elonlar_bazasi",
    "https://t.me/Navoiy_uy_joy_kv_barcha_elonlar",
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

EXCLUDED_IDS = {
    # -1001839437480,
}

# ============== LOGGING ==============
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ============== UTILITIES ==============
def normalize_channel(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        return urlparse(value).path.strip("/").lower()
    return value.lstrip("@").lower()

def is_working_time() -> bool:
    uz = timezone(timedelta(hours=5))
    t = datetime.now(uz).time()
    return dt_time(10, 0) <= t <= dt_time(23, 0)

async def ensure_connection(client: TelegramClient) -> bool:
    try:
        if not client.is_connected():
            await client.connect()
        return True
    except Exception as e:
        logging.error(f"Reconnect failed: {e}")
        return False

async def safe_get_entity(client: TelegramClient, ref: str):
    try:
        return await client.get_entity(ref)
    except Exception as e:
        logging.warning(f"get_entity failed for {ref}: {e}")
        return None

async def build_excluded_sets(client: TelegramClient):
    excluded_ids = set(EXCLUDED_IDS)
    excluded_usernames = set()
    for item in list(EXCLUDED_TARGETS) + [SOURCE_CHANNEL]:
        path = normalize_channel(item)
        if path:
            excluded_usernames.add(path)
        if path.startswith("+"):
            logging.warning(f"Skip resolve (private invite): {item}")
            continue
        ent = await safe_get_entity(client, path)
        if ent and getattr(ent, "id", None):
            excluded_ids.add(ent.id)
            logging.info(f"[Exclude] {getattr(ent,'title',getattr(ent,'username',ent.id))} (id={ent.id})")
    return excluded_ids, excluded_usernames

def group_messages(messages):
    grouped = {}
    for m in messages:
        if not getattr(m, "id", None):
            continue
        key = m.grouped_id if m.grouped_id else m.id
        grouped.setdefault(key, []).append(m)
    out = []
    for k in sorted(grouped.keys()):
        out.append(sorted(grouped[k], key=lambda x: x.id))
    return out

async def get_all_posts_grouped(client: TelegramClient, limit=100000):
    if not await ensure_connection(client):
        return [], None
    src_ref = normalize_channel(SOURCE_CHANNEL)
    source_ent = await safe_get_entity(client, src_ref)
    if source_ent is None:
        logging.error("Source resolve bo'lmadi.")
        return [], None
    try:
        msgs = await client.get_messages(source_ent, limit=limit)
    except errors.FloodWaitError as e:
        logging.warning(f"Flood on source fetch: wait {e.seconds}s")
        await asyncio.sleep(int(e.seconds) + 5)
        msgs = await client.get_messages(source_ent, limit=limit)
    grouped = group_messages(msgs)
    if isinstance(REPLAY_LAST_N_GROUPS, int) and REPLAY_LAST_N_GROUPS > 0:
        grouped = grouped[-REPLAY_LAST_N_GROUPS:]
    logging.info(f"Source groups: {len(grouped)}")
    return grouped, source_ent

def is_me_admin_entity(ent) -> bool:
    if isinstance(ent, Channel):
        if getattr(ent, "creator", False):
            return True
        return bool(getattr(ent, "admin_rights", None))
    return False  # Chat uchun aniqlash cheklangan

def uname_lower(ent) -> str:
    u = getattr(ent, "username", None)
    return u.lower() if u else ""

async def get_admin_groups(client: TelegramClient, excluded_ids: set, excluded_usernames: set):
    if not await ensure_connection(client):
        return []
    targets = []
    async for dlg in client.iter_dialogs(limit=None):
        ent = dlg.entity
        if not isinstance(ent, (Channel, Chat)):
            continue
        if isinstance(ent, Channel) and getattr(ent, "broadcast", False):
            continue
        if getattr(ent, "id", None) in excluded_ids:
            continue
        if uname_lower(ent) in excluded_usernames:
            continue
        if is_me_admin_entity(ent):
            targets.append(ent)
    # Majburiy qo‘shiladiganlar
    for u in ADDITIONAL_GROUPS:
        ent = await safe_get_entity(client, u.lstrip("@"))
        if not ent:
            continue
        if isinstance(ent, Channel) and getattr(ent, "broadcast", False):
            continue
        if getattr(ent, "id", None) in excluded_ids:
            continue
        if uname_lower(ent) in excluded_usernames:
            continue
        targets.append(ent)
    # unique by id
    uniq = {}
    for g in targets:
        uniq[getattr(g, "id", id(g))] = g
    res = list(uniq.values())
    logging.info(f"Admin targets: {len(res)}")
    return res

# ============== MAIN ==============
async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    # Sessiya
    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                os.remove(session_file)
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            os.remove(session_file)

    # Login
    try:
        await client.start(phone=phone)
        logging.info("Connected.")
    except errors.SessionPasswordNeededError:
        logging.error("Two-factor yoqilgan.")
        return
    except errors.PhoneNumberBannedError:
        logging.error("Raqam ban.")
        return
    except errors.SessionRevokedError:
        logging.error("Sessiya bekor qilingan.")
        return
    except Exception as e:
        logging.error(f"Connect error: {e}")
        return

    try:
        excluded_ids, excluded_usernames = await build_excluded_sets(client)

        while True:
            if not is_working_time():
                logging.info("Ish vaqtidan tashqari. 60s kutish.")
                await asyncio.sleep(60)
                continue

            source_groups, source_ent = await get_all_posts_grouped(client)
            if not source_groups or not source_ent:
                logging.info("Source bo'sh. Kutish.")
                await asyncio.sleep(CHECK_EVERY_SECONDS)
                continue

            admin_groups = await get_admin_groups(client, excluded_ids, excluded_usernames)
            if not admin_groups:
                logging.warning("Admin target yo‘q. 60s kutish.")
                await asyncio.sleep(60)
                continue

            # >>> YANGI LOGIKA <<<
            # Tashqi sikl: POST/ALBOM
            for msg_group in source_groups:
                message_ids = [m.id for m in msg_group if getattr(m, "id", None)]
                if not message_ids:
                    continue

                logging.info(f"Post group {message_ids[0]}.. tarqatish boshlandi.")

                # Ichki sikl: TARGETLAR
                for target in admin_groups:
                    gid = getattr(target, "id", None)
                    gtitle = getattr(target, "title", getattr(target, "username", str(gid)))
                    guname_l = uname_lower(target)

                    # Oxirgi himoya
                    if gid in excluded_ids or (guname_l and guname_l in excluded_usernames):
                        logging.info(f"Skip excluded: {gtitle}")
                        continue

                    try:
                        if not await ensure_connection(client):
                            continue
                        await client.forward_messages(
                            entity=target,
                            messages=message_ids,
                            from_peer=source_ent
                        )
                        logging.info(f"✅ Forwarded -> {gtitle}: {message_ids}")
                        await asyncio.sleep(TARGET_DELAY_SECONDS)

                    except errors.FloodWaitError as e:
                        wait_s = int(e.seconds) + 5
                        logging.warning(f"FloodWait for {gtitle}: wait {wait_s}s")
                        await asyncio.sleep(wait_s)

                    except (errors.ChatWriteForbiddenError,
                            errors.UserBannedInChannelError,
                            errors.ChannelPrivateError) as e:
                        logging.error(f"Access forbidden {gtitle}: {e}. Target skip.")
                        continue

                    except errors.RPCError as e:
                        logging.error(f"RPCError {gtitle}: {e}. Continue.")
                        await asyncio.sleep(5)
                        continue

                    except Exception as e:
                        logging.error(f"Error {gtitle}: {e}. Continue.")
                        continue

                # Bitta post hammaga yuborildi -> 3 minut kutamiz
                logging.info(f"⏳ Post tarqatildi. {PER_POST_SLEEP_SECONDS}s kutish...")
                await asyncio.sleep(PER_POST_SLEEP_SECONDS)

            logging.info(f"🔄 Barcha source postlar tarqatildi. {CHECK_EVERY_SECONDS}s dan keyin qayta tekshiramiz.")
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
