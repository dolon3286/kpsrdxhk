#!/usr/bin/env python3
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.filters import command, regex
from psutil import cpu_percent, virtual_memory, disk_usage
from time import time
from asyncio import sleep

from bot import bot_cache, status_reply_dict_lock, download_dict, download_dict_lock, botStartTime, Interval, config_dict, bot, OWNER_ID, user_data
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage, deleteMessage, auto_delete_message, sendStatusMessage, user_info, update_all_messages, delete_all_messages
from bot.helper.ext_utils.bot_utils import get_readable_file_size, get_readable_time, turn_page, setInterval, new_task, MirrorStatus, getDownloadByGid
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.themes import BotTheme


@new_task
async def mirror_status(_, message):
    async with download_dict_lock:
        count = len(download_dict)
    if count == 0:
        currentTime = get_readable_time(time() - botStartTime)
        free = get_readable_file_size(disk_usage(config_dict['DOWNLOAD_DIR']).free)
        free_p = round(100 - disk_usage(config_dict['DOWNLOAD_DIR']).percent, 1)
        msg = (
            "<i>No Active Downloads!</i>\n\n"
            "₪ <b>Bot Stats</b>\n"
            "<b>Page:</b> 1/1 | <b>Tasks:</b> 0 | <b>Step:</b> 1\n\n"
            "<table>\n"
            "<tr><th>⌘ CPU</th><th>∞ Free</th><th>⬝⬝ RAM</th><th>↳ UP</th></tr>\n"
            f"<tr><td>{cpu_percent()}%</td><td>{free} [{free_p}%]</td><td>{virtual_memory().percent}%</td><td>{currentTime}</td></tr>\n"
            "</table>"
        )
        buttons = ButtonMaker()
        buttons.ibutton(BotTheme('TSTATS'), "status tstats")
        buttons.ibutton(BotTheme('REFRESH'), "status ref")
        reply_message = await sendMessage(message, msg, buttons.build_menu(2))
        await auto_delete_message(message, reply_message)
    else:
        await sendStatusMessage(message)
        await deleteMessage(message)
        async with status_reply_dict_lock:
            if Interval:
                Interval[0].cancel()
                Interval.clear()
                Interval.append(setInterval(config_dict['STATUS_UPDATE_INTERVAL'], update_all_messages))


@new_task
async def status_pages(_, query):
    user_id = query.from_user.id
    data = query.data.split()
    if data[1] == 'ref':
        bot_cache.setdefault('status_refresh', {})
        if user_id in (refresh_status := bot_cache['status_refresh']) and (curr := (time() - refresh_status[user_id])) < 7:
            return await query.answer(f'Already Refreshed! Try after {get_readable_time(7 - curr)}', show_alert=True)
        else:
            refresh_status[user_id] = time()
        await query.answer("Refreshing Status...")
        await update_all_messages(True)
    elif data[1] in ['nex', 'pre']:
        await turn_page(data)
        await update_all_messages(True)
        await query.answer()
    elif data[1] == 'can':
        gid = data[2]
        dl = await getDownloadByGid(gid)
        if dl is None:
            return await query.answer("This task is not active!", show_alert=True)
        if OWNER_ID != user_id and dl.message.from_user.id != user_id and \
           (user_id not in user_data or not user_data[user_id].get('is_sudo')):
            return await query.answer("This task is not for you!", show_alert=True)
        obj = dl.download()
        await obj.cancel_download()
        await query.answer("Task Cancelled!", show_alert=True)
        await update_all_messages(True)
    elif data[1] == 'tstats':
        async with download_dict_lock:
            tasks = len(download_dict)
            dl_cnt = sum(1 for d in download_dict.values() if d.status() == MirrorStatus.STATUS_DOWNLOADING)
            up_cnt = sum(1 for d in download_dict.values() if d.status() == MirrorStatus.STATUS_UPLOADING)
            sd_cnt = sum(1 for d in download_dict.values() if d.status() == MirrorStatus.STATUS_SEEDING)
            qu_cnt = sum(1 for d in download_dict.values() if d.status() in [MirrorStatus.STATUS_QUEUEDL, MirrorStatus.STATUS_QUEUEUP])
            ar_cnt = sum(1 for d in download_dict.values() if d.status() in [MirrorStatus.STATUS_ARCHIVING, MirrorStatus.STATUS_EXTRACTING, MirrorStatus.STATUS_SPLITTING])
        free = get_readable_file_size(disk_usage(config_dict['DOWNLOAD_DIR']).free)
        tstats_msg = (
            f"📜 Tasks Statistics:\n"
            f"• Total Tasks: {tasks}\n"
            f"• Downloading: {dl_cnt}\n"
            f"• Uploading: {up_cnt}\n"
            f"• Seeding: {sd_cnt}\n"
            f"• Queued: {qu_cnt}\n"
            f"• Processing: {ar_cnt}\n"
            f"• Disk Free: {free}"
        )
        return await query.answer(tstats_msg, show_alert=True)
    elif data[1] == 'close':
        await delete_all_messages()
        await query.answer()


bot.add_handler(MessageHandler(mirror_status, filters=command(
    BotCommands.StatusCommand) & CustomFilters.authorized & ~CustomFilters.blacklisted))
bot.add_handler(CallbackQueryHandler(status_pages, filters=regex("^status")))
