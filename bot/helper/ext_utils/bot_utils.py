#!/usr/bin/env python3
import platform
from base64 import b64encode
from datetime import datetime
from os import path as ospath
from pkg_resources import get_distribution, DistributionNotFound
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, path as aiopath, mkdir
from re import match as re_match
from time import time
from html import escape
from uuid import uuid4
from subprocess import run as srun
from psutil import disk_usage, disk_io_counters, Process, cpu_percent, swap_memory, cpu_count, cpu_freq, getloadavg, virtual_memory, net_io_counters, boot_time
from asyncio import create_subprocess_exec, create_subprocess_shell, run_coroutine_threadsafe, sleep
from asyncio.subprocess import PIPE
from functools import partial, wraps
from concurrent.futures import ThreadPoolExecutor

from aiohttp import ClientSession as aioClientSession
from psutil import virtual_memory, cpu_percent, disk_usage
from requests import get as rget
from mega import MegaApi
from pyrogram.enums import ChatType
from pyrogram.types import BotCommand
from pyrogram.errors import PeerIdInvalid

from bot.helper.ext_utils.db_handler import DbManger
from bot.helper.themes import BotTheme
from bot.version import get_version
from bot import OWNER_ID, bot_name, bot_cache, DATABASE_URL, LOGGER, get_client, aria2, download_dict, download_dict_lock, botStartTime, user_data, config_dict, bot_loop, extra_buttons, user
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.telegraph_helper import telegraph
from bot.helper.ext_utils.shortners import short_url

THREADPOOL   = ThreadPoolExecutor(max_workers=1000)
MAGNET_REGEX = r'magnet:\?xt=urn:(btih|btmh):[a-zA-Z0-9]*\s*'
URL_REGEX    = r'^(?!\/)(rtmps?:\/\/|mms:\/\/|rtsp:\/\/|https?:\/\/|ftp:\/\/)?([^\/:]+:[^\/@]+@)?(www\.)?(?=[^\/:\s]+\.[^\/:\s]+)([^\/:\s]+\.[^\/:\s]+)(:\d+)?(\/[^#\s]*[\s\S]*)?(\?[^#\s]*)?(#.*)?$'
SIZE_UNITS   = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
STATUS_START = 0
PAGES        = 1
PAGE_NO      = 1


class MirrorStatus:
    STATUS_UPLOADING   = "Upload"
    STATUS_DOWNLOADING = "Download"
    STATUS_CLONING     = "Clone"
    STATUS_QUEUEDL     = "QueueDL"
    STATUS_QUEUEUP     = "QueueUp"
    STATUS_PAUSED      = "Pause"
    STATUS_ARCHIVING   = "Archive"
    STATUS_EXTRACTING  = "Extract"
    STATUS_SPLITTING   = "Split"
    STATUS_CHECKING    = "CheckUp"
    STATUS_SEEDING     = "Seed"
    STATUS_METADATA    = "Metadata"
    STATUS_ATTACHMENT  = "Attachment"


class setInterval:
    def __init__(self, interval, action):
        self.interval = interval
        self.action = action
        self.task = bot_loop.create_task(self.__set_interval())

    async def __set_interval(self):
        while True:
            await sleep(self.interval)
            await self.action()

    def cancel(self):
        self.task.cancel()


def get_readable_file_size(size_in_bytes):
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1
    return f'{size_in_bytes:.2f}{SIZE_UNITS[index]}' if index > 0 else f'{size_in_bytes}B'


async def getDownloadByGid(gid):
    async with download_dict_lock:
        return next((dl for dl in download_dict.values() if dl.gid() == gid), None)


async def getAllDownload(req_status, user_id=None):
    dls = []
    async with download_dict_lock:
        for dl in list(download_dict.values()):
            if user_id and user_id != dl.message.from_user.id:
                continue
            status = dl.status()
            if req_status in ['all', status]:
                dls.append(dl)
    return dls


async def get_user_tasks(user_id, maxtask):
    if tasks := await getAllDownload('all', user_id):
        return len(tasks) >= maxtask


def bt_selection_buttons(id_):
    gid = id_[:12] if len(id_) > 20 else id_
    pincode = ''.join([n for n in id_ if n.isdigit()][:4])
    buttons = ButtonMaker()
    BASE_URL = config_dict['BASE_URL']
    if config_dict['WEB_PINCODE']:
        buttons.ubutton("Select Files", f"{BASE_URL}/app/files/{id_}")
        buttons.ibutton("Pincode", f"btsel pin {gid} {pincode}")
    else:
        buttons.ubutton("Select Files", f"{BASE_URL}/app/files/{id_}?pin_code={pincode}")
    buttons.ibutton("Cancel", f"btsel rm {gid} {id_}")
    buttons.ibutton("Done Selecting", f"btsel done {gid} {id_}")
    return buttons.build_menu(2)


