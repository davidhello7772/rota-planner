"""Start Rota Planner: ``python3 run.py`` (or ``python3 -m rotaplanner``)."""

from __future__ import annotations

import argparse
import atexit
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import updates
from .app import App
from .demo import build_demo
from .exporters import pdf
from .instance import Instance, running_note, wake
from .service import Service
from .store import Store

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = 3  # exit code: the window could not open and nothing else could be shown
RESTART = 75   # (inside the program only) a new version was installed: start again
DEFAULT_PORT = 8765
TOKEN_FILE = ".session-token"
RUNNING_FILE = ".running"  # read by install-rota-planner.sh: no update while the program is open


def new_session_token(root: Path) -> str:
    """Replace the session token (the old one may have been seen)."""
    path = root / TOKEN_FILE
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def session_token(root: Path) -> str:
    """A secret kept with the data, so a bookmarked address keeps working."""
    path = root / TOKEN_FILE
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    root.mkdir(parents=True, exist_ok=True)
    return new_session_token(root)


def forget_old_access(root: Path) -> None:
    """Older versions could set a password and let other computers connect
    (``access.json``). Rota Planner now answers this computer only: that file
    (a password's hash, a list of addresses) is no longer used, so it goes."""
    try:
        (root / "access.json").unlink()
    except OSError:
        pass  # not there, or not removable here: it is ignored anyway


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="rota-planner", description="Prepare the monthly rota.")
    parser.add_argument("--data", type=Path, help="data folder (default: ./data next to the program)")
    parser.add_argument("--demo", action="store_true", help="use demonstration data with invented names")
    parser.add_argument("--reset-demo", action="store_true", help="rebuild the demonstration data first")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"preferred port (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--window", action="store_true",
                        help="show Rota Planner's own window (status, Start, Stop) instead of using this terminal")
    # Started again by itself after an update: the page already open expects this port.
    parser.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def private_folder(root: Path) -> None:
    """The data folder is for this user only (it holds people's details)."""
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass  # not every file system has permissions (Windows keeps its own)


def mark_running(root: Path) -> None:
    """Note this process's number in the data folder while it runs, so the
    updater (``install-rota-planner.sh``) waits until the program is closed.
    Only one Rota Planner runs per data folder (``instance.py``), so the note
    is simply this one's; a note left by a crash is harmless: the updater
    checks that the process is still Rota Planner."""
    marker = root / RUNNING_FILE
    mine = f"{os.getpid()}\n"
    try:
        marker.write_text(mine, encoding="utf-8")
    except OSError:
        return

    def remove() -> None:
        try:
            if marker.read_text(encoding="utf-8") == mine:
                marker.unlink()
        except OSError:
            pass

    atexit.register(remove)


def start_page(root: Path, url: str) -> Path:
    """A small private page that sends the browser to the program. Opening it,
    rather than the address itself, keeps the session token off the browser's
    command line, where other users of the computer could read it."""
    page = root / "start.html"
    page.write_text('<!doctype html><meta charset="utf-8"><title>Rota Planner</title>'
                    f'<meta http-equiv="refresh" content="0;url={url}">'
                    f'<p>Opening Rota Planner… <a href="{url}">Click here if nothing happens.</a></p>', encoding="utf-8")
    return page


def open_in_browser(root: Path, url: str) -> None:
    webbrowser.open(start_page(root, url).as_uri())


def already_running(root: Path, args) -> int:
    """Another Rota Planner uses this data folder: bring it forward rather than
    start a second one (two would overwrite each other's changes)."""
    note: dict = {}
    for _ in range(25):  # the other one may be starting: give it a few seconds to say where it is
        if wake(root):
            print("Rota Planner is already open: its window was brought to the front.")
            return 0
        note = running_note(root) or {}
        if isinstance(note.get("port"), int):
            break
        time.sleep(0.2)
    port = note.get("port")
    if isinstance(port, int):
        url = f"http://127.0.0.1:{port}/?t={session_token(root)}"
        print(f"Rota Planner is already running: http://127.0.0.1:{port}/")
        if not args.no_browser:
            open_in_browser(root, url)
    else:
        print("Rota Planner is already open with this data. Use that one, or close it first.")
    return 0


def main(argv=None) -> int:
    updates.FILES_UMASK = os.umask(0o077)  # every file made by the program is private to this user
    args = parse_args(sys.argv[1:] if argv is None else argv)
    demo = args.demo or args.reset_demo
    root = args.data or PROJECT_ROOT / ("data-demo" if demo else "data")

    if args.reset_demo and args.data is not None:
        # Only ever the demonstration data: never a folder named with --data.
        print("--reset-demo rebuilds the demonstration data only; it cannot be used with --data.")
        return 2
    private_folder(root)
    instance = Instance(root)
    if not instance.acquire():
        if args.reset_demo:
            print("Rota Planner is open with this data: close it first, then start again.")
            return 1
        return already_running(root, args)
    if args.reset_demo:
        instance.release()
        shutil.rmtree(root)
        private_folder(root)
        if not instance.acquire():
            print("Rota Planner was started again meanwhile: close it first.")
            return 1
    closers: list = []  # the window's way to close itself, once it is open

    def stop_on_request(_signal, _frame) -> None:
        """The system asks the program to end (log out, kill): end cleanly. The window
        closes itself on its next tick (an exception inside Tk would be swallowed)."""
        if closers:
            closers[-1]()
        else:
            raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, stop_on_request)
    except (ValueError, OSError):
        pass  # not the main thread (tests), or not offered here
    try:
        result = run(root, demo, args, instance, closers)
    except KeyboardInterrupt:
        return 0
    finally:
        instance.release()
    if result == RESTART:
        return start_again(restart_command(args, root, demo))
    if result == NO_WINDOW:
        # Started from an icon, with no terminal to fall back on: open one (the lock is free now).
        return 0 if open_terminal() else NO_WINDOW
    return result


