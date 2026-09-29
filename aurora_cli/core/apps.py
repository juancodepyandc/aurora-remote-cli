"""Memory diagnostics and explicit, bounded process termination."""
import os
import psutil
import click
from aurora_cli import display


def candidates():
    current = psutil.Process()
    protected = {os.getpid(), 0, 1, *(p.pid for p in current.parents())}
    rows = []
    for process in psutil.process_iter(['pid', 'name', 'memory_info', 'username', 'create_time']):
        try:
            if process.pid in protected or process.username() != current.username():
                continue
            info = process.info
            if info['name'] in {'WindowServer', 'loginwindow', 'systemd', 'launchd'}:
                continue
            if info['memory_info']:
                rows.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sorted(rows, key=lambda p: -p['memory_info'].rss)


def show_apps():
    for row in candidates()[:12]:
        display.view.console.print(
            f"{row['pid']:>7}  {row['memory_info'].rss/1024**3:.2f} Go  {row['name']}", markup=False)
    display.hint("/close PID demande un arrêt sans forcer ; enregistre ton travail avant de confirmer.")


def close_app(pid):
    row = next((p for p in candidates() if p['pid'] == pid), None)
    if row is None:
        raise ValueError("Processus absent, protégé ou appartenant à un autre utilisateur.")
    if not click.confirm(f"Demander l'arrêt de {row['name']} (PID {pid}) ?", default=False):
        return False
    process = psutil.Process(pid)
    if process.create_time() != row['create_time']:
        raise ValueError("Le processus a changé ; relance /apps.")
    process.terminate()
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        display.warning("L'application n'a pas quitté. Aucun arrêt forcé n'a été envoyé.")
        return False
    display.success("Application arrêtée. Tu peux reprendre ta demande.")
    return True
