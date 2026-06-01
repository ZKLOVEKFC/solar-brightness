#!/usr/bin/env python3
"""
solar-brightness — 基于太阳高度角 + 天气的外接显示器自适应亮度调节
=====================================================================

核心原理:
  1. 通过 IP 自动定位 (或手动) 获取经纬度
  2. 计算当前时刻的太阳高度角 → 基础亮度曲线
  3. 获取 Open-Meteo 免费天气 API 的云量/天气 → 亮度修正
  4. 通过 m1ddc (DDC/CI) 逐台控制外接显示器亮度
  5. 每 5 分钟渐变，每次最多变 3-5%，实现平滑过渡

依赖: m1ddc (brew install m1ddc), pyyaml (pip3 install pyyaml)
用法: python3 solar-brightness.py [--once|--install|--help]
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

VERSION = "1.0.0"
PROJECT = "solar-brightness"
GITHUB = "https://github.com/zklovekfc/solar-brightness"

# ── 路径 ────────────────────────────────────────────────────
BASE_DIR = Path(os.path.expanduser(f"~/.config/{PROJECT}"))
CONFIG_PATH = BASE_DIR / "config.yaml"
CACHE_DIR = BASE_DIR / "cache"
LOCATION_CACHE = CACHE_DIR / "location.json"
WEATHER_CACHE = CACHE_DIR / "weather.json"
STATE_FILE = CACHE_DIR / "state.json"
LOG_FILE = BASE_DIR / f"{PROJECT}.log"
LAUNCHD_PLIST = Path(os.path.expanduser(f"~/Library/LaunchAgents/com.{PROJECT}.plist"))

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 全局 logger，main() 中初始化
log: logging.Logger = None  # type: ignore


# ╔══════════════════════════════════════════════════════════════╗
# ║                       日志 + 工具                            ║
# ╚══════════════════════════════════════════════════════════════╝

class RotatingFileHandler(logging.FileHandler):
    def __init__(self, filename, max_lines=500, **kwargs):
        super().__init__(filename, **kwargs)
        self.max_lines = max_lines

    def emit(self, record):
        try:
            with open(self.baseFilename, "r") as f:
                lines = f.readlines()
        except (FileNotFoundError, PermissionError):
            lines = []
        if len(lines) >= self.max_lines:
            with open(self.baseFilename, "w") as f:
                f.writelines(lines[-self.max_lines // 2:])
        super().emit(record)


def setup_logging(level="INFO"):
    global log
    logger = logging.getLogger(PROJECT)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(str(LOG_FILE), max_lines=500)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    log = logger
    return logger


def _which(cmd):
    for p in os.environ.get("PATH", "").split(":") + [
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]:
        full = os.path.join(p, cmd)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    return None


_M1DDC = _which("m1ddc") or "/opt/homebrew/bin/m1ddc"
_SYSTEM_PROFILER = _which("system_profiler") or "/usr/sbin/system_profiler"


def http_get(url, proxy=None, timeout=10, no_proxy=False):
    cmd = ["curl", "-fsSL", "--connect-timeout", str(timeout), "--max-time", str(timeout)]
    if no_proxy:
        cmd += ["--noproxy", "*"]
    elif proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║                     配置                                     ║
# ╚══════════════════════════════════════════════════════════════╝

DEFAULT_CONFIG = """\
# solar-brightness 配置文件
# 文档: {github}

global:
  location:
    mode: auto                # auto | manual
    lat: null                 # manual 模式下的纬度
    lon: null                 # manual 模式下的经度
    cache_days: 7

  weather:
    enabled: true
    cache_minutes: 30

  brightness:
    night_min: 35             # 深夜最低亮度 %
    day_max: 100              # 晴天正午最高亮度 %
    curve_power: 0.7          # 曲线形状: <1 黄昏更平滑, >1 正午更集中
    weather_effect: 0.45      # 天气影响: 0=不管天气, 1=完全跟随

  transition:
    max_step_up: 5            # 每次最多升高 %
    max_step_down: 3          # 每次最多降低 %
    tick_minutes: 5           # 执行间隔(分钟)

  network:
    proxy: null               # http://127.0.0.1:7897 或 null
    timeout: 10

  logging:
    level: INFO               # DEBUG | INFO | WARNING | ERROR