def run(root: Path, demo: bool, args, instance: Instance, closers: list) -> int:
    mark_running(root)
    pdf.sweep_stale()
    store = Store(root)
    if demo and store.is_empty():
        print("Creating demonstration data…")
        build_demo(store)

    token = session_token(root)
    forget_old_access(root)
    app = App(store)
    app.sync.start_background()
    app.updates.start_background()
    service = Service(app, token, args.port, renew_token=lambda: new_session_token(root))
    if args.after_update:
        # The port was free for a moment: if another program took it, it may have received
        # the open page's token. Starting on any other port makes a new token (Service._bind).
        service.port = args.port
    service.listeners.append(lambda state: instance.serving(service.port if state == "running" else None))
    try:
        return serve(app, service, root, demo, args, instance, closers)
    finally:
        args.port = service.port or args.port  # where a restart finds the open page again
        # Leaving: what changed is sent and the pen given back, without waiting long for a server.
        farewell = threading.Thread(target=app.sync.goodbye, name="sync-goodbye", daemon=True)
        farewell.start()
        farewell.join(6)


def serve(app: App, service: Service, root: Path, demo: bool, args, instance: Instance, closers: list) -> int:

    if args.window:
        reason = None
        try:
            from . import window
        except ImportError as error:
            reason = error
        else:
            try:
                window.run(service, demo=demo, instance=instance,
                           open_browser=lambda: open_in_browser(root, service.url()),
                           browser_at_start=not args.no_browser, closers=closers)
                return RESTART if app.updates.restart.is_set() else 0
            except window.CannotOpen as error:
                reason = error
        if not attended():
            # Never run unseen, with no way to stop it: a terminal window is opened instead
            # (for the usual data only; with --data or --demo there is nothing to fall back to).
            return NO_WINDOW if args.data is None and not demo else 1
        print(f"The window cannot open here ({reason}); Rota Planner runs in this terminal instead.")
    return run_in_terminal(service, root, demo, args)


def restart_command(args, root: Path, demo: bool) -> list:
    """How to start this same Rota Planner again: the same data (its full path: the
    start is made from the program's folder), the port in use, the window if it had
    one. Built from what was understood, never from the words typed: nothing is
    reset a second time, and no new tab is opened (the page already open comes back)."""
    command = [sys.executable, str(PROJECT_ROOT / "run.py"), "--port", str(args.port), "--no-browser", "--after-update"]
    if args.data is not None:
        command += ["--data", str(Path(root).resolve())]
    if demo:
        command.append("--demo")
    if args.window:
        command.append("--window")
    return command


def start_again(command: list) -> int:
    """After an update: the same program, started afresh with the new version."""
    if os.name == "nt":
        subprocess.Popen(command, cwd=str(PROJECT_ROOT))  # Windows cannot replace a running program
        return 0
    os.chdir(str(PROJECT_ROOT))
    os.execv(sys.executable, command)
    return 0  # never reached


def attended() -> bool:
    """Is someone reading this program's messages (a terminal)?"""
    return sys.stdout is not None and sys.stdout.isatty()


def open_terminal() -> bool:
    """The window cannot show: start Rota Planner in a terminal window instead."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(PROJECT_ROOT / "Rota Planner.command")])
            return True
        if os.name == "nt":
            console = Path(sys.executable).with_name("python.exe")
            subprocess.Popen([str(console if console.exists() else sys.executable), str(PROJECT_ROOT / "run.py")],
                             cwd=str(PROJECT_ROOT), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            return True
        script = str(PROJECT_ROOT / "rota-planner.sh")
        for command in (["x-terminal-emulator", "-e", script], ["gnome-terminal", "--", script],
                        ["konsole", "-e", script], ["xterm", "-e", script]):
            if shutil.which(command[0]):
                subprocess.Popen(command, cwd=str(PROJECT_ROOT))
                return True
    except OSError:
        pass
    return False


def run_in_terminal(service: Service, root: Path, demo: bool, args) -> int:
    def announce(state: str) -> None:
        if state != "running":
            return
        # 127.0.0.1, not "localhost": the name could lead to another program listening on IPv6.
        print(f"Rota Planner is running{' (demo data)' if demo else ''}.")
        print(f"Open: {service.url()}")
        print("Press Ctrl+C to stop.", flush=True)

    service.listeners.append(announce)
    if not service.start():
        print(f"Rota Planner could not start: {service.problem}")
        return 1
    if not args.no_browser:
        open_in_browser(root, service.url())
    try:
        while not service.app.updates.restart.is_set():
            time.sleep(0.5)
        time.sleep(1.0)  # the answer "installed" reaches the page first
        service.close()
        print("A new version was installed: Rota Planner starts again.", flush=True)
        return RESTART
    except KeyboardInterrupt:
        service.close()
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
