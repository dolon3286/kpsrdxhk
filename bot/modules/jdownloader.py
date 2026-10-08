#!/usr/bin/env python3
"""Telegram LinkGrabber commands with JDownloader-like workflow."""
from __future__ import annotations

from html import escape
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.filters import command, regex
from bot import bot, bot_cache, DOWNLOAD_DIR, config_dict, user_data
from bot.helper.ext_utils.bot_utils import (
    new_task, sync_to_async, task_utils, fetch_user_tds,
    is_rclone_path, get_readable_file_size
)
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage, deleteMessage
from bot.helper.listeners.tasks_listener import MirrorLeechListener
from bot.helper.mirror_utils.download_utils.direct_downloader import add_direct_download
from bot.helper.mirror_utils.download_utils.jdownloader_linkgrabber import (
    extract_urls, grab_links, build_details
)
from bot.helper.mirror_utils.upload_utils.gdriveTools import GoogleDriveHelper


def _store():
    return bot_cache.setdefault("jd_grab", {})


def _key_for(user_id, message_id):
    return f"{user_id}:{message_id}"


def _format_entries(entries):
    lines = ["<b>JDownloader-style LinkGrabber</b>", ""]
    for index, item in enumerate(entries[:50], 1):
        size = get_readable_file_size(item.get("size", 0)) if item.get("size") else "-"
        name = escape(str(item.get("name") or item.get("filename") or "download"))
        url = escape(str(item.get("source_url") or item.get("url") or ""))
        lines.append(f"<b>{index}.</b> {name} <code>[{size}]</code>")
        lines.append(f"   <code>{url[:220]}</code>")
    if len(entries) > 50:
        lines.append(f"\n…and {len(entries) - 50} more links.")
    return "\n".join(lines)


def _select(entries, selection):
    if not selection or selection.lower() == "all":
        return entries
    selected = []
    for part in selection.replace(" ", "").split(","):
        if not part:
            continue
        try:
            index = int(part) - 1
        except ValueError:
            continue
        if 0 <= index < len(entries):
            selected.append(entries[index])
    return selected


async def _download_entries(message, entries):
    if not entries:
        await sendMessage(message, "No links selected.")
        return

    uid = message.from_user.id
    errors, buttons = await task_utils(message)
    if errors:
        text = "\n".join(f"<b>{i}.</b> {x}" for i, x in enumerate(errors, 1))
        await sendMessage(message, text)
        return

    up = ""
    drive_id = ""
    index_link = ""
    default_upload = config_dict["DEFAULT_UPLOAD"]

    if default_upload == "rc":
        up = config_dict["RCLONE_PATH"]
    elif default_upload == "ddl":
        up = "ddl"
    else:
        up = "gd"
        user_tds = await fetch_user_tds(uid)
        if len(user_tds) == 1:
            drive_id, index_link = next(iter(user_tds.values())).values()
        if not drive_id and not config_dict["GDRIVE_ID"] and not user_tds:
            await sendMessage(
                message,
                "JDownloader download needs a configured GDRIVE_ID, "
                "UserTD, RCLONE_PATH/DEFAULT_UPLOAD, or DDL destination."
            )
            return

    if up == "gd":
        if drive_id and not await sync_to_async(
            GoogleDriveHelper(user_id=uid).getFolderData, drive_id
        ):
            await sendMessage(message, "Google Drive ID validation failed!")
            return
        if not drive_id:
            drive_id = config_dict["GDRIVE_ID"]
    elif up != "ddl" and not is_rclone_path(up):
        await sendMessage(message, "No valid upload destination is configured.")
        return

    details = build_details(entries)
    listener = MirrorLeechListener(
        message,
        False,
        False,
        False,
        False,
        message.from_user.mention,
        False,
        False,
        None,
        None,
        up,
        False,
        drive_id=drive_id,
        index_link=index_link,
        source_url=entries[0].get("source_url") or entries[0]["url"],
    )
    await add_direct_download(
        details, f"{DOWNLOAD_DIR}{message.id}", listener, "LinkGrabber"
    )


