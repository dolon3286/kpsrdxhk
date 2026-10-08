from asyncio import Event, sleep
from base64 import b64encode
from secrets import token_urlsafe

from pyrogram.enums import ButtonStyle
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler
from aiofiles import open as aiopen
from aiofiles.os import path as aiopath, remove

from myjd.exception import MYJDException
from bot import (
    bot, LOGGER, jd_listener_lock, jd_downloads, download_dict,
    download_dict_lock, queue_dict_lock, queued_dl, non_queued_dl,
    config_dict,
)
from bot.core.jdownloader_booter import jdownloader
from bot.helper.ext_utils.bot_utils import new_task, get_readable_file_size
from bot.helper.ext_utils.task_manager import is_queued, start_from_queued, limit_checker, stop_duplicate_check
from bot.helper.listeners.jdownloader_listener import on_download_start
from bot.helper.mirror_utils.status_utils.jdownloader_status import JDownloaderStatus
from bot.helper.mirror_utils.status_utils.queue_status import QueueStatus
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import sendMessage, sendStatusMessage, editMessage, deleteMessage


async def get_jd_download_directory():
    result = await jdownloader.device.config.get(
        "org.jdownloader.settings.GeneralSettings", None, "DefaultDownloadFolder"
    )
    return f"/{str(result).strip('/')}/"


def trim_path(value):
    return "/".join(component[:255] for component in value.split("/"))


async def get_online_packages(path, downloads=False):
    if downloads:
        packages = await jdownloader.device.downloads.query_packages([{"saveTo": True}])
    else:
        packages = await jdownloader.device.linkgrabber.query_packages([{"saveTo": True}])
    return [
        item["uuid"] for item in packages
        if item.get("saveTo", "").startswith(path)
    ]


class JDownloaderHelper:
    def __init__(self, listener):
        self.listener = listener
        self.event = Event()
        self.message = None

    async def wait_for_configurations(self):
        buttons = ButtonMaker()
        buttons.ubutton("Select", "https://my.jdownloader.org")
        buttons.ibutton("Done Selecting", "jdq sdone", "header")
        buttons.ibutton("Cancel", "jdq cancel", "header")
        self.message = await sendMessage(
            self.listener.message,
            f"Remove unwanted files/change variants/edit names for <b>{self.listener.name}</b> "
            "on My.JDownloader. Do not start the download manually, then press Done Selecting.",
            buttons.build_menu(2),
        )

        async def callback(_, query):
            if query.from_user.id != self.listener.user_id:
                return await query.answer("Not Yours!", show_alert=True)
            await query.answer()
            action = query.data.split()[1]
            if action == "sdone":
                self.event.set()
            else:
                self.listener.is_cancelled = True
                self.event.set()
                await editMessage(query.message, "Task has been cancelled.")

        handler = self.listener.message._client.add_handler(
            CallbackQueryHandler(callback, filters=regex("^jdq") & user(self.listener.user_id)),
            group=-1,
        )
        try:
            await self.event.wait()
        finally:
            self.listener.message._client.remove_handler(*handler)
        if not self.listener.is_cancelled and self.message:
            await deleteMessage(self.message)
        return not self.listener.is_cancelled


