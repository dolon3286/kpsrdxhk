from time import time

from bot import LOGGER, jd_listener_lock, jd_downloads
from bot.core.jdownloader_booter import jdownloader
from bot.helper.ext_utils.bot_utils import MirrorStatus, get_readable_file_size, get_readable_time


async def _query(gid, old):
    try:
        result = await jdownloader.device.downloads.query_packages([{
            "bytesLoaded": True, "bytesTotal": True, "enabled": True,
            "packageUUIDs": jd_downloads[gid]["ids"], "maxResults": -1,
            "running": True, "speed": True, "eta": True, "status": True,
            "hosts": True,
        }])
        if not result:
            return old
        if len(result) == 1:
            return {**old, **result[0], "last_update": time()}
        loaded = sum(x.get("bytesLoaded", 0) for x in result)
        total = sum(x.get("bytesTotal", 0) for x in result)
        speed = sum(x.get("speed", 0) for x in result)
        status = next((x.get("status", "") for x in result if x.get("status")), "")
        name = result[0].get("name", "")
        hosts = result[0].get("hosts")
        if not speed:
            elapsed = time() - old.get("last_update", time())
            speed = (loaded - old.get("bytesLoaded", 0)) / elapsed if elapsed > 0 else 0
        eta = (total - loaded) / speed if speed else 0
        return {
            "name": name, "status": status, "speed": speed, "eta": eta,
            "hosts": hosts, "bytesLoaded": loaded, "bytesTotal": total,
            "last_update": time(),
        }
    except Exception:
        return old


class JDownloaderStatus:
    def __init__(self, listener, gid):
        self.listener = listener
        self._gid = gid
        self._info = {}
        self.engine = "JDownloader"

    async def _update(self):
        self._info = await _query(self._gid, self._info)

    def progress(self):
        total = self._info.get("bytesTotal", 0)
        return f"{round(self._info.get('bytesLoaded', 0) / total * 100, 2)}%" if total else "0%"

    def processed_bytes(self):
        return get_readable_file_size(self._info.get("bytesLoaded", 0))

    def speed(self):
        return f"{get_readable_file_size(self._info.get('speed', 0))}/s"

    def name(self):
        return self._info.get("name") or self.listener.name

    def size(self):
        return get_readable_file_size(self._info.get("bytesTotal", 0))

    def eta(self):
        value = self._info.get("eta")
        return get_readable_time(value) if value else "-"

    async def status(self):
        await self._update()
        state = str(self._info.get("status", "")).lower()
        if "finished" in state or state == "download complete":
            return MirrorStatus.STATUS_DOWNLOADING
        if state in {"", "queued", "waiting", "jdlimit"}:
            return MirrorStatus.STATUS_QUEUEDL if not self._info.get("bytesLoaded") else MirrorStatus.STATUS_DOWNLOADING
        if "error" in state or "failed" in state:
            return MirrorStatus.STATUS_DOWNLOADING
        return MirrorStatus.STATUS_DOWNLOADING

    def task(self):
        return self

    def gid(self):
        return self._gid

    async def cancel_task(self):
        self.listener.is_cancelled = True
        LOGGER.info(f"Cancelling JDownloader task: {self.name()}")
        data = jd_downloads.get(self._gid)
        if data:
            await jdownloader.device.downloads.remove_links(package_ids=data["ids"])
        async with jd_listener_lock:
            jd_downloads.pop(self._gid, None)
        await self.listener.onDownloadError("Cancelled by user!")