async def get_telegraph_list(telegraph_content):
    path = [(await telegraph.create_page(title=f"{config_dict['TITLE_NAME']} Drive Search", content=content))["path"] for content in telegraph_content]
    if len(path) > 1:
        await telegraph.edit_telegraph(path, telegraph_content)
    buttons = ButtonMaker()
    buttons.ubutton("🔎 VIEW", f"https://te.legra.ph/{path[0]}")
    buttons, _ = extra_btns(buttons)
    return buttons.build_menu(1)

def handleIndex(index, dic):
    while True:
        if abs(index) >= len(dic):
            if index < 0: index = len(dic) - abs(index)
            elif index > 0: index = index - len(dic)
        else: break
    return index

def get_progress_bar_string(pct):
    try:
        pct = float(str(pct).strip('%'))
    except Exception:
        pct = 0.0
    p = min(max(pct, 0), 100)
    cFull = int(p // 10)
    rem = p % 10
    cPart = 1 if rem >= 3 and cFull < 10 else 0
    p_str = '⬤' * cFull + ('◑' if cPart else '')
    p_str += '○' * (10 - cFull - cPart)
    return f"[{p_str}]"


def parse_time_to_seconds(time_str):
    if not time_str or time_str == '-':
        return 0
    import re
    total = 0
    matches = re.findall(r'(\d+)\s*([dhms])', str(time_str))
    for val, unit in matches:
        val = int(val)
        if unit == 'd': total += val * 86400
        elif unit == 'h': total += val * 3600
        elif unit == 'm': total += val * 60
        elif unit == 's': total += val
    return total


def get_all_versions():
    try:
        result = srun(['7z', '-version'], capture_output=True, text=True)
        vp = result.stdout.split('\n')[2].split(' ')[2]
    except FileNotFoundError:
        vp = ''
    try:
        result = srun([bot_cache['pkgs'][2], '-version'], capture_output=True, text=True)
        vf = result.stdout.split('\n')[0].split(' ')[2].split('ubuntu')[0]
    except FileNotFoundError:
        vf = ''
    try:
        result = srun([bot_cache['pkgs'][3], 'version'], capture_output=True, text=True)
        vr = result.stdout.split('\n')[0].split(' ')[1]
    except FileNotFoundError:
        vr = ''
    try:
        vpy = get_distribution('pyrogram').version
    except DistributionNotFound:
        try:
            vpy = get_distribution('pyrofork').version
        except DistributionNotFound:
            vpy = "2.xx.xx"
    bot_cache['eng_versions'] = {'p7zip':vp, 'ffmpeg': vf, 'rclone': vr,
                                    'aria': aria2.client.get_version()['version'],
                                    'aiohttp': get_distribution('aiohttp').version,
                                    'gapi': get_distribution('google-api-python-client').version,
                                    'mega': MegaApi('test').getVersion(),
                                    'qbit': get_client().app.version,
                                    'pyro': vpy,
                                    'ytdlp': get_distribution('yt-dlp').version}


class EngineStatus:
    def __init__(self):
        if not (version_cache := bot_cache.get('eng_versions')):
            get_all_versions()
            version_cache = bot_cache.get('eng_versions')
        self.STATUS_ARIA = f"Aria2 v{version_cache['aria']}"
        self.STATUS_AIOHTTP = f"AioHttp {version_cache['aiohttp']}"
        self.STATUS_GD = f"Google-API v{version_cache['gapi']}"
        self.STATUS_MEGA = f"MegaSDK v{version_cache['mega']}"
        self.STATUS_QB = f"qBit {version_cache['qbit']}"
        self.STATUS_TG = f"PyroMulti v{version_cache['pyro']}"
        self.STATUS_YT = f"yt-dlp v{version_cache['ytdlp']}"
        self.STATUS_EXT = "pExtract v2"
        self.STATUS_SPLIT_MERGE = f"ffmpeg v{version_cache['ffmpeg']}"
        self.STATUS_ZIP = f"p7zip v{version_cache['p7zip']}"
        self.STATUS_QUEUE = "Sleep v0"
        self.STATUS_RCLONE = f"RClone {version_cache['rclone']}"


def get_readable_message():
    tasks = len(download_dict)
    if tasks == 0:
        return None, None, None, None

    STATUS_LIMIT = config_dict['STATUS_LIMIT']
    globals()['PAGES'] = (tasks + STATUS_LIMIT - 1) // STATUS_LIMIT
    if PAGE_NO > PAGES and PAGES != 0:
        globals()['STATUS_START'] = STATUS_LIMIT * (PAGES - 1)
        globals()['PAGE_NO'] = PAGES

    tasks_msg = ""
    tasks_buttons = ButtonMaker()

    downloads_list = list(download_dict.values())
    visible_downloads = downloads_list[STATUS_START:STATUS_LIMIT+STATUS_START]

    for idx, download in enumerate(visible_downloads, start=STATUS_START + 1):
        msg_link = download.message.link if download.message.chat.type in [
            ChatType.SUPERGROUP, ChatType.CHANNEL] and not config_dict['DELETE_LINKS'] else ''
        elapsed = time() - download.message.date.timestamp()
        elapsed_str = get_readable_time(elapsed)

        name = "Task is being Processed!" if config_dict['SAFE_MODE'] and elapsed >= config_dict['STATUS_UPDATE_INTERVAL'] else escape(f'{download.name()}')
        user = download.message.from_user.mention(style="html")
        user_id = download.message.from_user.id
        link_part = f" [<a href='{msg_link}'>Link</a>]" if msg_link else ""

        # Progress string
        progress_val = download.progress() if hasattr(download, 'progress') else "0%"
        bar_str = get_progress_bar_string(progress_val)

        # Mode parsing
        mode_raw = download.upload_details.get('mode', '')
        if '|' in mode_raw:
            parts = [m.strip() for m in mode_raw.split('|', 1)]
            out_mode, in_mode = parts[0], parts[1]
        else:
            out_mode = mode_raw.strip() or '#Leech'
            in_mode = '#Aria2'

        # Status text with source/engine
        status_val = download.status()
        eng_name = download.eng()
        if in_mode == '#Mega' or 'mega' in eng_name.lower():
            status_display = f"{status_val} (mega.nz)"
        elif 'drive' in eng_name.lower() or in_mode == '#GDrive':
            status_display = f"{status_val} (drive.google.com)"
        elif hasattr(download, 'source_url') and download.source_url and download.source_url.startswith('http'):
            try:
                from urllib.parse import urlparse
                domain = urlparse(download.source_url).netloc
                status_display = f"{status_val} ({domain})" if domain else status_val
            except Exception:
                status_display = status_val
        else:
            status_display = status_val

        # Time formatting: Elapsed of Total ( Eta )
        eta_val = download.eta() if hasattr(download, 'eta') else '-'
        eta_sec = parse_time_to_seconds(eta_val)
        if eta_sec > 0 and eta_val != '-':
            total_sec = int(elapsed) + eta_sec
            time_display = f"{elapsed_str} of {get_readable_time(total_sec)} ( {eta_val} )"
        else:
            time_display = f"{elapsed_str}"

        tasks_msg += f"<b>{idx}. {name}</b>\n"
        tasks_msg += f"↬ Task By {user} ( #ID{user_id} ){link_part}\n\n"

        if status_val == MirrorStatus.STATUS_SEEDING:
            tasks_msg += f"{bar_str} 100%\n\n"
            table_rows = (
                f"<tr><td>⇥ Uploaded</td><td>{download.uploaded_bytes()} of {download.size()}</td></tr>\n"
                f"<tr><td>↳ Status</td><td>{status_display}</td></tr>\n"
                f"<tr><td>↳ Speed</td><td>{download.upload_speed()}</td></tr>\n"
                f"<tr><td>↳ Time</td><td>{download.seeding_time()} ( Ratio: {download.ratio()} )</td></tr>\n"
                f"<tr><td>↳ Engine</td><td>{eng_name}</td></tr>\n"
                f"<tr><td>↳ In Mode</td><td>{in_mode}</td></tr>\n"
                f"<tr><td>↳ Out Mode</td><td>#Seed</td></tr>\n"
            )
        elif status_val not in [MirrorStatus.STATUS_SPLITTING, MirrorStatus.STATUS_SEEDING]:
            tasks_msg += f"{bar_str} {progress_val}\n\n"
            table_rows = (
                f"<tr><td>⇥ Processed</td><td>{download.processed_bytes()} of {download.size()}</td></tr>\n"
                f"<tr><td>↳ Status</td><td>{status_display}</td></tr>\n"
                f"<tr><td>↳ Speed</td><td>{download.speed()}</td></tr>\n"
                f"<tr><td>↳ Time</td><td>{time_display}</td></tr>\n"
                f"<tr><td>↳ Engine</td><td>{eng_name}</td></tr>\n"
                f"<tr><td>↳ In Mode</td><td>{in_mode}</td></tr>\n"
                f"<tr><td>↳ Out Mode</td><td>{out_mode}</td></tr>\n"
            )
            if hasattr(download, 'seeders_num'):
                try:
                    table_rows += f"<tr><td>↳ Seeds/Leechs</td><td>{download.seeders_num()} / {download.leechers_num()}</td></tr>\n"
                except Exception:
                    pass
        else:
            tasks_msg += f"{bar_str} {progress_val}\n\n"
            table_rows = (
                f"<tr><td>⇥ Size</td><td>{download.size()}</td></tr>\n"
                f"<tr><td>↳ Status</td><td>{status_display}</td></tr>\n"
                f"<tr><td>↳ Elapsed</td><td>{elapsed_str}</td></tr>\n"
                f"<tr><td>↳ Engine</td><td>{eng_name}</td></tr>\n"
                f"<tr><td>↳ In Mode</td><td>{in_mode}</td></tr>\n"
                f"<tr><td>↳ Out Mode</td><td>{out_mode}</td></tr>\n"
            )

        tasks_msg += f"<table>\n{table_rows}</table>\n\n"

        # Cancel button for this task
        tasks_buttons.ibutton(f"Cancel #{idx}", f"status can {download.gid()}")
        if eng_name.startswith("qBit"):
            tasks_buttons.ibutton(f"Select #{idx}", f"btsel {download.gid()}")

    # Message 2: Bot Stats
    currentTime = get_readable_time(time() - botStartTime)
    free_bytes = disk_usage(config_dict['DOWNLOAD_DIR']).free
    free = get_readable_file_size(free_bytes)
    free_p = round(100 - disk_usage(config_dict['DOWNLOAD_DIR']).percent, 1)

    stats_msg = (
        "₪ <b>Bot Stats</b>\n"
        f"<b>Page:</b> {PAGE_NO}/{PAGES} | <b>Tasks:</b> {tasks} | <b>Step:</b> 1\n\n"
        "<table>\n"
        "<tr><th>⌘ CPU</th><th>∞ Free</th><th>⬝⬝ RAM</th><th>↳ UP</th></tr>\n"
        f"<tr><td>{cpu_percent()}%</td><td>{free} [{free_p}%]</td><td>{virtual_memory().percent}%</td><td>{currentTime}</td></tr>\n"
        "</table>"
    )

    stats_buttons = ButtonMaker()
    stats_buttons.ibutton("<<", "status pre")
    stats_buttons.ibutton(">>", "status nex")
    stats_buttons.ibutton(BotTheme('TSTATS'), "status tstats")
    stats_buttons.ibutton(BotTheme('REFRESH'), "status ref")

    return tasks_msg.strip(), tasks_buttons.build_menu(2), stats_msg.strip(), stats_buttons.build_menu(4)


async def turn_page(data):
    STATUS_LIMIT = config_dict['STATUS_LIMIT']
    global STATUS_START, PAGE_NO
    async with download_dict_lock:
        if data[1] == "nex":
            if PAGE_NO == PAGES:
                STATUS_START = 0
                PAGE_NO = 1
            else:
                STATUS_START += STATUS_LIMIT
                PAGE_NO += 1
        elif data[1] == "pre":
            if PAGE_NO == 1:
                STATUS_START = STATUS_LIMIT * (PAGES - 1)
                PAGE_NO = PAGES
            else:
                STATUS_START -= STATUS_LIMIT
                PAGE_NO -= 1


def get_readable_time(seconds):
    periods = [('d', 86400), ('h', 3600), ('m', 60), ('s', 1)]
    result = ''
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f'{int(period_value)}{period_name}'
    return result


def is_magnet(url):
    return bool(re_match(MAGNET_REGEX, url))


def is_url(url):
    return bool(re_match(URL_REGEX, url))


def is_gdrive_link(url):
    return "drive.google.com" in url


def is_telegram_link(url):
    return url.startswith(('https://t.me/', 'https://telegram.me/', 'https://telegram.dog/', 'https://telegram.space/', 'tg://openmessage?user_id='))


def is_share_link(url):
    return bool(re_match(r'https?:\/\/.+\.gdtot\.\S+|https?:\/\/(.+\.filepress|filebee|appdrive|gdflix|www.jiodrive)\.\S+', url))


def is_index_link(url): 
     return bool(re_match(r'https?:\/\/.+\/\d+\:\/', url))    


def is_mega_link(url):
    return "mega.nz" in url or "mega.co.nz" in url


def is_rclone_path(path):
    return bool(re_match(r'^(mrcc:)?(?!magnet:)(?![- ])[a-zA-Z0-9_\. -]+(?<! ):(?!.*\/\/).*$|^rcl$', path))


def get_mega_link_type(url):
    return "folder" if "folder" in url or "/#F!" in url else "file"


def arg_parser(items, arg_base):
    if not items:
        return arg_base
    bool_arg_set = {'-b', '-e', '-z', '-s', '-j', '-d'}
    t = len(items)
    i = 0
    arg_start = -1

    while i + 1 <= t:
        part = items[i].strip()
        if part in arg_base:
            if arg_start == -1:
                arg_start = i
            if i + 1 == t and part in bool_arg_set or part in ['-s', '-j']:
                arg_base[part] = True
            else:
                sub_list = []
                for j in range(i + 1, t):
                    item = items[j].strip()
                    if item in arg_base:
                        if part in bool_arg_set and not sub_list:
                            arg_base[part] = True
                        break
                    sub_list.append(item.strip())
                    i += 1
                if sub_list:
                    arg_base[part] = " ".join(sub_list)
        i += 1

    link = []
    if items[0].strip() not in arg_base:
        if arg_start == -1:
            link.extend(item.strip() for item in items)
        else:
            link.extend(items[r].strip() for r in range(arg_start))
        if link:
            arg_base['link'] = " ".join(link)
    return arg_base


async def get_content_type(url):
    try:
        async with aioClientSession(trust_env=True) as session:
            async with session.get(url, verify_ssl=False) as response:
                return response.headers.get('Content-Type')
    except Exception:
        return None


def update_user_ldata(id_, key=None, value=None):
    exception_keys = ['is_sudo', 'is_auth', 'dly_tasks', 'is_blacklist', 'token', 'time']
    if key is None and value is None:
        if id_ in user_data:
            updated_data = {}
            for k, v in user_data[id_].items():
                if k in exception_keys:
                    updated_data[k] = v
            user_data[id_] = updated_data
        return
    user_data.setdefault(id_, {})
    user_data[id_][key] = value


async def download_image_url(url):
    path = "Images/"
    if not await aiopath.isdir(path):
        await mkdir(path)
    image_name = url.split('/')[-1]
    des_dir = ospath.join(path, image_name)
    async with aioClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                async with aiopen(des_dir, 'wb') as file:
                    async for chunk in response.content.iter_chunked(1024):
                        await file.write(chunk)
                LOGGER.info(f"Image Downloaded Successfully as {image_name}")
            else:
                LOGGER.error(f"Failed to Download Image from {url}")
    return des_dir


async def cmd_exec(cmd, shell=False):
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    stdout = stdout.decode().strip()
    stderr = stderr.decode().strip()
    return stdout, stderr, proc.returncode


def new_task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return bot_loop.create_task(func(*args, **kwargs))
    return wrapper


async def sync_to_async(func, *args, wait=True, **kwargs):
    pfunc = partial(func, *args, **kwargs)
    future = bot_loop.run_in_executor(THREADPOOL, pfunc)
    return await future if wait else future


def async_to_sync(func, *args, wait=True, **kwargs):
    future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
    return future.result() if wait else future


def new_thread(func):
    @wraps(func)
    def wrapper(*args, wait=False, **kwargs):
        future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
        return future.result() if wait else future
    return wrapper


async def compare_versions(v1, v2):
    v1_parts = [int(part) for part in v1.split('-')[0][1:].split('.')]
    v2_parts = [int(part) for part in v2.split('-')[0][1:].split('.')]
    for i in range(3):
        v1_part, v2_part = v1_parts[i], v2_parts[i]
        if v1_part < v2_part:
            return "New Version Update is Available! Check Now!"
        elif v1_part > v2_part:
            return "More Updated! Kindly Contribute in Official"
    return "Already up to date with latest version"


async def get_stats(event, key="home"):
    user_id = event.from_user.id
    btns = ButtonMaker()
    btns.ibutton('Back', f'kpsmlx {user_id} stats home')
    if key == "home":
        btns = ButtonMaker()
        btns.ibutton('Bot Stats', f'kpsmlx {user_id} stats stbot')
        btns.ibutton('OS Stats', f'kpsmlx {user_id} stats stsys')
        btns.ibutton('Repo Stats', f'kpsmlx {user_id} stats strepo')
        btns.ibutton('Bot Limits', f'kpsmlx {user_id} stats botlimits')
        msg = "⌬ <b><i>Bot & OS Statistics!</i></b>"
    elif key == "stbot":
        total, used, free, disk = disk_usage('/')
        swap = swap_memory()
        memory = virtual_memory()
        disk_io = disk_io_counters()
        msg = BotTheme(
            'BOT_STATS',
            bot_uptime=get_readable_time(time() - botStartTime),
            ram_bar=get_progress_bar_string(memory.percent),
            ram=memory.percent,
            ram_u=get_readable_file_size(memory.used),
            ram_f=get_readable_file_size(memory.available),
            ram_t=get_readable_file_size(memory.total),
            swap_bar=get_progress_bar_string(swap.percent),
            swap=swap.percent,
            swap_u=get_readable_file_size(swap.used),
            swap_f=get_readable_file_size(swap.free),
            swap_t=get_readable_file_size(swap.total),
            disk=disk,
            disk_bar=get_progress_bar_string(disk),
            disk_read=f"{get_readable_file_size(disk_io.read_bytes)} ({get_readable_time(disk_io.read_time / 1000)})"
            if disk_io
            else "Access Denied",
            disk_write=f"{get_readable_file_size(disk_io.write_bytes)} ({get_readable_time(disk_io.write_time / 1000)})"
            if disk_io
            else "Access Denied",
            disk_t=get_readable_file_size(total),
            disk_u=get_readable_file_size(used),
            disk_f=get_readable_file_size(free),
        )
    elif key == "stsys":
        cpuUsage = cpu_percent(interval=0.5)
        msg = BotTheme('SYS_STATS',
            os_uptime=get_readable_time(time() - boot_time()),
            os_version=platform.version(),
            os_arch=platform.platform(),
            up_data=get_readable_file_size(net_io_counters().bytes_sent),
            dl_data=get_readable_file_size(net_io_counters().bytes_recv),
            pkt_sent=str(net_io_counters().packets_sent)[:-3],
            pkt_recv=str(net_io_counters().packets_recv)[:-3],
            tl_data=get_readable_file_size(net_io_counters().bytes_recv + net_io_counters().bytes_sent),
            cpu=cpuUsage,
            cpu_bar=get_progress_bar_string(cpuUsage),
            cpu_freq=f"{cpu_freq(percpu=False).current / 1000:.2f} GHz" if cpu_freq() else "Access Denied",
            sys_load="%, ".join(str(round((x / cpu_count() * 100), 2)) for x in getloadavg()) + "%, (1m, 5m, 15m)",
            p_core=cpu_count(logical=False),
            v_core=cpu_count(logical=True) - cpu_count(logical=False),
            total_core=cpu_count(logical=True),
            cpu_use=len(Process().cpu_affinity()),
        )
    elif key == "strepo":
        last_commit, changelog = 'No Data', 'N/A'
        if await aiopath.exists('.git'):
            last_commit = (await cmd_exec("git log -1 --pretty='%cd ( %cr )' --date=format-local:'%d/%m/%Y'", True))[0]
            changelog = (await cmd_exec("git log -1 --pretty=format:'<code>%s</code> <b>By</b> %an'", True))[0]
        official_v = (await cmd_exec("curl -o latestversion.py https://github.com/Tamilupdates/KPSML-X/raw/refs/heads/kpsmlx/bot/version.py -s && python3 latestversion.py && rm latestversion.py", True))[0]
        msg = BotTheme('REPO_STATS',
            last_commit=last_commit,
            bot_version=get_version(),
            lat_version=official_v,
            commit_details=changelog,
            remarks=await compare_versions(get_version(), official_v),
        )
    elif key == "botlimits":
        msg = BotTheme('BOT_LIMITS',
                DL = ('∞' if (val := config_dict['DIRECT_LIMIT']) == '' else val),
                TL = ('∞' if (val := config_dict['TORRENT_LIMIT']) == '' else val),
                GL = ('∞' if (val := config_dict['GDRIVE_LIMIT']) == '' else val),
                YL = ('∞' if (val := config_dict['YTDLP_LIMIT']) == '' else val),
                PL = ('∞' if (val := config_dict['PLAYLIST_LIMIT']) == '' else val),
                CL = ('∞' if (val := config_dict['CLONE_LIMIT']) == '' else val),
                ML = ('∞' if (val := config_dict['MEGA_LIMIT']) == '' else val),
                LL = ('∞' if (val := config_dict['LEECH_LIMIT']) == '' else val),
                TV  = ('Disabled' if (val := config_dict['TOKEN_TIMEOUT']) == '' else get_readable_time(val)),
                UTI = ('Disabled' if (val := config_dict['USER_TIME_INTERVAL']) == 0 else get_readable_time(val)),
                UT = ('∞' if (val := config_dict['USER_MAX_TASKS']) == '' else val),
                BT = ('∞' if (val := config_dict['BOT_MAX_TASKS']) == '' else val),
        )
    btns.ibutton('Close', f'kpsmlx {user_id} close')
    return msg, btns.build_menu(2)


async def getdailytasks(user_id, increase_task=False, upleech=0, upmirror=0, check_mirror=False, check_leech=False):
    task, lsize, msize = 0, 0, 0
    if user_id in user_data and user_data[user_id].get('dly_tasks'):
        userdate, task, lsize, msize = user_data[user_id]['dly_tasks']
        nowdate = datetime.now()
        if userdate.year <= nowdate.year and userdate.month <= nowdate.month and userdate.day < nowdate.day:
            task, lsize, msize = 0, 0, 0
            if increase_task:
                task = 1
            elif upleech != 0:
                lsize += upleech
            elif upmirror != 0:
                msize += upmirror
        elif increase_task:
            task += 1
        elif upleech != 0:
            lsize += upleech
        elif upmirror != 0:
            msize += upmirror
    elif increase_task:
        task += 1
    elif upleech != 0:
        lsize += upleech
    elif upmirror != 0:
        msize += upmirror
    update_user_ldata(user_id, 'dly_tasks', [datetime.now(), task, lsize, msize])
    if DATABASE_URL:
        await DbManger().update_user_data(user_id)
    if check_leech:
        return lsize
    elif check_mirror:
        return msize
    return task


async def fetch_user_tds(user_id, force=False):
    user_dict = user_data.get(user_id, {})
    if config_dict['USER_TD_MODE'] and user_dict.get('td_mode', False) or force:
        return user_dict.get('user_tds', {})
    return {}


async def fetch_user_dumps(user_id):
    user_dict = user_data.get(user_id, {})
    if (dumps := user_dict.get('ldump', False)):
        if not isinstance(dumps, dict):
            update_user_ldata(user_id, 'ldump', {})
            return {}
        return dumps
    return {}


async def checking_access(user_id, button=None):
    if not config_dict['TOKEN_TIMEOUT'] or bool(user_id == OWNER_ID or user_id in user_data and user_data[user_id].get('is_sudo')):
        return None, button
    user_data.setdefault(user_id, {})
    data = user_data[user_id]
    expire = data.get('time')
    if config_dict['LOGIN_PASS'] is not None and data.get('token', '') == config_dict['LOGIN_PASS']:
        return None, button
    isExpired = (expire is None or expire is not None and (time() - expire) > config_dict['TOKEN_TIMEOUT'])
    if isExpired:
        token = data['token'] if expire is None and 'token' in data else str(uuid4())
        if expire is not None:
            del data['time']
        data['token'] = token
        user_data[user_id].update(data)
        if button is None:
            button = ButtonMaker()
        encrypt_url = b64encode(f"{token}&&{user_id}".encode()).decode()
        button.ubutton('Generate New Token', short_url(f'https://t.me/{bot_name}?start={encrypt_url}'))
        return f'<i>Temporary Token has been expired,</i> Kindly generate a New Temp Token to start using bot Again.\n<b>Validity :</b> <code>{get_readable_time(config_dict["TOKEN_TIMEOUT"])}</code>', button
    return None, button


def extra_btns(buttons, already=False):
    if extra_buttons and not already:
        for btn_name, btn_url in extra_buttons.items():
            buttons.ubutton(btn_name, btn_url, 'l_body')
    return buttons, True



async def set_commands(client):
    if not config_dict['SET_COMMANDS']:
        return
    try:
        bot_cmds = [
            BotCommand(
                BotCommands.MirrorCommand[0],
                f'or /{BotCommands.MirrorCommand[1]} Mirror [links/media/rclone_path]',
            ),
            BotCommand(
                BotCommands.LeechCommand[0],
                f'or /{BotCommands.LeechCommand[1]} Leech [links/media/rclone_path]',
            ),
            BotCommand(
                BotCommands.QbMirrorCommand[0],
                f'or /{BotCommands.QbMirrorCommand[1]} Mirror magnet/torrent using qBittorrent',
            ),
            BotCommand(
                BotCommands.QbLeechCommand[0],
                f'or /{BotCommands.QbLeechCommand[1]} Leech magnet/torrent using qBittorrent',
            ),
            BotCommand(
                BotCommands.YtdlCommand[0],
                f'or /{BotCommands.YtdlCommand[1]} Mirror yt-dlp supported links via bot',
            ),
            BotCommand(
                BotCommands.YtdlLeechCommand[0],
                f'or /{BotCommands.YtdlLeechCommand[1]} Leech yt-dlp supported links via bot',
            ),
            BotCommand(
                BotCommands.CloneCommand[0],
                f'or /{BotCommands.CloneCommand[1]} Copy file/folder to Drive (GDrive/RClone)',
            ),
            BotCommand(
                BotCommands.CountCommand,
                '[drive_url]: Count file/folder of Google Drive/RClone Drives',
            ),
            BotCommand(
                BotCommands.StatusCommand[0],
                f'or /{BotCommands.StatusCommand[1]} Get Bot All Status Stats Message',
            ),
            BotCommand(
                BotCommands.StatsCommand[0],
                f'or /{BotCommands.StatsCommand[1]} Check Bot & System stats',
            ),
            BotCommand(
                BotCommands.BtSelectCommand,
                'Select files to download only torrents/magnet qbit/aria2c',
            ),
            BotCommand(
                BotCommands.CategorySelect,
                'Select Upload Category with UserTD or Bot Categories to upload only GDrive upload',
            ),
            BotCommand(BotCommands.CancelMirror, 'Cancel a Task of yours!'),
            BotCommand(
                BotCommands.CancelAllCommand[0],
                'Cancel all Tasks in whole Bots.',
            ),
            BotCommand(BotCommands.ListCommand, 'Search in Drive(s)'),
            BotCommand(
                BotCommands.SearchCommand,
                'Search in Torrent via qBit clients!',
            ),
            BotCommand(
                BotCommands.HelpCommand,
                'Get detailed help about the KPSML-X Bot',
            ),
            BotCommand(
                BotCommands.UserSetCommand[0],
                f"or /{BotCommands.UserSetCommand[1]} User's Personal Settings (Open in PM)",
            ),
            BotCommand(
                BotCommands.AniListCommand,
                'Search Animes on AniList.com and fetch details',
            ),
            BotCommand(
                BotCommands.MyDramaListCommand,
                'Search Dramas on MyDramaList.com and fetch details',
            ),
            BotCommand(
                BotCommands.SpeedCommand[0],
                f'or /{BotCommands.SpeedCommand[1]} Check Server Up & Down Speed & Details',
            ),
            BotCommand(
                BotCommands.MediaInfoCommand[0],
                f'or /{BotCommands.MediaInfoCommand[1]} Generate Mediainfo for Replied Media or DL links',
            ),
            BotCommand(
                BotCommands.BotSetCommand[0],
                f"or /{BotCommands.BotSetCommand[1]} Bot's Personal Settings (Owner or Sudo Only)",
            ),
            BotCommand(
                BotCommands.RestartCommand[0],
                f'or /{BotCommands.RestartCommand[1]} Restart & Update the Bot (Owner or Sudo Only)',
            ),
        ]
        if config_dict['SHOW_EXTRA_CMDS']:
            bot_cmds.insert(1, BotCommand(BotCommands.MirrorCommand[2], f'or /{BotCommands.MirrorCommand[3]} Mirror and UnZip [links/media/rclone_path]'))
            bot_cmds.insert(1, BotCommand(BotCommands.MirrorCommand[4], f'or /{BotCommands.MirrorCommand[5]} Mirror and Zip [links/media/rclone_path]'))
            bot_cmds.insert(4, BotCommand(BotCommands.LeechCommand[2], f'or /{BotCommands.LeechCommand[3]} Leech and UnZip [links/media/rclone_path]'))
            bot_cmds.insert(4, BotCommand(BotCommands.LeechCommand[4], f'or /{BotCommands.LeechCommand[5]} Leech and Zip [links/media/rclone_path]'))
            bot_cmds.insert(7, BotCommand(BotCommands.QbMirrorCommand[2], f'or /{BotCommands.QbMirrorCommand[3]} Mirror magnet/torrent and UnZip using qBit'))
            bot_cmds.insert(7, BotCommand(BotCommands.QbMirrorCommand[4], f'or /{BotCommands.QbMirrorCommand[5]} Mirror magnet/torrent and Zip using qBit'))
            bot_cmds.insert(10, BotCommand(BotCommands.QbLeechCommand[2], f'or /{BotCommands.QbLeechCommand[3]} Leech magnet/torrent and UnZip using qBit'))
            bot_cmds.insert(10, BotCommand(BotCommands.QbLeechCommand[4], f'or /{BotCommands.QbLeechCommand[5]} Leech magnet/torrent and Zip using qBit'))
            bot_cmds.insert(13, BotCommand(BotCommands.YtdlCommand[2], f'or /{BotCommands.YtdlCommand[3]} Mirror yt-dlp supported links and Zip via bot'))
            bot_cmds.insert(13, BotCommand(BotCommands.YtdlLeechCommand[2], f'or /{BotCommands.YtdlLeechCommand[3]} Leech yt-dlp supported links and Zip via bot'))
        await client.set_bot_commands(bot_cmds)
        LOGGER.info('Bot Commands have been Set & Updated')
    except Exception as err:
        LOGGER.error(err)
