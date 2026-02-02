import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

def find_steam_root() -> Path | None:
    if sys.platform.startswith("win"):
        # 1) Windows Registry
        try:
            import winreg
            for value_name in ("SteamPath", "InstallPath"):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                        steam_path, _ = winreg.QueryValueEx(k, value_name)
                        p = Path(steam_path)
                        if p.exists():
                            return p
                except FileNotFoundError:
                    pass
        except Exception:
            pass

        candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Steam",
            Path(os.environ.get("PROGRAMFILES", "")) / "Steam",
            Path("C:/Steam"),
        ]
        for p in candidates:
            if p.exists():
                return p

    if sys.platform.startswith("linux"):
        candidates = [
            Path.home() / ".steam" / "steam",
            Path.home() / ".local" / "share" / "Steam",
            Path.home() / ".steam" / "root",
        ]
        for p in candidates:
            if p.exists():
                return p

    if sys.platform == "darwin":
        candidates = [
            Path.home() / "Library" / "Application Support" / "Steam",
        ]
        for p in candidates:
            if p.exists():
                return p

    return None

# ---------------------------
# Helpers
# ---------------------------

def speed(bps: float) -> str:
    mbps = bps / (1024 * 1024)
    if mbps >= 1:
        return f"{mbps:.2f} MB/s"
    kbps = bps / 1024
    return f"{kbps:.0f} KB/s"

def folder_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    try:
        for root, _, files in os.walk(path):
            for fn in files:
                fp = Path(root) / fn
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total

def parse_app_name_from_manifest(manifest_path: Path) -> str | None:
    if not manifest_path.exists():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'"\s*name\s*"\s*"([^"]+)"', text)
        return m.group(1) if m else None
    except Exception:
        return None

# ---------------------------
# Parse download progress from manifest 
# ---------------------------

def parse_manifest_kv(manifest_path: Path) -> dict:
    """
    Простой KV-парсер под appmanifest_*.acf
    """
    kv = {}
    if not manifest_path.exists():
        return kv
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        # пары вида "key"  "value"
        for m in re.finditer(r'"\s*([^"]+)\s*"\s*"([^"]*)\s*"', text):
            k = m.group(1).strip()
            v = m.group(2).strip()
            kv[k] = v
    except Exception:
        pass
    return kv

def get_downloaded_bytes_from_manifest(manifest_path: Path) -> int | None:
    kv = parse_manifest_kv(manifest_path)

    candidates = [
        "BytesDownloaded",
        "bytesdownloaded",
        "DownloadedBytes",
        "downloaded_bytes",
        "BytesToDownload",  
        "SizeOnDisk",      
    ]

    for key in candidates:
        if key in kv:
            s = kv[key]
            if s.isdigit():
                val = int(s)
                return val
    return None

# ---------------------------
# Detect active downloads
# ---------------------------

def detect_active_downloads(steam_root: Path):
    steamapps = steam_root / "steamapps"
    downloading = steamapps / "downloading"
    if not downloading.exists():
        return []

    active = []
    for item in downloading.iterdir():
        if item.is_dir() and item.name.isdigit():
            appid = item.name
            try:
                has_any = any(item.rglob("*"))
            except Exception:
                has_any = True
            if has_any:
                manifest = steamapps / f"appmanifest_{appid}.acf"
                name = parse_app_name_from_manifest(manifest) or f"AppID {appid}"
                active.append((appid, name, item, manifest))
    return active

# ---------------------------
# Pause detection via logs
# ---------------------------

def detect_pause_via_logs(steam_root: Path, appid: str) -> bool:
    log_path = steam_root / "logs" / "content_log.txt"
    if not log_path.exists():
        return False

    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]
        s = "\n".join(lines).lower()
        if appid in s and any(k in s for k in ("paused", "pause", "suspend", "suspended")):
            return True
    except Exception:
        pass
    return False

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
        print("Steam не найден. Укажи путь вручную через STEAM_ROOT.")
        print("Windows PowerShell:  $env:STEAM_ROOT='D:\\Games\\Steam'")
        print("Linux/macOS: export STEAM_ROOT=~/.local/share/Steam")
        return

    print(f"[INFO] Steam root: {steam_root}")

    interval = 60
    repeats = 5

    prev_manifest_metric = {}  # appid -> bytes (from manifest proxy)
    prev_folder_size = {}      # appid -> bytes (from folder size)

    for i in range(repeats):
        ts = datetime.now().strftime("%H:%M:%S")
        active = detect_active_downloads(steam_root)

        if not active:
            print(f"[{ts}] Сейчас нет активных загрузок в Steam (или Steam не качает игру).")
        else:
            for appid, name, folder, manifest in active:
                m_bytes = get_downloaded_bytes_from_manifest(manifest)

                speed_bps = 0.0
                source = ""

                if m_bytes is not None:
                    prev = prev_manifest_metric.get(appid, m_bytes)
                    delta = max(0, m_bytes - prev)
                    speed_bps = delta / interval
                    prev_manifest_metric[appid] = m_bytes
                    source = "manifest"
                else:
                    size_now = folder_size_bytes(folder)
                    prev = prev_folder_size.get(appid, size_now)
                    delta = max(0, size_now - prev)
                    speed_bps = delta / interval
                    prev_folder_size[appid] = size_now
                    source = "folder"

                paused_by_logs = detect_pause_via_logs(steam_root, appid)
                if speed_bps > 0:
                    status = "downloading"
                else:
                    status = "paused" if paused_by_logs else "idle/paused"

                print(
                    f"[{ts}] {name} (appid={appid}) | status={status} | speed={speed(speed_bps)} | source={source}"
                )

        if i < repeats - 1:
            time.sleep(interval)

if __name__ == "__main__":
    main()
