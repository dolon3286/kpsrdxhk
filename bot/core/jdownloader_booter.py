from asyncio import sleep
from json import dumps
from random import randint
from re import match
from shutil import which

from aiofiles import open as aiopen
from aiofiles.os import listdir, makedirs, path, rename
from aioshutil import rmtree

from myjd import MyJdApi
from bot import LOGGER, config_dict, bot_name
from bot.helper.ext_utils.bot_utils import cmd_exec, new_task

_MAX_BOOT_RETRIES = 5
_BOOT_RETRY_DELAY = 10


class JDownloader(MyJdApi):
    def __init__(self):
        super().__init__()
        self._device_name = ""
        self.is_connected = False
        self.error = "JDownloader credentials not provided!"

    async def _write_config(self, filename, data):
        async with aiopen(filename, "w") as f:
            await f.write(dumps(data))

    @new_task
    async def boot(self, retries=0):
        await cmd_exec(["pkill", "-9", "-f", "java"])

        email = config_dict.get("JD_EMAIL", "")
        password = config_dict.get("JD_PASS", "")
        if not email or not password:
            self.is_connected = False
            self.error = "JDownloader credentials not provided!"
            LOGGER.warning(self.error)
            return

        self.error = "Connecting to JDownloader..."
        self._device_name = f"{randint(0, 1000)}@{bot_name}"

        if await path.exists("/JDownloader/logs"):
            LOGGER.info("Starting JDownloader...")
        else:
            LOGGER.info("Initializing JDownloader for the first time...")

        jd_config = {
            "autoconnectenabledv2": True,
            "password": password,
            "devicename": self._device_name,
            "email": email,
        }
        remote_config = {
            "localapiserverheaderaccesscontrollalloworigin": "",
            "deprecatedapiport": 3128,
            "localapiserverheaderxcontenttypeoptions": "nosniff",
            "localapiserverheaderxframeoptions": "DENY",
            "externinterfaceenabled": False,
            "deprecatedapilocalhostonly": True,
            "localapiserverheaderreferrerpolicy": "no-referrer",
            "deprecatedapienabled": True,
            "localapiserverheadercontentsecuritypolicy": "default-src 'self'",
            "jdanywhereapienabled": False,
            "externinterfacelocalhostonly": True,
            "localapiserverheaderxxssprotection": "1; mode=block",
        }

        await makedirs("/JDownloader/cfg", exist_ok=True)
        await self._write_config(
            "/JDownloader/cfg/org.jdownloader.api.myjdownloader.MyJDownloaderSettings.json",
            jd_config,
        )
        await self._write_config(
            "/JDownloader/cfg/org.jdownloader.api.RemoteAPIConfig.json",
            remote_config,
        )

        if not await path.exists("/JDownloader/JDownloader.jar"):
            LOGGER.info("JDownloader.jar not found; downloading the official installer...")
            await makedirs("/JDownloader", exist_ok=True)
            _, __, code = await cmd_exec([
                "curl", "-L", "--fail", "--retry", "3",
                "-o", "/JDownloader/JDownloader.jar",
                "https://installer.jdownloader.org/JDownloader.jar",
            ])
            if code != 0 or not await path.exists("/JDownloader/JDownloader.jar"):
                LOGGER.error("Unable to download JDownloader.jar")
                self.error = "JDownloader.jar is missing and automatic download failed!"
                self.is_connected = False
                return

        java_cmd = (
            "java -Xms256m -Xmx500m "
            "-Dsun.jnu.encoding=UTF-8 -Dfile.encoding=UTF-8 "
            "-Djava.awt.headless=true -jar /JDownloader/JDownloader.jar"
        )
        if which("cpulimit"):
            cmd = f"cpulimit -l {config_dict.get('CPU_LIMIT', 20)} -- {java_cmd}"
        else:
            LOGGER.warning("cpulimit is not installed; starting JDownloader without CPU throttling.")
            cmd = java_cmd
        self.is_connected = True
        _, __, code = await cmd_exec(cmd, shell=True)
        self.is_connected = False

        if code != -9 and retries < _MAX_BOOT_RETRIES:
            LOGGER.warning(
                f"JDownloader exited with code {code}; retrying in "
                f"{_BOOT_RETRY_DELAY}s ({retries + 1}/{_MAX_BOOT_RETRIES})"
            )
            await sleep(_BOOT_RETRY_DELAY)
            await self.boot(retries + 1)


jdownloader = JDownloader()
