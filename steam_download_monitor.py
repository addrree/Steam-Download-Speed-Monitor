import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

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

def parse_kv(manifest_path: Path) -> dict:
    kv = {}
    if not manifest_path.exists():
        return kv
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'"\s*([^"]+)\s*"\s*"([^"]*)\s*"', text):
            kv[m.group(1).strip()] = m.group(2).strip()
    except Exception:
        pass
    return kv

def get_int(kv: dict, key: str):
    v = kv.get(key)
    return int(v) if v and v.isdigit() else None

def bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n/(1024**3):.2f} GB"
    if n >= 1024**2:
        return f"{n/(1024**2):.2f} MB"
    if n >= 1024:
        return f"{n/1024:.1f} KB"
    return f"{n} B"

def speed(bps: float) -> str:
    mbps = bps / (1024 * 1024)
    if mbps >= 1:
        return f"{mbps:.2f} MB/s"
    kbps = bps / 1024
    return f"{kbps:.0f} KB/s"

def detect_active_downloads(steam_root: Path):
    steamapps = steam_root / "steamapps"
    downloading = steamapps / "downloading"
    if not downloading.exists():
        return []

    active = []
    for d in downloading.iterdir():
        if d.is_dir() and d.name.isdigit():
            appid = d.name
            manifest = steamapps / f"appmanifest_{appid}.acf"
            kv = parse_kv(manifest)
            name = kv.get("name") or f"AppID {appid}"
            # считаем активным, если есть прогресс-ключи или папка не пустая
            has_progress = any(k in kv for k in ("BytesToDownload", "BytesDownloaded", "BytesToStage", "BytesStaged"))
            try:
                non_empty = any(d.rglob("*"))
            except Exception:
                non_empty = True
            if has_progress or non_empty:
                active.append((appid, name, manifest))
    return active

def status_from_deltas(dd: int, ds: int) -> str:
    # dd: delta downloaded, ds: delta staged
    if dd > 0:
        return "downloading"
    if ds > 0:
        return "staging/installing"
    return "paused/idle"

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

    interval = 60
    repeats = 5

    prev_downloaded = {}  # appid -> bytes
    prev_staged = {}      # appid -> bytes

    for i in range(repeats):
        ts = datetime.now().strftime("%H:%M:%S")
        active = detect_active_downloads(steam_root)

        if not active:
            print(f"[{ts}] Сейчас нет активных загрузок.")
        else:
            for appid, name, manifest in active:
                kv = parse_kv(manifest)

                bd = get_int(kv, "BytesDownloaded") or 0
                bt = get_int(kv, "BytesToDownload") or 0
                bs = get_int(kv, "BytesStaged") or 0
                bst = get_int(kv, "BytesToStage") or 0

                bd_prev = prev_downloaded.get(appid, bd)
                bs_prev = prev_staged.get(appid, bs)

                d_bd = max(0, bd - bd_prev)
                d_bs = max(0, bs - bs_prev)

                speed_bps = d_bd / interval
                staged_bps = d_bs / interval

                status = status_from_deltas(d_bd, d_bs)

                # прогресс в %
                pct = ""
                if bt > 0:
                    pct = f"{(bd / bt * 100):.1f}%"

                # печать: “за минуту скачано N” тоже даём
                print(
                    f"[{ts}] {name} (appid={appid}) | {status} | "
                    f"downloaded: {bytes(bd)}/{bytes(bt)} {pct} | "
                    f"+{bytes(d_bd)}/min ({speed(speed_bps)}) | "
                    f"staged +{bytes(d_bs)}/min ({speed(staged_bps)})"
                )

                prev_downloaded[appid] = bd
                prev_staged[appid] = bs

        if i < repeats - 1:
            time.sleep(interval)

if __name__ == "__main__":
    main()
