from asyncio import sleep

from bot import bot_cache, jd_listener_lock, jd_downloads, LOGGER
from bot.core.jdownloader_booter import jdownloader
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.mirror_utils.status_utils.jdownloader_status import JDownloaderStatus


@new_task
async def remove_download(gid):
    data = jd_downloads.get(gid)
    if not data:
        return
    try:
        await jdownloader.device.downloads.remove_links(package_ids=data["ids"])
    finally:
        async with jd_listener_lock:
            jd_downloads.pop(gid, None)


@new_task
async def _jd_listener():
    while True:
        await sleep(3)
        async with jd_listener_lock:
            if not jd_downloads:
                bot_cache["jd_listener"] = None
                return
            try:
                packages = await jdownloader.device.downloads.query_packages([
                    {"finished": True, "saveTo": True}
                ])
            except Exception:
                continue
            completed = {p["uuid"] for p in packages if p.get("finished")}
            for gid, data in list(jd_downloads.items()):
                if data.get("status") != "down":
                    continue
                ids = data.get("ids", [])
                if ids and all(x in completed for x in ids):
                    data["status"] = "done"
                    task = download_dict_get(data.get("uid"))
                    if task:
                        try:
                            await task.listener.onDownloadComplete()
                        except Exception as exc:
                            LOGGER.error(f"JDownloader completion callback failed: {exc}")
                    if not bot_cache.get("jd_stop_all"):
                        await jdownloader.device.downloads.remove_links(package_ids=ids)
                    jd_downloads.pop(gid, None)


def download_dict_get(uid):
    from bot import download_dict
    return download_dict.get(uid)


async def on_download_start():
    if not bot_cache.get("jd_listener"):
        bot_cache["jd_listener"] = _jd_listener()
