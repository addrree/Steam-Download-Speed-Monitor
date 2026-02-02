# Steam-Download-Speed-Monitor
Cкрипт, который в фоне/консоли отслеживает скорость загрузки игры в Steam.
Каждую минуту (5 раз) выводит:
- какая игра сейчас скачивается (name + appid)
- статус загрузки (downloading / paused)
- скорость (MB/s или KB/s)
- источник расчёта скорости (manifest / folder)

## Как работает
1. Находит Steam независимо от пути установки:
   - Windows: читает путь из реестра `HKCU\Software\Valve\Steam`
   - Linux/macOS: проверяет стандартные директории (Steam root)
2. Определяет активные загрузки по папке `steamapps/downloading/<appid>`.
3. Имя игры берёт из `steamapps/appmanifest_<appid>.acf` (ключ `"name"`).
4. Скорость считает в 2 уровня:
   - **manifest**: пытается взять прогресс-байты из `appmanifest_*.acf` (если такие поля есть)
   - **folder**: fallback — по росту размера `steamapps/downloading/<appid>`
5. Пауза определяется эвристикой по `logs/content_log.txt` + нулевая скорость.

## Запуск
Требуется Python 3.10+.

```bash
python steam_download_monitor.py