@new_task
async def jd_grab(_, message):
    raw = ""
    if len(message.command) > 1:
        raw = message.text.split(None, 1)[1]
    elif message.reply_to_message:
        raw = message.reply_to_message.text or message.reply_to_message.caption or ""

    urls = extract_urls(raw)
    if not urls:
        await sendMessage(
            message,
            f"Usage: <code>/{BotCommands.JdGrabCommand} URL ...</code>\n"
            "You can also reply to a message containing multiple URLs."
        )
        return

    status = await sendMessage(
        message, f"<i>LinkGrabber:</i> collecting up to <b>100</b> links..."
    )
    entries = await grab_links(urls)
    await deleteMessage(status)

    if not entries:
        await sendMessage(
            message,
            "LinkGrabber found no downloadable links. "
            "The site may require JavaScript/login/captcha or may not expose files."
        )
        return

    uid = message.from_user.id
    key = _key_for(uid, message.id)
    _store()[key] = {"entries": entries, "message": message}

    buttons = ButtonMaker()
    buttons.ibutton("⬇️ Download All", f"jdg {uid} {message.id} all")
    buttons.ibutton("✖️ Close", f"jdg {uid} {message.id} close")
    text = _format_entries(entries)
    await sendMessage(
        message,
        text + "\n\n<b>Selective:</b> reply with "
        f"<code>/{BotCommands.JdDownloadCommand} 1,3,5</code>",
        buttons.build_menu(2)
    )


@new_task
async def jd_download(_, message):
    uid = message.from_user.id
    store = _store()
    key = None

    if message.reply_to_message:
        candidate = _key_for(uid, message.reply_to_message.id)
        if candidate in store:
            key = candidate

    selection = "all"
    if len(message.command) > 1:
        if message.command[1].replace(",", "").replace(" ", "").isdigit():
            selection = message.command[1]
        elif len(message.command) > 2:
            selection = message.command[2]
        elif message.command[1].isdigit():
            key = _key_for(uid, int(message.command[1]))
            if len(message.command) > 2:
                selection = message.command[2]

    if key is None:
        own = [k for k in store if k.startswith(f"{uid}:")]
        if own:
            key = own[-1]

    if key not in store:
        await sendMessage(message, "No active LinkGrabber result found.")
        return

    entries = _select(store[key]["entries"], selection)
    if not entries:
        await sendMessage(message, "No valid link numbers were selected.")
        return

    await sendMessage(
        message,
        f"<b>LinkGrabber:</b> starting <code>{len(entries)}</code> download(s)."
    )
    await _download_entries(message, entries)


async def jd_callback(_, query):
    data = query.data.split()
    if len(data) < 4:
        return await query.answer("Invalid request", show_alert=True)

    uid, msg_id, action = int(data[1]), data[2], data[3]
    if query.from_user.id != uid:
        return await query.answer("Not yours!", show_alert=True)

    key = _key_for(uid, int(msg_id))
    data = _store().get(key)
    if not data:
        return await query.answer("LinkGrabber data expired.", show_alert=True)
    entries = data["entries"]
    source_message = data["message"]

    await query.answer()

    if action == "close":
        _store().pop(key, None)
        return await editMessage(query.message, "LinkGrabber closed.")

    if action == "all":
        await editMessage(
            query.message,
            f"Starting <b>{len(entries)}</b> LinkGrabber download(s)..."
        )
        await _download_entries(source_message, entries)
        _store().pop(key, None)


bot.add_handler(MessageHandler(
    jd_grab,
    filters=command(BotCommands.JdGrabCommand)
    & CustomFilters.authorized
    & ~CustomFilters.blacklisted
))
bot.add_handler(MessageHandler(
    jd_download,
    filters=command(BotCommands.JdDownloadCommand)
    & CustomFilters.authorized
    & ~CustomFilters.blacklisted
))
bot.add_handler(CallbackQueryHandler(jd_callback, filters=regex(r"^jdg")))
