"""Rota Planner's own window: is it running, where to open it, Start and Stop.

Opened by the launchers (``run.py --window``) instead of a terminal. It uses
Tk, which comes with Python on Mac and Windows (on Linux: the package
``python3-tk``). Closing the window stops Rota Planner.

Everything shown is read from the program (``Service``, ``Access``,
``sync.summary``); nothing here decides anything. Shapes are painted by
``paint.py`` so the edges are smooth on every system. The colours are read
from the interface's own (``web/css/app.css``), light or dark like the system.
"""

from __future__ import annotations

import base64
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError:  # Linux without the python3-tk package
    tk = None  # type: ignore[assignment]

from . import paint
from .instance import Instance
from .service import FAILED, RUNNING, STARTING, STOPPED, STOPPING, Service
from .sync import summary as sync_summary

PROJECT_ROOT = Path(__file__).resolve().parent.parent

APP_CSS = Path(__file__).resolve().parent / "web" / "css" / "app.css"


def palettes(css: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """The interface's own colours, read from ``web/css/app.css`` (light, and
    the dark ones from its prefers-color-scheme block), so the window always
    matches the pages. ``--line-strong`` becomes ``line_strong``."""
    text = APP_CSS.read_text(encoding="utf-8") if css is None else css
    dark_at = text.find("prefers-color-scheme: dark")
    if dark_at < 0:
        raise ValueError("app.css has no dark colours")

    def colours(part: str) -> Dict[str, str]:
        found = re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", part)
        return {name.replace("-", "_"): value.lower() for name, value in found}

    light = colours(text[:dark_at])
    dark = dict(light, **colours(text[dark_at:]))
    for palette in (light, dark):
        # Two shades the pages do not need: a pressed button, and the light of a stopped program.
        palette["accent_press"] = paint.hex_colour(paint.mix(paint.rgb(palette["accent_hover"]), paint.rgb(palette["text"]), 0.25))
        palette["idle"] = palette["chip_strong"]
    return {"light": light, "dark": dark}


WIDTH = 440        # the window's width, in points
PAD = 22           # around the cards
CARD_PAD = 18      # inside a card
CARD_RADIUS = 14
BUTTON_H = 40
SMALL_H = 28
PULSE_FRAMES = 18


class CannotOpen(Exception):
    """The window cannot be shown here (no Tk, no screen): use the terminal."""


def quiet(command: List[str], timeout: float = 3) -> str:
    """Run a small command and return what it printed (never a console window on Windows)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              creationflags=flags).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def system_dark() -> bool:
    """Is the system in dark mode? (Light when it cannot be told.)
    ROTA_PLANNER_THEME=light or dark decides instead (for trying both)."""
    forced = os.environ.get("ROTA_PLANNER_THEME", "").lower()
    if forced in ("light", "dark"):
        return forced == "dark"
    try:
        if sys.platform == "darwin":
            return quiet(["defaults", "read", "-g", "AppleInterfaceStyle"]).lower() == "dark"
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        for key in ("color-scheme", "gtk-theme"):
            if "dark" in quiet(["gsettings", "get", "org.gnome.desktop.interface", key]).lower():
                return True
    except Exception:  # noqa: BLE001 — no way to tell: light
        pass
    return False


def program_version() -> str:
    """The date of the version in place, when the program was installed with git."""
    if not (PROJECT_ROOT / ".git").exists():
        return ""
    return quiet(["git", "-C", str(PROJECT_ROOT), "log", "-1", "--format=%cd", "--date=short"])


def show_folder(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def short_path(path: Path, longest: int = 30) -> str:
    """A folder as people say it: ~ for the home folder, and only its last
    parts when it is long (Show opens it)."""
    home = str(Path.home())
    text = str(path)
    if text.startswith(home + os.sep):
        text = "~" + text[len(home):]
    if len(text) > longest:
        parts = Path(text).parts
        text = os.path.join("…", *parts[-2:]) if len(parts) > 2 else text
    return text


# -- painted widgets ----------------------------------------------------------

class Painter:
    """Smooth shapes as Tk images, made once and kept (Tk forgets images
    nobody holds)."""

    def __init__(self, root: "tk.Tk", scale: float) -> None:
        self.root, self.scale = root, scale
        self._images: Dict[tuple, "tk.PhotoImage"] = {}

    def px(self, points: float) -> int:
        return int(round(points * self.scale))

    def image(self, key: tuple, make: Callable[[], paint.Rows]) -> "tk.PhotoImage":
        if key not in self._images:
            rows = make()
            image = tk.PhotoImage(master=self.root, width=len(rows[0]), height=len(rows))
            image.put(paint.tk_data(rows))
            self._images[key] = image
        return self._images[key]

    def box(self, width: int, height: int, radius: float, fill: str, back: str,
            line: Optional[str] = None, line_width: float = 1.0) -> "tk.PhotoImage":
        key = ("box", width, height, radius, fill, back, line, line_width)
        return self.image(key, lambda: paint.rounded_box(
            width, height, radius, paint.rgb(fill), paint.rgb(back),
            paint.rgb(line) if line else None, line_width))

    def light(self, size: int, dot: str, halo: str, back: str) -> "tk.PhotoImage":
        return self.image(("light", size, dot, halo, back),
                          lambda: paint.light(size, paint.rgb(dot), paint.rgb(halo), paint.rgb(back)))


class Button(tk.Canvas if tk else object):  # type: ignore[misc]
    """A rounded button: primary (filled), secondary (outlined), danger
    (outlined, red text) or quiet (small, grey). Mouse and keyboard."""

    def __init__(self, parent, window: "Window", text: str, command: Callable[[], None],
                 kind: str = "secondary", width: Optional[float] = None, back: str = "surface") -> None:
        self.w, self.kind, self.command, self.back = window, kind, command, back
        self.text = text
        self.enabled = True
        self.hover = self.pressed = self.focused = False
        height = SMALL_H if kind == "quiet" else BUTTON_H
        self.font = window.fonts["small_bold" if kind == "quiet" else "button"]
        self.width_points = width
        pw, ph = self._size(height)
        super().__init__(parent, width=pw, height=ph, highlightthickness=0, bd=0,
                         bg=window.c[back], takefocus=1, cursor="hand2")
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda _e: self._set(focused=True))
        self.bind("<FocusOut>", lambda _e: self._set(focused=False))
        self.bind("<Return>", lambda _e: self._fire())
        self.bind("<KP_Enter>", lambda _e: self._fire())
        self.bind("<space>", lambda _e: self._fire())
        self.draw()

    def _size(self, height: float):
        p = self.w.painter
        width = self.width_points
        if width is None:
            width = (self.font.measure(self.text) / p.scale) + (24 if self.kind == "quiet" else 36)
        return p.px(width), p.px(height)

    def configure_button(self, text: Optional[str] = None, kind: Optional[str] = None,
                         enabled: Optional[bool] = None) -> None:
        changed = False
        for name, value in (("text", text), ("kind", kind), ("enabled", enabled)):
            if value is not None and getattr(self, name) != value:
                setattr(self, name, value)
                changed = True
        if changed:
            if self.width_points is None:
                pw, ph = self._size(SMALL_H if self.kind == "quiet" else BUTTON_H)
                self.configure(width=pw, height=ph)
            self.configure(cursor="hand2" if self.enabled else "arrow", takefocus=1 if self.enabled else 0)
            self.draw()

    def _set(self, **state) -> None:
        for name, value in state.items():
            setattr(self, name, value)
        self.draw()

    def _release(self, event) -> None:
        inside = 0 <= event.x < int(self["width"]) and 0 <= event.y < int(self["height"])
        self._set(pressed=False)
        if inside:
            self._fire()

    def _fire(self) -> None:
        if self.enabled:
            self.command()

    def colours(self):
        c, kind = self.w.c, self.kind
        if not self.enabled:
            return c["chip"], None, c["text_3"]
        if kind == "primary":
            fill = c["accent_press"] if self.pressed else c["accent_hover"] if self.hover else c["accent"]
            return fill, None, c["on_accent"]
        if kind == "quiet":
            return (c["chip_hover"] if self.hover or self.pressed else c["chip"]), None, c["text_2"]
        fill = c["chip_hover"] if self.pressed else c["chip"] if self.hover else c["surface"]
        if kind == "danger" and (self.hover or self.pressed):
            fill = c["bad_weak"]
        return fill, c["line_strong"], (c["bad"] if kind == "danger" else c["text"])

    def draw(self) -> None:
        p, c = self.w.painter, self.w.c
        width, height = int(self["width"]), int(self["height"])
        fill, line, text = self.colours()
        radius = height / 2 if self.kind == "quiet" else p.px(10)
        if self.focused and self.enabled:
            line, line_width = c["accent"], max(2.0, p.scale * 2)
        else:
            line_width = max(1.0, p.scale)
        image = p.box(width, height, radius, fill, c[self.back], line, line_width)
        self.delete("all")
        self.create_image(0, 0, image=image, anchor="nw")
        self.create_text(width / 2, height / 2, text=self.text, fill=text, font=self.font)


class Card(tk.Canvas if tk else object):  # type: ignore[misc]
    """A rounded white (or dark) card; put widgets in ``.inside``."""

    def __init__(self, parent, window: "Window") -> None:
        self.w = window
        p = window.painter
        super().__init__(parent, highlightthickness=0, bd=0, bg=window.c["bg"],
                         width=p.px(WIDTH - 2 * PAD), height=p.px(80))
        self.inside = tk.Frame(self, bg=window.c["surface"], bd=0, highlightthickness=0)
        # A strut: the inside is always the card's full width, so rows reach its right edge.
        tk.Frame(self.inside, bg=window.c["surface"], width=p.px(WIDTH - 2 * PAD - 2 * CARD_PAD), height=0).pack()
        self._inside_id = self.create_window(p.px(CARD_PAD), p.px(CARD_PAD), window=self.inside, anchor="nw")
        self.inside.bind("<Configure>", lambda _e: self.after_idle(self.fit))

    def fit(self) -> None:
        p, c = self.w.painter, self.w.c
        width = p.px(WIDTH - 2 * PAD)
        height = self.inside.winfo_reqheight() + 2 * p.px(CARD_PAD)
        if int(self["height"]) != height or not self.find_withtag("back"):
            self.configure(height=height)
            self.delete("back")
            image = p.box(width, height, p.px(CARD_RADIUS), c["surface"], c["bg"], c["line"], max(1.0, p.scale))
            self.create_image(0, 0, image=image, anchor="nw", tags="back")
            self.tag_lower("back")


# -- the window ---------------------------------------------------------------

class Window:
    def __init__(self, root: "tk.Tk", service: Service, data_root: Path, demo: bool,
                 open_browser: Callable[[], None]) -> None:
        self.root, self.service, self.data_root, self.demo = root, service, data_root, demo
        self.open_browser = open_browser
        self.scale = self._scale()
        self.painter = Painter(root, self.scale)
        self.dark = system_dark()
        self.palettes = palettes()
        self.c = self.palettes["dark" if self.dark else "light"]
        self.fonts = self._fonts()
        self.facts = {"sync": None, "others": [], "version": "", "dark": self.dark}
        self.dirty = True
        self.facts_wanted = True
        self.wake_wanted = False
        self.close_wanted = False
        self.closing = False
        self.pulse = 0
        self.widgets: Dict[str, object] = {}
        self.timers: Dict[str, str] = {}
        self.busy = False
        self.copied_until = 0.0
        self._icons()
        root.title("Rota Planner (demo data)" if demo else "Rota Planner")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        modifier = "Command" if sys.platform == "darwin" else "Control"
        root.bind_all(f"<{modifier}-o>", lambda _e: self.open())
        root.bind_all(f"<{modifier}-q>", lambda _e: self.close())
        if sys.platform == "darwin":
            root.createcommand("tk::mac::Quit", self.close)
            root.createcommand("tk::mac::ReopenApplication", self.bring_forward)
        service.listeners.append(self._changed)
        self.build()
        self._centre()
        threading.Thread(target=self._watch, name="window-facts", daemon=True).start()
        self.later("tick", 200, self.tick)
        self.later("animate", 80, self.animate)

    # -- set-up -------------------------------------------------------------

    def _scale(self) -> float:
        if sys.platform == "darwin":
            return 1.0  # Tk works in points there, and Retina screens are handled by the system
        return max(1.0, min(3.0, self.root.winfo_fpixels("1i") / 96))

    def _fonts(self) -> Dict[str, "tkfont.Font"]:
        default = tkfont.nametofont("TkDefaultFont").actual("family")
        if sys.platform == "darwin":
            family, base = default, 13
        elif os.name == "nt":
            family, base = "Segoe UI", 10
        else:
            available = set(tkfont.families(self.root))
            family = next((f for f in ("Inter", "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans")
                           if f in available), default)
            base = 11

        def font(size: int, weight: str = "normal") -> "tkfont.Font":
            return tkfont.Font(root=self.root, family=family, size=size, weight=weight)

        return {
            "title": font(base + 5, "bold"), "state": font(base + 4, "bold"), "body": font(base),
            "body_bold": font(base, "bold"), "small": font(base - 1), "small_bold": font(base - 1, "bold"),
            "button": font(base, "bold"), "label": font(base - 1),
        }

    def _icons(self) -> None:
        try:
            self.icon_big = tk.PhotoImage(master=self.root, data=base64.b64encode(paint.icon_png(128)))
            self.icon_small = tk.PhotoImage(master=self.root, data=base64.b64encode(paint.icon_png(64)))
            self.root.iconphoto(True, self.icon_big, self.icon_small)
        except tk.TclError:
            pass

    def _centre(self) -> None:
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 3)
        self.root.geometry(f"+{x}+{y}")

    # -- building -----------------------------------------------------------

    def build(self) -> None:
        """Make every widget (again, after the system changed light/dark)."""
        for child in self.root.winfo_children():
            child.destroy()
        c, p, f = self.c, self.painter, self.fonts
        self.root.configure(bg=c["bg"])
        page = tk.Frame(self.root, bg=c["bg"], padx=p.px(PAD), pady=p.px(PAD))
        page.pack(fill="both", expand=True)

        header = tk.Frame(page, bg=c["bg"])
        header.pack(fill="x", pady=(0, p.px(16)))
        logo = self.painter.image(("logo", p.px(44)), lambda: self._logo_rows(p.px(44)))
        tk.Label(header, image=logo, bg=c["bg"], bd=0).pack(side="left")
        words = tk.Frame(header, bg=c["bg"])
        words.pack(side="left", padx=(p.px(12), 0))
        tk.Label(words, text="Rota Planner", font=f["title"], fg=c["text"], bg=c["bg"]).pack(anchor="w")
        self.widgets["subtitle"] = tk.Label(words, text="", font=f["small"], fg=c["text_2"], bg=c["bg"])
        self.widgets["subtitle"].pack(anchor="w")

        # Status card: the light, what it is doing, the address, Open and Start/Stop.
        status = Card(page, self)
        status.pack(fill="x")
        inside = status.inside
        line = tk.Frame(inside, bg=c["surface"])
        line.pack(fill="x")
        size = p.px(40)
        self.widgets["light"] = tk.Label(line, bg=c["surface"], bd=0)
        self.widgets["light"].pack(side="left")
        texts = tk.Frame(line, bg=c["surface"])
        texts.pack(side="left", padx=(p.px(12), 0))
        self.widgets["state"] = tk.Label(texts, text="", font=f["state"], fg=c["text"], bg=c["surface"])
        self.widgets["state"].pack(anchor="w")
        self.widgets["detail"] = tk.Label(texts, text="", font=f["small"], fg=c["text_2"], bg=c["surface"],
                                          justify="left", wraplength=p.px(WIDTH - 2 * PAD - 2 * CARD_PAD - 60))
        self.widgets["detail"].pack(anchor="w")
        self.light_size = size

        address = tk.Frame(inside, bg=c["surface"])
        address.pack(fill="x", pady=(p.px(14), 0))
        self.widgets["address_row"] = address
        self.widgets["address"] = tk.Label(address, text="", font=f["body"], fg=c["text"], bg=c["surface"])
        self.widgets["address"].pack(side="left")
        self.widgets["copy"] = Button(address, self, "Copy", self.copy, kind="quiet")
        self.widgets["copy"].pack(side="right")

        buttons = tk.Frame(inside, bg=c["surface"])
        buttons.pack(fill="x", pady=(p.px(16), 0))
        inner = WIDTH - 2 * PAD - 2 * CARD_PAD
        self.widgets["toggle"] = Button(buttons, self, "Stop", self.toggle, kind="danger", width=104)
        self.widgets["toggle"].pack(side="right")
        self.widgets["open"] = Button(buttons, self, "Open Rota Planner", self.open, kind="primary",
                                      width=inner - 104 - 10)
        self.widgets["open"].pack(side="left")

        # Details card: who can use it, the address for other browsers, the other computers, the data.
        details = Card(page, self)
        details.pack(fill="x", pady=(p.px(14), 0))
        self.rows = tk.Frame(details.inside, bg=c["surface"])
        self.rows.pack(fill="x")

        footer = tk.Frame(page, bg=c["bg"])
        footer.pack(fill="x", pady=(p.px(14), 0))
        tk.Label(footer, text="Closing this window stops Rota Planner.", font=f["small"],
                 fg=c["text_3"], bg=c["bg"]).pack(side="left")
        self.widgets["version"] = tk.Label(footer, text="", font=f["small"], fg=c["text_3"], bg=c["bg"])
        self.widgets["version"].pack(side="right")
        self.dirty = True
        self.refresh()

    def _logo_rows(self, size: int) -> paint.Rows:
        """The icon on the page's colour (Tk images here have no transparency)."""
        back = paint.rgb(self.c["bg"])
        return [[paint.mix(back, (r, g, b), a / 255) for (r, g, b, a) in row] for row in paint.icon_pixels(size)]

    def _detail_rows(self) -> List[tuple]:
        """(label, value, tone, button) for the details card."""
        access, rows = self.service.access, []
        if access.network_open:
            count = len(access.allowed)
            who = f"This computer and {count} other address{'es' if count != 1 else ''} of the network"
        else:
            who = "Only this computer"
        rows.append(("Who can use it", who + (" · password asked" if access.has_password else ""), "", None))
        others = self.facts["others"]
        if self.service.running and others:
            rows.append(("Other computers open", "\n".join(others), "", None))
        sync = self.facts["sync"]
        if sync is not None:
            text, tone, explanation = sync_summary(sync)
            pen = (sync.get("pen") or {}).get("text") or ""
            words = text if not sync.get("configured") else f"{text}\n{pen or explanation}"
            rows.append(("Other computers", words, tone, None))
        rows.append(("Data", short_path(self.data_root), "",
                     ("Show", lambda: show_folder(self.data_root))))
        return rows

    def _fill_details(self) -> None:
        c, p, f = self.c, self.painter, self.fonts
        rows = self._detail_rows()
        shown = [(label, value, tone, button[0] if button else None) for label, value, tone, button in rows]
        if shown == self.widgets.get("detail_rows_shown"):
            return
        self.widgets["detail_rows_shown"] = shown
        for child in self.rows.winfo_children():
            child.destroy()
        label_width = p.px(128)
        value_width = p.px(WIDTH - 2 * PAD - 2 * CARD_PAD) - label_width
        for index, (label, value, tone, button) in enumerate(rows):
            if index:
                tk.Frame(self.rows, bg=c["line"], height=max(1, p.px(1))).pack(fill="x", pady=p.px(10))
            row = tk.Frame(self.rows, bg=c["surface"])
            row.pack(fill="x")
            row.grid_columnconfigure(0, minsize=label_width)
            row.grid_columnconfigure(1, weight=1)
            tk.Label(row, text=label, font=f["label"], fg=c["text_3"], bg=c["surface"], anchor="nw",
                     justify="left", wraplength=label_width - p.px(8)).grid(row=0, column=0, sticky="nw", pady=(p.px(1), 0))
            holder = tk.Frame(row, bg=c["surface"])
            holder.grid(row=0, column=1, sticky="new")
            first, _, rest = value.partition("\n")
            colour = {"ok": c["ok"], "warn": c["warn"], "bad": c["bad"]}.get(tone, c["text"])
            top = tk.Frame(holder, bg=c["surface"])
            top.pack(fill="x")
            wrap = value_width - (p.px(70) if button else 0)
            tk.Label(top, text=first, font=f["body_bold"] if tone else f["body"], fg=colour, bg=c["surface"],
                     justify="left", anchor="w", wraplength=wrap).pack(side="left", anchor="w")
            if button:
                Button(top, self, button[0], button[1], kind="quiet").pack(side="right", padx=(p.px(8), 0))
            if rest:
                tk.Label(holder, text=rest, font=f["small"], fg=c["text_2"], bg=c["surface"],
                         justify="left", anchor="w", wraplength=value_width).pack(anchor="w")

    # -- showing the state ----------------------------------------------------

    def refresh(self) -> None:
        if self.closing:
            return
        c, s, w = self.c, self.service, self.widgets
        state = s.state
        subtitle = "Demonstration data, invented names" if self.demo else "The monthly rota, on this computer"
        w["subtitle"].configure(text=subtitle)
        if state == RUNNING:
            since = time.strftime("%H:%M", time.localtime(s.since)) if s.since else ""
            title, detail = "Running", f"Since {since}. Open it in your browser." if since else ""
        elif state == STARTING:
            title, detail = "Starting…", "Getting ready."
        elif state == STOPPING:
            title, detail = "Stopping…", ""
        elif state == FAILED:
            title, detail = "Could not start", s.problem
        else:
            title, detail = "Stopped", "Nobody can open Rota Planner until you start it."
        w["state"].configure(text=title)
        w["detail"].configure(text=detail)
        running = state == RUNNING
        if running:
            w["address"].configure(text=s.plain_url(), fg=c["text"])
            w["copy"].configure_button(text="Copied" if time.time() < self.copied_until else "Copy", enabled=True)
        else:
            w["address"].configure(text="No address while stopped" if state in (STOPPED, FAILED) else "…",
                                   fg=c["text_3"])
            w["copy"].configure_button(enabled=False)
        moving = state in (STARTING, STOPPING) or self.busy
        w["open"].configure_button(enabled=not moving and state != STOPPING)
        w["toggle"].configure_button(text="Stop" if running else ("Try again" if state == FAILED else "Start"),
                                     kind="danger" if running else "secondary", enabled=not moving)
        w["version"].configure(text=f"Version of {self.facts['version']}" if self.facts["version"] else "")
        self._fill_details()
        self._draw_light()
        self.dirty = False

    def tone(self) -> tuple:
        c, state = self.c, self.service.state
        if state == RUNNING:
            return c["ok"], c["ok_weak"]
        if state in (STARTING, STOPPING):
            return c["warn"], c["warn_weak"]
        if state == FAILED:
            return c["bad"], c["bad_weak"]
        return c["idle"], c["chip"]

    def _draw_light(self) -> None:
        dot, weak = self.tone()
        back = self.c["surface"]
        if self.service.state == RUNNING:
            # Breathing: the halo grows a little stronger and fainter.
            phase = (1 - math.cos(2 * math.pi * self.pulse / PULSE_FRAMES)) / 2
            halo = paint.hex_colour(paint.mix(paint.rgb(weak), paint.rgb(dot), 0.10 + 0.22 * phase))
        else:
            halo = weak
        image = self.painter.light(self.light_size, dot, halo, back)
        self.widgets["light"].configure(image=image)

    # -- actions --------------------------------------------------------------

    def _in_background(self, work: Callable[[], None]) -> None:
        def run() -> None:
            try:
                work()
            finally:
                self.busy = False
                self.dirty = True
        self.busy = True
        self.dirty = True
        threading.Thread(target=run, daemon=True).start()

    def open(self) -> None:
        if self.service.running:
            self.open_browser()
            return

        def start_then_open() -> None:
            if self.service.start() and not self.closing:
                self.open_browser()
        self._in_background(start_then_open)

    def toggle(self) -> None:
        if self.service.running:
            self._in_background(self.service.stop)
        else:
            self._in_background(self.service.start)

    def copy(self) -> None:
        if not self.service.running:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.service.plain_url())
        self.copied_until = time.time() + 1.6
        self.dirty = True

    def later(self, name: str, ms: int, work: Callable[[], None]) -> None:
        """Run ``work`` in ``ms`` milliseconds (one timer of each name)."""
        self.timers[name] = self.root.after(ms, work)

    def dispose(self) -> None:
        """Stop the timers (before the window goes)."""
        self.closing = True
        for timer in self.timers.values():
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        self.timers.clear()

    def close(self) -> None:
        if self.closing:
            return
        self.dispose()
        self.root.withdraw()
        try:
            self.service.close()
        finally:
            self.root.destroy()

    def bring_forward(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    # -- timers (Tk is only ever touched from its own thread) -------------------

    def _changed(self, _state: str) -> None:
        self.dirty = True  # from the server's threads: picked up by tick()
        self.facts_wanted = True

    def wake(self) -> None:
        self.wake_wanted = True  # from the wake-up door's thread

    def request_close(self) -> None:
        self.close_wanted = True  # from a signal handler (log out, kill)

    def tick(self) -> None:
        if self.closing:
            return
        if self.close_wanted:
            self.close()
            return
        if self.wake_wanted:
            self.wake_wanted = False
            self.bring_forward()
        if self.facts["dark"] != self.dark:
            self.dark = self.facts["dark"]
            self.c = self.palettes["dark" if self.dark else "light"]
            self.widgets.clear()
            self.build()
        if self.dirty or (self.copied_until and time.time() > self.copied_until):
            if self.copied_until and time.time() > self.copied_until:
                self.copied_until = 0.0
            self.refresh()
        self.later("tick", 200, self.tick)

    def animate(self) -> None:
        if self.closing:
            return
        # Breathe only while someone is looking (the window has the focus); otherwise rest.
        looking = self.root.state() == "normal" and self.root.focus_displayof() is not None
        if self.service.state == RUNNING and (looking or self.pulse):
            self.pulse = (self.pulse + 1) % PULSE_FRAMES
            self._draw_light()
        self.later("animate", 110 if looking else 600, self.animate)

    def _watch(self) -> None:
        """Facts that take a moment to find out, gathered outside the window's thread."""
        self.facts["version"] = program_version()
        last_slow = last_theme = 0.0
        while not self.closing:
            if time.time() - last_theme > 12:  # a change of theme is rare: no need to ask often
                last_theme = time.time()
                self.facts["dark"] = system_dark()
            if time.time() - last_slow > 15 or self.facts_wanted:
                last_slow, self.facts_wanted = time.time(), False
                try:
                    self.facts["sync"] = self.service.app.sync.status()
                except Exception:  # noqa: BLE001 — shown as "Sync: look" by the program next time
                    self.facts["sync"] = None
                self.facts["others"] = self.service.other_addresses()
                self.dirty = True
            for _ in range(15):  # about 3 s, but quick to notice a start or stop
                if self.closing or self.facts_wanted:
                    break
                time.sleep(0.2)


def _dpi_aware() -> None:
    """Windows: sharp text on high-resolution screens."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def run(service: Service, data_root: Path, demo: bool, instance: Instance,
        open_browser: Callable[[], None], browser_at_start: bool = True,
        closers: Optional[list] = None) -> int:
    """Show the window, start Rota Planner, and wait until the window is closed."""
    if tk is None:
        raise CannotOpen("Tk is not installed; on Linux: sudo apt install python3-tk")
    if tk.TkVersion < 8.6:
        raise CannotOpen(f"Tk {tk.TkVersion} is too old (8.6 is needed)")
    _dpi_aware()
    try:
        root = tk.Tk(className="RotaPlanner")
    except tk.TclError as error:
        raise CannotOpen(str(error).split("\n")[0]) from error
    try:
        window = Window(root, service, data_root, demo, open_browser)
    except (OSError, ValueError, KeyError, tk.TclError) as error:  # e.g. app.css unreadable
        root.destroy()
        raise CannotOpen(f"the window could not be drawn: {error}") from error
    instance.on_wake = window.wake
    instance.open_door()
    if closers is not None:
        closers.append(window.request_close)

    def first_start() -> None:
        if service.start() and browser_at_start:
            open_browser()
    threading.Thread(target=first_start, name="first-start", daemon=True).start()
    try:
        root.mainloop()
    finally:
        window.closing = True
        service.close()
    return 0
