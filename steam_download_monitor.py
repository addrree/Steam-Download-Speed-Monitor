import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------
# Steam path discovery
# ---------------------------

def find_steam_root():
    if sys.platform.startswith("win"):
        try:
            import winreg
            for v in ("SteamPath", "InstallPath"):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                        p = Path(winreg.QueryValueEx(k, v)[0])
                        if p.exists():
                            return p
                except FileNotFoundError:
                    pass
        except Exception:
            pass

        for p in [
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Steam",
            Path(os.environ.get("PROGRAMFILES", "")) / "Steam",
            Path("C:/Steam"),
        ]:
            if p.exists():
                return p

    if sys.platform.startswith("linux"):
        for p in [Path.home()/".local/share/Steam", Path.home()/".steam/steam", Path.home()/".steam/root"]:
            if p.exists():
                return p

    if sys.platform == "darwin":
        p = Path.home() / "Library/Application Support/Steam"
        if p.exists():
            return p

    return None

# ---------------------------
# Active downloads + name
# ---------------------------

def parse_manifest_name(manifest_path: Path):
    if not manifest_path.exists():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'"\s*name\s*"\s*"([^"]+)"', text)
        return m.group(1) if m else None
    except Exception:
        return None

def detect_active_downloads(steam_root: Path, recent_seconds: int):
    steamapps = steam_root / "steamapps"
    downloading = steamapps / "downloading"
    if not downloading.exists():
        return []

    now = time.time()
    active = []
    for d in downloading.iterdir():
        if d.is_dir() and d.name.isdigit():
            try:
                non_empty = any(d.rglob("*"))
            except Exception:
                non_empty = True
            if not non_empty:
                continue

            try:
                mtime = d.stat().st_mtime
            except OSError:
                mtime = now

            if now - mtime > recent_seconds:
                continue

            appid = d.name
            manifest = steamapps / f"appmanifest_{appid}.acf"
            name = parse_manifest_name(manifest) or f"AppID {appid}"
            active.append((appid, name))
    return active

# ---------------------------
# Incremental log reading + speed parsing
# ---------------------------

_SPEED_PATTERNS = [
    re.compile(r'(\d+(?:\.\d+)?)\s*(GB/s|MB/s|KB/s|B/s)\b', re.IGNORECASE),
    re.compile(r'(\d+(?:\.\d+)?)\s*(Gbps|Mbps|Kbps|bps)\b', re.IGNORECASE),
]

def speed_to_bps(val: float, unit: str):
    u = unit.lower()
    if u == "b/s":
        return val
    if u == "kb/s":
        return val * 1024
    if u == "mb/s":
        return val * 1024 * 1024
    if u == "gb/s":
        return val * 1024 * 1024 * 1024

    if u == "bps":
        return val / 8
    if u == "kbps":
        return (val * 1000) / 8
    if u == "mbps":
        return (val * 1_000_000) / 8
    if u == "gbps":
        return (val * 1_000_000_000) / 8
    return None

def parse_last_speed_bps(text: str):
    last = None
    for pat in _SPEED_PATTERNS:
        for m in pat.finditer(text):
            bps = speed_to_bps(float(m.group(1)), m.group(2))
            if bps is not None:
                last = bps
    return last

class LogTailer:
    def __init__(self, path: Path, start_at_end: bool = True):
        self.path = path
        self.offset = 0
        self.last_append_ts = 0.0 
        if start_at_end:
            self.seek_end()

    def seek_end(self):
        if self.path.exists():
            try:
                self.offset = self.path.stat().st_size
            except OSError:
                self.offset = 0
        else:
            self.offset = 0

    def read_new_text(self, max_bytes: int = 200_000) -> str:
        if not self.path.exists():
            return ""
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0

            if size == self.offset:
                return ""

            with self.path.open("rb") as f:
                f.seek(self.offset, os.SEEK_SET)
                data = f.read(max_bytes)

            if data:
                self.offset += len(data)
                self.last_append_ts = time.time()
                return data.decode("utf-8", errors="ignore")
            return ""
        except Exception:
            return ""

def speed(bps: float) -> str:
    mbps = bps / (1024 * 1024)
    if mbps >= 1:
        return f"{mbps:.2f} MB/s"
    kbps = bps / 1024
    return f"{kbps:.0f} KB/s"

# ---------------------------
# Main
# ---------------------------

def main():
    steam_root = None
    env_root = os.environ.get("STEAM_ROOT")
    if env_root:
        p = Path(env_root)
        if p.exists():
            steam_root = p
    if steam_root is None:
        steam_root = find_steam_root()

    if not steam_root:
        print("Steam не найден. Укажи путь через STEAM_ROOT.")
        return

    print(f"[INFO] Steam root: {steam_root}")

    logs_dir = steam_root / "logs"
    tailers = [
        LogTailer(logs_dir / "download_log.txt", start_at_end=True),
        LogTailer(logs_dir / "content_log.txt", start_at_end=True),
    ]

    interval = 60         
    repeats = 5           
    STALE_SECONDS = 10     # если лог не дописывался >10 секунд -> считаем скорость 0
    ACTIVE_FOLDER_RECENT = 60  # downloading/<appid> должен меняться за последнюю минуту

    last_speed_bps = 0.0

    for i in range(repeats):
        ts = datetime.now().strftime("%H:%M:%S")

        active = detect_active_downloads(steam_root, recent_seconds=ACTIVE_FOLDER_RECENT)
        new_text = ""
        for t in tailers:
            new_text += "\n" + t.read_new_text()
        new_speed = parse_last_speed_bps(new_text) if new_text.strip() else None
        if new_speed is not None:
            last_speed_bps = new_speed
        last_append = max(t.last_append_ts for t in tailers)
        if time.time() - last_append > STALE_SECONDS:
            last_speed_bps = 0.0

        status = "downloading" if last_speed_bps > 0 else "paused/idle"

        if not active:
            print(f"[{ts}] Нет активных загрузок. | {status} | speed={speed(last_speed_bps)}")
        else:
            for appid, name in active:
                print(f"[{ts}] {name}  | {status} | speed={speed(last_speed_bps)} | source=logs(realtime)")

        if i < repeats - 1:
            time.sleep(interval)

if __name__ == "__main__":
    main()