async def add_jd_download(listener, path):
    gid = token_urlsafe(12)
    try:
        async with jd_listener_lock:
            if not jdownloader.is_connected:
                raise MYJDException(jdownloader.error)

            default_path = await get_jd_download_directory()
            jd_downloads[gid] = {"status": "collect", "path": path, "uid": listener.uid}

            existing = await jdownloader.device.linkgrabber.query_packages([{}])
            if existing:
                old = [
                    x["uuid"] for x in existing
                    if x.get("saveTo", "").startswith(default_path)
                ]
                if old:
                    await jdownloader.device.linkgrabber.remove_links(package_ids=old)

            if await aiopath.exists(listener.link):
                async with aiopen(listener.link, "rb") as f:
                    content = b64encode(await f.read()).decode()
                await jdownloader.device.linkgrabber.add_container(
                    "DLC", f"data:;base64,{content}"
                )
            else:
                await jdownloader.device.linkgrabber.add_links([{
                    "autoExtract": False,
                    "links": listener.link,
                    "deepDecrypt": True,
                    "overwritePackagizerRules": listener.join,
                }])

            await sleep(1)
            while await jdownloader.device.linkgrabber.is_collecting():
                await sleep(0.5)

            online = []
            corrupted = []
            unknown = False
            name = ""

            for _ in range(180):
                packages = await jdownloader.device.linkgrabber.query_packages([{
                    "bytesTotal": True,
                    "saveTo": True,
                    "availableOnlineCount": True,
                    "availableOfflineCount": True,
                    "availableTempUnknownCount": True,
                    "availableUnknownCount": True,
                }])
                for package in packages:
                    if package.get("onlineCount", 1) == 0:
                        corrupted.append(package["uuid"])
                        continue
                    unknown |= bool(
                        package.get("tempUnknownCount", 0)
                        or package.get("unknownCount", 0)
                        or package.get("offlineCount", 0)
                    )
                    listener.size += package.get("bytesTotal", 0)
                    online.append(package["uuid"])
                    name = name or package.get("name", "").replace("/", "").split("/")[0]
                    save_to = package.get("saveTo", "")
                    if save_to.startswith(default_path):
                        await jdownloader.device.linkgrabber.set_download_directory(
                            trim_path(save_to).replace(default_path, f"{path}/", 1),
                            [package["uuid"]],
                        )
                if online:
                    break
                await sleep(0.5)

            if not online:
                if corrupted:
                    await jdownloader.device.linkgrabber.remove_links(package_ids=corrupted)
                raise MYJDException(name or "JDownloader could not find an online downloadable link")

            jd_downloads[gid]["ids"] = online

            if unknown:
                links = await jdownloader.device.linkgrabber.query_links(
                    [{"packageUUIDs": online, "availability": True}]
                )
                bad = [x["uuid"] for x in links if x.get("availability", "").lower() != "online"]
                if bad or corrupted:
                    await jdownloader.device.linkgrabber.remove_links(bad, corrupted)

        listener.name = listener.name or name

        duplicate, button = await stop_duplicate_check(listener.name, listener)
        if duplicate:
            await jdownloader.device.linkgrabber.remove_links(package_ids=online)
            await listener.onDownloadError(duplicate, button)
            return

        limit = await limit_checker(listener.size, listener)
        if limit:
            await jdownloader.device.linkgrabber.remove_links(package_ids=online)
            await listener.onDownloadError(limit)
            return

        if listener.select:
            if not await JDownloaderHelper(listener).wait_for_configurations():
                await jdownloader.device.linkgrabber.remove_links(package_ids=online)
                return
            online = await get_online_packages(path)
            if not online:
                raise MYJDException("All selected files were removed from LinkGrabber.")
            async with jd_listener_lock:
                jd_downloads[gid]["ids"] = online

        queued, event = await is_queued(listener.uid)
        if queued:
            async with download_dict_lock:
                download_dict[listener.uid] = QueueStatus(
                    listener.name, listener.size, gid, listener, "Dl"
                )
            await listener.onDownloadStart()
            await sendStatusMessage(listener.message)
            await event.wait()
            if listener.is_cancelled:
                return

        if listener.name and listener.name != name:
            links = await jdownloader.device.linkgrabber.query_links([{"packageUUIDs": online}])
            if len(links) == 1:
                old = links[0].get("name", "")
                ext = old.rsplit(".", 1)[-1] if "." in old else ""
                new_name = listener.name
                if ext and not new_name.lower().endswith(f".{ext.lower()}"):
                    new_name = f"{new_name}.{ext}"
                await jdownloader.device.linkgrabber.rename_link(links[0]["uuid"], new_name)
            else:
                for package_id in online:
                    await jdownloader.device.linkgrabber.rename_package(package_id, listener.name)
                    await jdownloader.device.linkgrabber.set_download_directory(
                        f"{path}/{listener.name}", [package_id]
                    )

        await jdownloader.device.linkgrabber.move_to_downloadlist(package_ids=online)
        await sleep(0.5)
        online = await get_online_packages(path, True)
        if not online:
            raise MYJDException("JDownloader did not move the task to its download list.")

        async with jd_listener_lock:
            jd_downloads[gid] = {"status": "down", "path": path, "ids": online, "uid": listener.uid}

        await jdownloader.device.downloads.force_download(package_ids=online)
        async with download_dict_lock:
            download_dict[listener.uid] = JDownloaderStatus(listener, gid)

        if queued:
            await start_from_queued()
        else:
            await listener.onDownloadStart()
            await sendStatusMessage(listener.message)

        await on_download_start()
        LOGGER.info(f"JDownloader download started: {listener.name} ({get_readable_file_size(listener.size)})")

    except BaseException as exc:
        LOGGER.error(f"JDownloader error: {exc}")
        async with jd_listener_lock:
            jd_downloads.pop(gid, None)
        await listener.onDownloadError(str(exc))
    finally:
        if await aiopath.exists(listener.link):
            await remove(listener.link)