displays:
  "H24T09P":
    name: "桌面主屏"
    max_nits: 400
    min_pct: 10
    max_pct: 100
    offset: 0

  "default":
    name: "未命名显示器"
    max_nits: 400
    min_pct: 10
    max_pct: 100
    offset: 0
"""


def config_create():
    """首次安装时创建配置文件。"""
    if CONFIG_PATH.exists():
        return False
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        f.write(DEFAULT_CONFIG.format(github=GITHUB))
    return True


def config_load():
    import yaml
    if not CONFIG_PATH.exists():
        print(f"❌ 配置不存在: {CONFIG_PATH}", file=sys.stderr)
        print(f"   运行: python3 {__file__} --install", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    g = raw.get("global", {})
    return {
        "location": g.get("location", {}),
        "weather": g.get("weather", {}),
        "brightness": g.get("brightness", {}),
        "transition": g.get("transition", {}),
        "network": g.get("network", {}),
        "logging": g.get("logging", {}),
        "displays": raw.get("displays", {"default": {}}),
    }


def cfg_proxy(cfg):
    return cfg.get("network", {}).get("proxy") or None


def cfg_timeout(cfg):
    return cfg.get("network", {}).get("timeout", 10)


# ╔══════════════════════════════════════════════════════════════╗
# ║                     定位                                     ║
# ╚══════════════════════════════════════════════════════════════╝

GEOCODING_CACHE = {}

CITY_COORDS_CN = {
    "北京": (39.9042, 116.4074), "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644), "深圳": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551), "南京": (32.0617, 118.7778),
    "成都": (30.5728, 104.0668), "武汉": (30.5928, 114.3055),
    "重庆": (29.4316, 106.9123), "天津": (39.3434, 117.3616),
    "苏州": (31.2990, 120.5853), "西安": (34.3416, 108.9398),
    "长沙": (28.2282, 112.9388), "郑州": (34.7466, 113.6254),
    "青岛": (36.0671, 120.3826), "大连": (38.9140, 121.6147),
    "厦门": (24.4798, 118.0894), "福州": (26.0745, 119.2965),
    "济南": (36.6512, 116.9972), "合肥": (31.8206, 117.2272),
    "昆明": (25.0389, 102.7183), "贵阳": (26.6477, 106.6302),
    "南宁": (22.8170, 108.3665), "海口": (20.0440, 110.1999),
    "石家庄": (38.0428, 114.5149), "太原": (37.8706, 112.5489),
    "沈阳": (41.8057, 123.4315), "长春": (43.8171, 125.3235),
    "哈尔滨": (45.8038, 126.5350), "兰州": (36.0611, 103.8343),
    "乌鲁木齐": (43.8256, 87.6168), "呼和浩特": (40.8424, 111.7490),
    "拉萨": (29.6500, 91.1000), "西宁": (36.6171, 101.7782),
    "银川": (38.4872, 106.2309), "南昌": (28.6820, 115.8579),
    "宁波": (29.8683, 121.5440), "无锡": (31.4912, 120.3119),
    "东莞": (23.0208, 113.7518), "佛山": (23.0218, 113.1215),
    "温州": (28.0015, 120.6994), "常州": (31.8101, 119.9741),
}


def geocode_city(city_name, proxy=None, timeout=10):
    if city_name in GEOCODING_CACHE:
        return GEOCODING_CACHE[city_name]
    if city_name in CITY_COORDS_CN:
        GEOCODING_CACHE[city_name] = CITY_COORDS_CN[city_name]
        return CITY_COORDS_CN[city_name]
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote(city_name)}&count=1&format=json"
    resp = http_get(url, proxy=proxy, timeout=timeout)
    if resp:
        try:
            data = json.loads(resp)
            if data.get("results"):
                r = data["results"][0]
                GEOCODING_CACHE[city_name] = (r["latitude"], r["longitude"])
                return GEOCODING_CACHE[city_name]
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    return None


def locate_ipip(timeout=10):
    url = "https://myip.ipip.net"
    resp = http_get(url, no_proxy=True, timeout=timeout)
    if resp:
        m = re.search(r"来自于：(.+?)\s+(\S+)$", resp)
        if m:
            parts = m.group(1).split()
            return parts[-1] if parts else parts[0] if parts else None
    return None


def locate_ip_api(no_proxy=False, proxy=None, timeout=5):
    url = "http://ip-api.com/json/?fields=status,lat,lon,city,country"
    resp = http_get(url, no_proxy=no_proxy, proxy=proxy if not no_proxy else None, timeout=timeout)
    if resp:
        try:
            data = json.loads(resp)
            if data.get("status") == "success":
                return (data["lat"], data["lon"], data.get("city", ""))
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def resolve_location(cfg):
    loc_cfg = cfg.get("location", {})
    proxy = cfg_proxy(cfg)
    timeout = cfg_timeout(cfg)

    if loc_cfg.get("mode") == "manual":
        lat, lon = loc_cfg.get("lat"), loc_cfg.get("lon")
        if lat is not None and lon is not None:
            log.info("📍 手动位置: %.4f, %.4f", lat, lon)
            return (lat, lon)

    cache_days = loc_cfg.get("cache_days", 7)
    if LOCATION_CACHE.exists():
        try:
            with open(LOCATION_CACHE) as f:
                c = json.load(f)
            age = (time.time() - c["timestamp"]) / 86400
            if age < cache_days:
                log.info("📍 缓存位置 (%.1f天前): %.4f, %.4f (%s)", age, c["lat"], c["lon"], c.get("city", ""))
                return (c["lat"], c["lon"])
        except (json.JSONDecodeError, KeyError):
            pass

    # 优先 ipip.net（国内真实IP）
    city = locate_ipip(timeout=timeout)
    if city:
        geo = geocode_city(city, proxy=proxy, timeout=timeout)
        if geo:
            _save_location_cache(geo[0], geo[1], city)
            log.info("📍 ipip.net: %s → %.4f, %.4f", city, *geo)
            return geo

    # 备用直连 ip-api
    result = locate_ip_api(no_proxy=True, timeout=5)
    if result:
        _save_location_cache(*result)
        log.info("📍 直连: %.4f, %.4f (%s)", *result)
        return (result[0], result[1])

    # 代理 ip-api
    if proxy:
        result = locate_ip_api(proxy=proxy, timeout=timeout)
        if result:
            _save_location_cache(*result)
            log.info("📍 代理: %.4f, %.4f (%s) ⚠️", *result)
            return (result[0], result[1])

    # 过期缓存兜底
    if LOCATION_CACHE.exists():
        try:
            with open(LOCATION_CACHE) as f:
                c = json.load(f)
            log.warning("⚠️ 使用过期缓存: %.4f, %.4f", c["lat"], c["lon"])
            return (c["lat"], c["lon"])
        except (json.JSONDecodeError, KeyError):
            pass

    log.error("❌ 无法定位。请设置 manual lat/lon 或检查网络。")
    sys.exit(1)


def _save_location_cache(lat, lon, city=""):
    with open(LOCATION_CACHE, "w") as f:
        json.dump({"lat": lat, "lon": lon, "city": city, "timestamp": time.time()}, f)


# ╔══════════════════════════════════════════════════════════════╗
# ║                     天气                                     ║
# ╚══════════════════════════════════════════════════════════════╝

def fetch_weather(lat, lon, cfg):
    proxy = cfg_proxy(cfg)
    timeout = cfg_timeout(cfg)
    cache_minutes = cfg.get("weather", {}).get("cache_minutes", 30)

    if WEATHER_CACHE.exists():
        try:
            with open(WEATHER_CACHE) as f:
                c = json.load(f)
            if (time.time() - c["timestamp"]) / 60 < cache_minutes:
                return {"cloud_cover": c["cloud_cover"], "weather_code": c["weather_code"]}
        except (json.JSONDecodeError, KeyError):
            pass

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&current=cloud_cover,weather_code&timezone=auto"
    )
    resp = http_get(url, proxy=proxy, timeout=timeout)
    if resp:
        try:
            data = json.loads(resp)
            cur = data.get("current", {})
            result = {"cloud_cover": cur.get("cloud_cover", 0), "weather_code": cur.get("weather_code", 0)}
            with open(WEATHER_CACHE, "w") as f:
                json.dump({**result, "timestamp": time.time()}, f)
            return result
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def weather_factor(cloud_cover, weather_code, effect):
    if effect <= 0:
        return 1.0
    cloud_factor = 1.0 - (cloud_cover / 100.0) * effect
    penalties = {45: 0.15, 48: 0.15, 51: 0.10, 53: 0.10, 55: 0.10, 61: 0.15, 63: 0.15,
                 65: 0.15, 71: 0.10, 73: 0.10, 75: 0.10, 77: 0.10, 80: 0.12, 81: 0.12,
                 82: 0.12, 95: 0.20, 96: 0.20, 99: 0.20}
    penalty = penalties.get(int(weather_code), 0.0) if weather_code else 0.0
    return max(0.4, min(1.0, cloud_factor - penalty))


# ╔══════════════════════════════════════════════════════════════╗
# ║                   太阳位置 (Spencer 1971)                     ║
# ╚══════════════════════════════════════════════════════════════╝

def solar_declination(doy):
    b = math.radians((360 / 365) * (doy - 81))
    return math.degrees(math.asin(math.sin(math.radians(23.45)) * math.sin(b)))


def solar_elevation_angle(lat, lon, dt_local):
    tz_offset = -time.timezone / 3600.0
    dt_utc = dt_local - timedelta(hours=tz_offset)
    hour_utc = dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0

    decl = math.radians(solar_declination(dt_local.timetuple().tm_yday))
    ha = math.radians(15.0 * (hour_utc - 12.0) + lon)

    sin_elev = math.sin(math.radians(lat)) * math.sin(decl) + math.cos(math.radians(lat)) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))


# ╔══════════════════════════════════════════════════════════════╗
# ║                     亮度计算                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def target_brightness(elev, cloud_cover, weather_code, cfg):
    b = cfg.get("brightness", {})
    night_min, day_max = b.get("night_min", 35), b.get("day_max", 100)
    curve, we = b.get("curve_power", 0.7), b.get("weather_effect", 0.45)

    if elev <= 0:
        base = night_min
    else:
        base = night_min + (day_max - night_min) * (math.sin(math.radians(elev)) ** curve)

    if cfg.get("weather", {}).get("enabled", True):
        return round(base * weather_factor(cloud_cover, weather_code, we), 1)
    return round(base, 1)


# ╔══════════════════════════════════════════════════════════════╗
# ║                   DDC/CI 显示器控制                          ║
# ╚══════════════════════════════════════════════════════════════╝

def builtin_display_uuids():
    uuids = set()
    try:
        r = subprocess.run([_SYSTEM_PROFILER, "SPDisplaysDataType", "-json"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for gpu in json.loads(r.stdout).get("SPDisplaysDataType", []):
                for d in gpu.get("spdisplays_ndrvs", []):
                    if "internal" in d.get("spdisplays_connection_type", "").lower():
                        uid = d.get("_spdisplays_display-uuid", "")
                        if uid:
                            uuids.add(uid)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return uuids


def display_list():
    try:
        r = subprocess.run([_M1DDC, "display", "list", "detailed"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return []
        builtins = builtin_display_uuids()
        displays = []
        for line in r.stdout.strip().split("\n"):
            m = re.match(r"\[(\d+)\]\s+(.+?)\s+\(([^)]+)\)", line)
            if m:
                idx, name, ident = int(m.group(1)), m.group(2).strip(), m.group(3).strip()
                if name == "(null)" or any(b in ident for b in builtins):
                    continue
                displays.append((idx, name, ident))
        return displays
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def display_get(idx):
    try:
        r = subprocess.run([_M1DDC, "display", str(idx), "get", "luminance"],
                           capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        return None


def display_set(idx, value):
    value = max(0, min(100, int(round(value))))
    try:
        r = subprocess.run([_M1DDC, "display", str(idx), "set", "luminance", str(value)],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def find_display_config(name, ident, cfg):
    displays_cfg = cfg.get("displays", {})
    for key, dc in displays_cfg.items():
        if key == "default":
            continue
        if key.lower() in name.lower() or key.lower() in ident.lower():
            return dc
    return displays_cfg.get("default", {})


# ╔══════════════════════════════════════════════════════════════╗
# ║                     主逻辑                                   ║
# ╚══════════════════════════════════════════════════════════════╝

def run_once(cfg):
    """执行一次亮度调节。"""
    lat, lon = resolve_location(cfg)

    cloud_cover, weather_code = 0, 0
    if cfg.get("weather", {}).get("enabled", True):
        w = fetch_weather(lat, lon, cfg)
        if w:
            cloud_cover, weather_code = w["cloud_cover"], w["weather_code"]
            log.info("🌤️ 云量: %d%%, 天气码: %d", cloud_cover, weather_code)
        else:
            log.info("🌤️ 天气不可用，假设晴天")

    elev = solar_elevation_angle(lat, lon, datetime.now())
    target = target_brightness(elev, cloud_cover, weather_code, cfg)
    log.info("☀️ 太阳高度角: %.1f° → 目标亮度: %.1f%%", elev, target)

    displays = display_list()
    if not displays:
        log.warning("⚠️ 未发现 DDC/CI 可控显示器")
        return

    log.info("🖥️ %d 台显示器", len(displays))
    state = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    tc = cfg.get("transition", {})
    for (ddc_idx, name, ident) in displays:
        dc = find_display_config(name, ident, cfg)
        dname = dc.get("name", name)
        min_pct = dc.get("min_pct", 10)
        max_pct = dc.get("max_pct", 100)
        offset = dc.get("offset", 0)

        ft = max(min_pct, min(max_pct, target + offset))
        cur = display_get(ddc_idx)
        if cur is None:
            log.warning("  %s: 无法读取亮度", dname)
            continue

        diff = ft - cur
        if abs(diff) < 1:
            continue

        step = max(diff, -tc.get("max_step_down", 3)) if diff < 0 else min(diff, tc.get("max_step_up", 5))
        new_val = max(0, min(100, round(cur + step)))

        if display_set(ddc_idx, new_val):
            log.info("  ✅ %s: %d%% → %d%% (步进 %+d%%, 目标 %.0f%%)", dname, cur, new_val, step, ft)
            state[ident or name] = {"brightness": new_val, "timestamp": time.time()}
        else:
            log.warning("  ❌ %s: 设置失败", dname)

    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except (PermissionError, OSError):
        pass


# ╔══════════════════════════════════════════════════════════════╗
# ║                   CLI                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def cmd_install():
    """安装 launchd 定时任务和服务。"""
    script_path = Path(__file__).resolve()
    cfg_created = config_create()

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.{PROJECT}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>{script_path}</string>
        <string>--once</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>{os.path.expanduser('~')}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{BASE_DIR}/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>{BASE_DIR}/launchd.log</string>
    <key>LowPriorityIO</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""

    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(LAUNCHD_PLIST, "w") as f:
        f.write(plist_content)

    subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"""
╔══════════════════════════════════════════════════╗
║  ✅ {PROJECT} 安装完成！                          ║
╠══════════════════════════════════════════════════╣
║  配置文件: {CONFIG_PATH}
║  定时任务: {LAUNCHD_PLIST}
║  运行日志: {LOG_FILE}
║  运行间隔: 每 5 分钟
╚══════════════════════════════════════════════════╝

下一步:
  1. 编辑配置:  vim {CONFIG_PATH}
  2. 手动测试:  python3 {script_path} --once
  3. 查看日志:  tail -f {LOG_FILE}

管理:
  暂停: launchctl unload {LAUNCHD_PLIST}
  恢复: launchctl load {LAUNCHD_PLIST}
  立即: launchctl start com.{PROJECT}
""")
    if cfg_created:
        print(f"ℹ️  已创建默认配置文件，请根据你的显示器编辑 {CONFIG_PATH}")


def cmd_status():
    """显示当前状态。"""
    print(f"{PROJECT} v{VERSION}\n")
    print(f"配置文件: {CONFIG_PATH} {'✅' if CONFIG_PATH.exists() else '❌'}")
    print(f"定时任务: {LAUNCHD_PLIST} {'✅' if LAUNCHD_PLIST.exists() else '❌'}")
    print(f"位置缓存: {LOCATION_CACHE} {'✅' if LOCATION_CACHE.exists() else '❌'}")
    print(f"天气缓存: {WEATHER_CACHE} {'✅' if WEATHER_CACHE.exists() else '❌'}")

    if LAUNCHD_PLIST.exists():
        r = subprocess.run(["launchctl", "list", f"com.{PROJECT}"], capture_output=True, text=True)
        print(f"运行状态: {'✅ 运行中' if r.returncode == 0 else '❌ 未加载'}")
        if r.returncode == 0:
            print(r.stdout.split("\n")[0] if r.stdout else "")

    displays = display_list() if _M1DDC and os.path.exists(_M1DDC) else []
    if displays:
        print(f"\n显示器 ({len(displays)} 台):")
        for idx, name, ident in displays:
            cur = display_get(idx)
            print(f"  [{idx}] {name}: {cur}% (UUID: {ident[:20]}...)")
    else:
        print("\n⚠️  未发现 DDC/CI 可控显示器 (m1ddc 未安装或显示器不支持)")

    # 显示最后日志
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f:
                lines = f.readlines()
            if lines:
                print("\n📋 最近日志:")
                for line in lines[-5:]:
                    print(f"  {line.rstrip()}")
        except (FileNotFoundError, PermissionError):
            pass


def main():
    parser = argparse.ArgumentParser(
        prog=PROJECT,
        description="基于太阳高度角 + 天气的外接显示器自适应亮度调节",
        epilog=f"项目主页: {GITHUB}",
    )
    parser.add_argument("--once", action="store_true", help="执行一次亮度调节后退出")
    parser.add_argument("--install", action="store_true", help="安装 launchd 定时任务")
    parser.add_argument("--uninstall", action="store_true", help="卸载定时任务")
    parser.add_argument("--status", action="store_true", help="显示当前状态")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    args = parser.parse_args()

    if args.uninstall:
        if LAUNCHD_PLIST.exists():
            subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            LAUNCHD_PLIST.unlink()
            print(f"✅ 已卸载 {PROJECT}")
        else:
            print("未安装")
        return

    if args.install:
        cmd_install()
        return

    if args.status:
        cmd_status()
        return

    # 默认模式: 单次运行
    if args.once or True:  # 默认即单次运行
        cfg = config_load()
        setup_logging(cfg.get("logging", {}).get("level", "INFO"))
        log.info("━━━━ %s v%s ━━━━", PROJECT, VERSION)
        run_once(cfg)
        log.info("━━━━ 完成 ━━━━")


if __name__ == "__main__":
    main()
