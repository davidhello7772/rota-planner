"""Rota Planner's own window: is it running, where to open it, Start and Stop.

Opened by the launchers (``run.py --window``) instead of a terminal. It uses
Tk, which comes with Python on Mac and Windows (on Linux: the package
``python3-tk``). Closing the window stops Rota Planner.

Everything shown is read from the program (``Service``,
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
from typing import Any, Callable, Dict, List, Optional

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError:  # Linux without the python3-tk package
    tk = None  # type: ignore[assignment]

from . import desktop, paint
from .instance import Instance
from .service import FAILED, RUNNING, STARTING, STOPPED, STOPPING, Service
from .sync import summary as sync_summary
from .updates import UpdateError

Json = Dict[str, Any]
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
UPDATE_FACTS_SECONDS = 300  # the version line is read again this often (and after a look or an installation)

# The window's class, which the taskbar and the dock use to find its launcher
# and icon. Tk keeps the first letter in capitals and lowers all the others
# ("rota-planner" gives "Rota-planner"); the Linux launcher made by
# install-rota-planner.sh says the same (StartupWMClass), and its file name,
# rota-planner.desktop, matches it too.
TK_CLASS = "rota-planner"


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


class Choice(tk.Canvas if tk else object):  # type: ignore[misc]
    """One of a few choices (a radio button), painted: a rounded row with a
    ring, a title and a line of explanation. Mouse and keyboard (Space, Enter,
    the arrows move to the other choice). ``kind="danger"``: red once chosen."""

    def __init__(self, parent, window: "Window", title: str, words: str, command: Callable[[], None],
                 selected: bool = False, kind: str = "", back: str = "surface") -> None:
        self.w, self.title, self.words, self.command = window, title, words, command
        self.selected, self.kind, self.back = selected, kind, back
        self.hover = self.focused = False
        p = window.painter
        super().__init__(parent, width=p.px(WIDTH - 2 * PAD - 2 * CARD_PAD), height=p.px(56), highlightthickness=0,
                         bd=0, bg=window.c[back], takefocus=1, cursor="hand2")
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False))
        self.bind("<ButtonRelease-1>", lambda _e: self._choose())
        self.bind("<FocusIn>", lambda _e: self._set(focused=True))
        self.bind("<FocusOut>", lambda _e: self._set(focused=False))
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, lambda _e: self._choose())
        self.bind("<Up>", lambda _e: self._move(-1))
        self.bind("<Down>", lambda _e: self._move(1))
        self.draw()

    def _set(self, **state) -> None:
        for name, value in state.items():
            setattr(self, name, value)
        self.draw()

    def _choose(self) -> None:
        if not self.selected:
            self.command()

    def _move(self, step: int) -> None:
        siblings = [child for child in self.master.winfo_children() if isinstance(child, Choice)]
        target = siblings[(siblings.index(self) + step) % len(siblings)]
        target.focus_set()
        target._choose()

    def set_selected(self, selected: bool) -> None:
        if selected != self.selected:
            self.selected = selected
            self.draw()

    def draw(self) -> None:
        p, c, f = self.w.painter, self.w.c, self.w.fonts
        width = int(self["width"])
        pad, ring, gap = p.px(12), p.px(18), p.px(10)
        left = pad + ring + gap
        self.delete("all")
        title = self.create_text(left, pad, text=self.title, anchor="nw", font=f["body_bold"], fill=c["text"],
                                 width=width - left - pad)
        top = self.bbox(title)
        words = self.create_text(left, top[3] + p.px(2), text=self.words, anchor="nw", font=f["small"],
                                 fill=c["text_2"], width=width - left - pad)
        height = self.bbox(words)[3] + pad
        self.configure(height=height)
        danger = self.kind == "danger"
        if self.selected:
            fill = c["bad_weak"] if danger else c["accent_weak"]
            line = c["bad"] if danger else c["accent"]
        else:
            fill = c["chip"] if self.hover else c["surface"]
            line = c["accent"] if self.focused else c["line_strong"]
        thick = max(2.0, p.scale * 2) if self.focused or self.selected else max(1.0, p.scale)
        self.create_image(0, 0, image=p.box(width, height, p.px(10), fill, c[self.back], line, thick),
                          anchor="nw", tags="back")
        self.tag_lower("back")
        mark = (c["bad"] if danger else c["accent"]) if self.selected else c["chip_strong"]
        ring_top = top[1] + (top[3] - top[1] - ring) // 2
        self.create_image(pad, ring_top, anchor="nw",
                          image=p.box(ring, ring, ring / 2, c["surface"], fill, mark, max(1.5, p.scale * 1.5)))
        if self.selected:
            dot = p.px(8)
            self.create_image(pad + (ring - dot) // 2, ring_top + (ring - dot) // 2, anchor="nw",
                              image=p.box(dot, dot, dot / 2, mark, c["surface"]))


# -- the window ---------------------------------------------------------------

UNINSTALLED = "uninstalled"  # what run() returns once Rota Planner was uninstalled from the window
TONE_MARK = {"ok": "✓", "warn": "!", "bad": "✕", "": "•"}

class Window:
    """Pages: "main" (the status), "update" (a new version), "uninstall" (the
    choices), "working" (uninstalling), "done" (uninstalled, or why not)."""

    def __init__(self, root: "tk.Tk", service: Service, demo: bool,
                 open_browser: Callable[[], None],
                 prepare_uninstall: Optional[Callable[[], None]] = None,
                 program: Optional[Path] = None) -> None:
        self.root, self.service, self.demo = root, service, demo
        self.open_browser = open_browser
        # Given only where uninstalling from here is possible (desktop.supported): removes the
        # "running" note once the program has stopped (the lock stays: no second start meanwhile).
        self.prepare_uninstall = prepare_uninstall
        self.program = program or Path(__file__).resolve().parent.parent
        self.page = "main"
        self.page_wanted = ""          # set from other threads, the page changes on the next tick
        self.updating = ""             # "checking" | "installing" (from the window)
        self.update_problem = ""
        self.delete_data = False
        self.typed = None              # the "Type DELETE" text (a StringVar, made with the page)
        self.looked = False            # the other computers looked at, for the uninstall page
        self.steps: List[List[str]] = []  # [words, "todo" | "doing" | "done"] while uninstalling
        self.outcome: Json = {}        # desktop.uninstall's answer
        self.uninstalled = False
        self.scale = self._scale()
        self.painter = Painter(root, self.scale)
        self.dark = system_dark()
        self.palettes = palettes()
        self.c = self.palettes["dark" if self.dark else "light"]
        self.fonts = self._fonts()
        self.facts = {"sync": None, "version": "", "dark": self.dark, "update": None}
        self.dirty = True
        self.facts_wanted = True
        self.wake_wanted = False
        self.close_wanted = False
        self.restart_seen = 0.0  # when the tick first saw that a new version was installed
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
        """Make every widget of the page shown (again, after the page or the
        system's light/dark changed)."""
        for child in self.root.winfo_children():
            child.destroy()
        self.widgets.clear()
        c, p = self.c, self.painter
        self.root.configure(bg=c["bg"])
        page = tk.Frame(self.root, bg=c["bg"], padx=p.px(PAD), pady=p.px(PAD))
        page.pack(fill="both", expand=True)
        {"main": self._build_main, "update": self._build_update, "uninstall": self._build_uninstall,
         "working": self._build_working, "done": self._build_done}[self.page](page)
        self.dirty = True
        self.refresh()

    def show(self, page: str) -> None:
        """Another page (from Tk's thread only)."""
        self.page = page
        self.build()
        self.root.update_idletasks()
        first = self.widgets.get("first")
        if first is not None:
            first.focus_set()

    def _header(self, page, title: str, subtitle: str = "") -> None:
        c, p, f = self.c, self.painter, self.fonts
        header = tk.Frame(page, bg=c["bg"])
        header.pack(fill="x", pady=(0, p.px(16)))
        logo = self.painter.image(("logo", p.px(44)), lambda: self._logo_rows(p.px(44)))
        tk.Label(header, image=logo, bg=c["bg"], bd=0).pack(side="left")
        words = tk.Frame(header, bg=c["bg"])
        words.pack(side="left", padx=(p.px(12), 0))
        tk.Label(words, text=title, font=f["title"], fg=c["text"], bg=c["bg"]).pack(anchor="w")
        self.widgets["subtitle"] = tk.Label(words, text=subtitle, font=f["small"], fg=c["text_2"], bg=c["bg"])
        self.widgets["subtitle"].pack(anchor="w")

    def _card(self, page, title: str = "") -> "tk.Frame":
        """A card below the last one; returns the frame to fill."""
        c, p, f = self.c, self.painter, self.fonts
        card = Card(page, self)
        card.pack(fill="x", pady=(0, p.px(12)))
        if title:
            tk.Label(card.inside, text=title, font=f["body_bold"], fg=c["text"], bg=c["surface"],
                     anchor="w").pack(fill="x", pady=(0, p.px(8)))
        return card.inside

    def _text(self, parent, text: str, font: str = "small", colour: str = "text_2", back: str = "surface",
              pady=0) -> "tk.Label":
        c, p = self.c, self.painter
        label = tk.Label(parent, text=text, font=self.fonts[font], fg=c[colour], bg=c[back], justify="left",
                         anchor="w", wraplength=p.px(WIDTH - 2 * PAD - 2 * CARD_PAD))
        label.pack(fill="x", pady=pady)
        return label

    def _lines(self, parent, lines: List[tuple], back: str = "surface") -> None:
        """Lines with a mark in front (✓ ! ✕ ·, coloured by their tone)."""
        c, p, f = self.c, self.painter, self.fonts
        width = p.px(WIDTH - 2 * PAD - 2 * CARD_PAD)
        for index, (tone, words) in enumerate(lines):
            row = tk.Frame(parent, bg=c[back])
            row.pack(fill="x", pady=(p.px(6) if index else 0, 0))
            colour = {"ok": c["ok"], "warn": c["warn"], "bad": c["bad"]}.get(tone, c["text_3"])
            tk.Label(row, text=TONE_MARK.get(tone, "•"), font=f["body_bold"], fg=colour, bg=c[back],
                     width=2, anchor="nw").pack(side="left", anchor="n")
            tk.Label(row, text=words, font=f["small"], fg=c["text"] if tone == "bad" else c["text_2"], bg=c[back],
                     justify="left", anchor="w", wraplength=width - p.px(24)).pack(side="left", fill="x", anchor="n")

    def _buttons(self, page) -> "tk.Frame":
        row = tk.Frame(page, bg=self.c["bg"])
        row.pack(fill="x", pady=(self.painter.px(4), 0))
        return row

    def _build_main(self, page) -> None:
        c, p, f = self.c, self.painter, self.fonts
        self._header(page, "Rota Planner")

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
        self.widgets["first"] = self.widgets["open"]

        # Details card: the other computers and the version (shown once known).
        details = Card(page, self)
        self.widgets["details"] = details
        self.rows = tk.Frame(details.inside, bg=c["surface"])
        self.rows.pack(fill="x")

        footer = tk.Frame(page, bg=c["bg"])
        footer.pack(fill="x", pady=(p.px(14), 0))
        self.widgets["footer"] = footer
        tk.Label(footer, text="Closing this window stops Rota Planner.", font=f["small"],
                 fg=c["text_3"], bg=c["bg"]).pack(side="left")
        if self.prepare_uninstall is not None:
            Button(footer, self, "Uninstall…", self.ask_uninstall, kind="quiet", back="bg").pack(side="right")

    # -- a new version ----------------------------------------------------------

    def _build_update(self, page) -> None:
        c, p, f = self.c, self.painter, self.fonts
        info = self.facts["update"] or {}
        self._header(page, "A new version", f"This computer has the version of {info.get('version') or '…'}.")
        inside = self._card(page, "What is new")
        width = p.px(WIDTH - 2 * PAD - 2 * CARD_PAD)
        for item in info.get("items") or []:
            row = tk.Frame(inside, bg=c["surface"])
            row.pack(fill="x", pady=(0, p.px(4)))
            tk.Label(row, text="•", font=f["body"], fg=c["text_3"], bg=c["surface"], width=2,
                     anchor="nw").pack(side="left", anchor="n")
            tk.Label(row, text=item, font=f["body"], fg=c["text"], bg=c["surface"], justify="left", anchor="w",
                     wraplength=width - p.px(24)).pack(side="left", fill="x", anchor="n")
        if info.get("more"):
            self._text(inside, f"… and {info['more']} more.", colour="text_3")
        inside = self._card(page)
        self._text(inside, "A copy of your data is saved first; only the program changes. "
                           "Rota Planner then starts again by itself.", font="body", colour="text")
        if (self.facts["sync"] or {}).get("configured"):
            self._text(inside, "With other computers: update them all the same day.", pady=(p.px(6), 0))
        self.widgets["update_state"] = self._text(inside, "", pady=(p.px(8), 0))
        buttons = self._buttons(page)
        self.widgets["update_go"] = Button(buttons, self, "Update now", self.install_update, kind="primary",
                                           back="bg", width=150)
        self.widgets["update_go"].pack(side="right")
        self.widgets["update_back"] = Button(buttons, self, "Not now", lambda: self.show("main"), back="bg")
        self.widgets["update_back"].pack(side="right", padx=(0, p.px(10)))
        self.widgets["first"] = self.widgets["update_go"]

    def show_update(self) -> None:
        self.update_problem = ""
        self.show("update")

    def check_update(self) -> None:
        if self.updating:
            return
        self.updating = "checking"

        def look() -> None:
            try:
                self.service.app.updates.check()
                self.facts["update"] = self.service.app.updates.info()
            finally:
                self.updating = ""
                self.dirty = True
        threading.Thread(target=look, name="window-update-check", daemon=True).start()
        self.dirty = True

    def install_update(self) -> None:
        """The same as the browser's "Update…": the version listed, a copy of the data
        first, then the program starts again (``tick`` sees ``updates.restart``)."""
        if self.updating:
            return
        target = str((self.facts["update"] or {}).get("target") or "")
        self.updating, self.update_problem = "installing", ""

        def install() -> None:
            app = self.service.app
            try:
                app.updates.install(expected=target, data_lock=app.lock)
            except UpdateError as error:
                self.update_problem = str(error)
                self.facts["update"] = app.updates.info()
            except Exception as error:  # noqa: BLE001 — said on the page, never a silent failure
                self.update_problem = f"The new version could not be installed: {error}"
            finally:
                self.updating = ""
                self.dirty = True
        threading.Thread(target=install, name="window-update", daemon=True).start()
        self.dirty = True

    def _refresh_update(self) -> None:
        w = self.widgets
        installing = self.updating == "installing"
        if self.service.app.updates.restart.is_set():
            words, colour = "Installed. Rota Planner starts again…", "ok"
        elif installing:
            words, colour = "Saving a copy of the data, then installing… (a minute or so)", "text_2"
        else:
            words, colour = self.update_problem, "bad"
        w["update_state"].configure(text=words, fg=self.c[colour])
        if not words:
            w["update_state"].pack_forget()
        elif not w["update_state"].winfo_manager():
            w["update_state"].pack(fill="x", pady=(self.painter.px(8), 0))
        available = bool((self.facts["update"] or {}).get("available"))
        w["update_go"].configure_button(text="Installing…" if installing else "Update now",
                                        enabled=available and not self.updating)
        w["update_back"].configure_button(enabled=not installing)

    # -- uninstalling -----------------------------------------------------------

    def ask_uninstall(self) -> None:
        """The uninstall page; first, a fresh look at the other computers."""
        if self.prepare_uninstall is None or self.page != "main":
            return
        self.delete_data, self.looked = False, False

        def look() -> None:
            try:
                self.facts["sync"] = self.service.app.sync.check_now()
            except Exception:  # noqa: BLE001 — then from what was last seen
                pass
            finally:
                self.looked = True
                self.dirty = True
        threading.Thread(target=look, name="window-look", daemon=True).start()
        self.show("uninstall")

    def _build_uninstall(self, page) -> None:
        c, p, f = self.c, self.painter, self.fonts
        self._header(page, "Uninstall Rota Planner", "From this computer only.")
        inside = self._card(page, "What is removed")
        removed = [("", "The program and its icon.")]
        if desktop.has_download_key():
            removed.append(("", "This computer's key for downloading the program."))
        self._lines(inside, removed)
        self._text(inside, "git, Python and Tk stay: other programs may use them.", colour="text_3",
                   pady=(p.px(8), 0))

        inside = self._card(page, "Your data")
        keep = Choice(inside, self, "Keep it", f"Moved to “{desktop.kept_folder_name()}” in your home folder. "
                                               "Install again to use it.", lambda: self.choose_delete(False),
                      selected=not self.delete_data)
        keep.pack(fill="x")
        delete = Choice(inside, self, "Delete it too", "People, rotas, settings, backups and saved documents. "
                                                       "This cannot be undone.", lambda: self.choose_delete(True),
                        selected=self.delete_data, kind="danger")
        delete.pack(fill="x", pady=(p.px(8), 0))
        self.widgets["keep"], self.widgets["delete"] = keep, delete
        confirm = tk.Frame(inside, bg=c["surface"])
        self.widgets["confirm"] = confirm
        tk.Label(confirm, text="To confirm, type DELETE:", font=f["small"], fg=c["text_2"],
                 bg=c["surface"]).pack(side="left")
        self.typed = tk.StringVar(master=self.root, value="")
        self.typed.trace_add("write", lambda *_a: self._changed_typing())
        entry = tk.Entry(confirm, textvariable=self.typed, font=f["body"], width=12, bg=c["surface"], fg=c["text"],
                         insertbackground=c["text"], relief="flat", bd=0, highlightthickness=max(1, p.px(1)),
                         highlightbackground=c["line_strong"], highlightcolor=c["accent"])
        entry.pack(side="left", padx=(p.px(8), 0), ipady=p.px(4), ipadx=p.px(4))
        entry.bind("<Return>", lambda _e: self.uninstall())
        self.widgets["typed"] = entry
        self.widgets["data_lines"] = tk.Frame(inside, bg=c["surface"])

        buttons = self._buttons(page)
        self.widgets["page"], self.widgets["buttons"] = page, buttons
        if (self.facts["sync"] or {}).get("configured"):
            self._others_card()
        self.widgets["go"] = Button(buttons, self, "Uninstall", self.uninstall, kind="danger", back="bg", width=130)
        self.widgets["go"].pack(side="right")
        self.widgets["cancel"] = Button(buttons, self, "Cancel", lambda: self.show("main"), back="bg")
        self.widgets["cancel"].pack(side="right", padx=(0, p.px(10)))
        self.widgets["first"] = keep if not self.delete_data else delete
        self.widgets["advice_shown"] = None

    def choose_delete(self, delete: bool) -> None:
        self.delete_data = delete
        if self.typed is not None:
            self.typed.set("")
        self.dirty = True
        self.refresh()
        if delete:
            self.widgets["typed"].focus_set()

    def _changed_typing(self) -> None:
        self.dirty = True

    def _advice(self) -> Json:
        if not self.looked:  # not one step before the other computers were looked at
            shared = bool((self.facts["sync"] or {}).get("configured"))
            return {"lines": [("", "Looking at the other computers…")] if shared else [], "allowed": False,
                    "shared": shared}
        return desktop.advice(self.facts["sync"] or {"configured": False}, self.delete_data)

    def _others_card(self) -> "tk.Frame":
        """The "Other computers" card, made when it turns out to be needed (the
        page may have been opened before this computer's set-up was known)."""
        if "others" not in self.widgets:
            others = self._card(self.widgets["page"], "Other computers")
            others.master.pack_configure(before=self.widgets["buttons"])
            self.widgets["others"] = tk.Frame(others, bg=self.c["surface"])
            self.widgets["others"].pack(fill="x")
        return self.widgets["others"]

    def _may_uninstall(self) -> bool:
        """Worked out now, never from what the button showed a moment ago."""
        typed = (self.typed.get().strip() if self.typed is not None else "") == "DELETE"
        return self._advice()["allowed"] and (typed or not self.delete_data)

    def _refresh_uninstall(self) -> None:
        w, p = self.widgets, self.painter
        w["keep"].set_selected(not self.delete_data)
        w["delete"].set_selected(self.delete_data)
        if self.delete_data and not w["confirm"].winfo_manager():
            w["confirm"].pack(fill="x", pady=(p.px(12), 0))
        elif not self.delete_data and w["confirm"].winfo_manager():
            w["confirm"].pack_forget()
        advice = self._advice()
        lines = advice["lines"]
        if lines != w["advice_shown"]:
            w["advice_shown"] = lines
            holder = self._others_card() if advice["shared"] else w["data_lines"]
            for frame in (w["data_lines"], w.get("others")):
                if frame is not None:
                    for child in frame.winfo_children():
                        child.destroy()
            w["data_lines"].pack_forget()
            if lines:
                self._lines(holder, lines)
                if holder is w["data_lines"]:
                    holder.pack(fill="x", pady=(p.px(12), 0))
        w["go"].configure_button(enabled=self._may_uninstall())

    def uninstall(self) -> None:
        """Stop, send the last changes and give the pen back, free the data folder,
        then the installer's --uninstall (``desktop.uninstall``). From a thread: the
        page shows each step."""
        if self.page != "uninstall" or self.prepare_uninstall is None or not self._may_uninstall():
            return
        delete = self.delete_data
        shared = bool((self.facts["sync"] or {}).get("configured"))
        self.steps = [["Stopping Rota Planner", "todo"]]
        if shared:
            self.steps.append(["Sending the last changes to the other computers", "todo"])
        self.steps.append(["Deleting the program and the data" if delete else "Putting the data aside, "
                           "removing the program", "todo"])
        prepare = self.prepare_uninstall

        def step(index: int, state: str) -> None:
            self.steps[index][1] = state
            self.dirty = True

        def work() -> None:
            app = self.service.app
            try:
                step(0, "doing")
                self.service.close()
                app.updates.stop()
                step(0, "done")
                if shared:
                    step(1, "doing")
                app.sync.goodbye()   # what changed is sent, the pen given back
                app.sync.detach()    # and nothing is ever sent again from here
                if shared:
                    step(1, "done")
                with app.lock:
                    prepare()        # the "running" note goes: the installer may act
                step(len(self.steps) - 1, "doing")
                self.outcome = desktop.uninstall(self.program, delete)
            except Exception as error:  # noqa: BLE001 — said on the page
                self.outcome = {"done": False, "kept": "", "sync": "", "message": f"Something went wrong: {error}"}
            self.uninstalled = bool(self.outcome.get("done"))
            if self.uninstalled:
                step(len(self.steps) - 1, "done")
            self.page_wanted = "done"
        self.show("working")
        threading.Thread(target=work, name="window-uninstall", daemon=True).start()

    def _build_working(self, page) -> None:
        c, p, f = self.c, self.painter, self.fonts
        self._header(page, "Uninstalling…", "Please wait: this takes a few moments.")
        inside = self._card(page)
        line = tk.Frame(inside, bg=c["surface"])
        line.pack(fill="x")
        self.light_size = p.px(40)
        self.widgets["light"] = tk.Label(line, bg=c["surface"], bd=0)
        self.widgets["light"].pack(side="left", anchor="n")
        self.widgets["steps"] = tk.Frame(line, bg=c["surface"])
        self.widgets["steps"].pack(side="left", fill="x", padx=(p.px(12), 0))
        self.widgets["steps_shown"] = None

    def _refresh_working(self) -> None:
        c, f, w = self.c, self.fonts, self.widgets
        shown = [tuple(s) for s in self.steps]
        if shown == w["steps_shown"]:
            return
        w["steps_shown"] = shown
        for child in w["steps"].winfo_children():
            child.destroy()
        for words, state in self.steps:
            mark, colour, font = {"done": ("✓", c["ok"], f["body"]), "doing": ("…", c["text"], f["body_bold"]),
                                  "todo": ("·", c["text_3"], f["body"])}[state]
            row = tk.Frame(w["steps"], bg=c["surface"])
            row.pack(fill="x", pady=(0, self.painter.px(6)))
            tk.Label(row, text=mark, font=f["body_bold"], fg=colour, bg=c["surface"], width=2,
                     anchor="w").pack(side="left")
            tk.Label(row, text=words, font=font, fg=c["text"] if state != "todo" else c["text_3"],
                     bg=c["surface"], anchor="w", justify="left",
                     wraplength=self.painter.px(WIDTH - 2 * PAD - 2 * CARD_PAD - 90)).pack(side="left")

    def _build_done(self, page) -> None:
        c, p, f = self.c, self.painter, self.fonts
        outcome = self.outcome
        done = bool(outcome.get("done"))
        self._header(page, "Uninstalled" if done else "Not uninstalled",
                     "Rota Planner is no longer on this computer." if done else
                     "Rota Planner is stopped: open it again from its icon.")
        inside = self._card(page)
        line = tk.Frame(inside, bg=c["surface"])
        line.pack(fill="x")
        self.light_size = p.px(40)
        self.widgets["light"] = tk.Label(line, bg=c["surface"], bd=0)
        self.widgets["light"].pack(side="left", anchor="n")
        texts = tk.Frame(line, bg=c["surface"])
        texts.pack(side="left", fill="x", padx=(p.px(12), 0))
        wrap = p.px(WIDTH - 2 * PAD - 2 * CARD_PAD - 60)
        if done:
            kept = outcome.get("kept") or ""
            title = "Your data is kept" if kept else "Its data is deleted" if self.delete_data else "Done"
            words = (f"In “{Path(kept).name}”, in your home folder. To use it again, install Rota Planner "
                     "and bring this folder when asked." if kept else
                     "Nothing of it is left on this computer." if self.delete_data else "")
        else:
            message = outcome.get("message") or ""
            title = "Nothing was removed" if "Nothing was removed" in message or not message else "Not finished"
            kept = outcome.get("kept") or ""
            words = f"Your data is in “{Path(kept).name}”, in your home folder." if kept else ""
        tk.Label(texts, text=title, font=f["state"], fg=c["text"], bg=c["surface"], anchor="w").pack(anchor="w")
        for text in (words, outcome.get("message") or ""):
            if text:
                tk.Label(texts, text=text, font=f["small"], fg=c["text_2"], bg=c["surface"], justify="left",
                         anchor="w", wraplength=wrap).pack(anchor="w", pady=(p.px(4), 0))
        buttons = self._buttons(page)
        self.widgets["close"] = Button(buttons, self, "Close", self.close, kind="primary", back="bg", width=110)
        self.widgets["close"].pack(side="right")
        self.widgets["first"] = self.widgets["close"]
        if outcome.get("kept"):
            Button(buttons, self, "Show the folder", lambda: desktop.show_folder(outcome["kept"]),
                   back="bg").pack(side="right", padx=(0, p.px(10)))

    def _logo_rows(self, size: int) -> paint.Rows:
        """The icon on the page's colour (Tk images here have no transparency)."""
        back = paint.rgb(self.c["bg"])
        return [[paint.mix(back, (r, g, b), a / 255) for (r, g, b, a) in row] for row in paint.icon_pixels(size)]

    def _detail_rows(self) -> List[tuple]:
        """(label, value, tone, button) for the details card."""
        rows = []
        sync = self.facts["sync"]
        if sync is not None:
            text, tone, explanation = sync_summary(sync)
            pen = (sync.get("pen") or {}).get("text") or ""
            words = text if not sync.get("configured") else f"{text}\n{pen or explanation}"
            rows.append(("Several computers", words, tone, None))
        info = self.facts["update"]
        if self.facts["version"] and info is not None:
            version = f"This computer has the version of {self.facts['version']}."
            if not info.get("supported"):
                rows.append(("Version", f"Version of {self.facts['version']}", "", None))
            elif info.get("available"):
                count = int(info["available"])
                rows.append(("Version", f"New version\n{count} change{'s' if count != 1 else ''}. "
                                        f"{version}", "ok", ("Update…", self.show_update)))
            else:
                checking = self.updating == "checking" or info.get("busy") == "checking"
                said = ("Looking for a new version…" if checking else info.get("problem") or
                        (f"Up to date (looked {info['checked_text']})." if info.get("checked_text") else ""))
                rows.append(("Version", f"Version of {self.facts['version']}" + (f"\n{said}" if said else ""), "",
                             ("…" if checking else "Check", self.check_update)))
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
        details = self.widgets["details"]
        if not rows:
            details.pack_forget()
            return
        if not details.winfo_manager():
            details.pack(fill="x", pady=(self.painter.px(14), 0), before=self.widgets["footer"])
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
        if self.page != "main":
            refresh = {"update": self._refresh_update, "uninstall": self._refresh_uninstall,
                       "working": self._refresh_working}.get(self.page)
            if refresh is not None:
                refresh()
            if "light" in self.widgets:
                self._draw_light()
            self.dirty = False
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
        self._fill_details()
        self._draw_light()
        self.dirty = False

    def tone(self) -> tuple:
        c, state = self.c, self.service.state
        if self.page == "working":
            return c["warn"], c["warn_weak"]
        if self.page == "done":
            return (c["ok"], c["ok_weak"]) if self.uninstalled else (c["bad"], c["bad_weak"])
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
        if self.breathing():
            # Breathing: the halo grows a little stronger and fainter.
            phase = (1 - math.cos(2 * math.pi * self.pulse / PULSE_FRAMES)) / 2
            halo = paint.hex_colour(paint.mix(paint.rgb(weak), paint.rgb(dot), 0.10 + 0.22 * phase))
        else:
            halo = weak
        image = self.painter.light(self.light_size, dot, halo, back)
        self.widgets["light"].configure(image=image)

    def breathing(self) -> bool:
        """The light breathes while Rota Planner runs, and while it is being uninstalled."""
        return self.page == "working" or (self.page == "main" and self.service.state == RUNNING)

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
        if self.page != "main":
            return
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
        if self.closing or self.page == "working":
            return  # while uninstalling, the window stays until it is done
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
        if self.service.app.updates.restart.is_set() and not self.restart_seen:
            self.restart_seen = time.time()
        if self.close_wanted or (self.restart_seen and time.time() - self.restart_seen > 1.0):
            # Asked to end; or a new version was installed and the page has had its answer:
            # the program starts again.
            self.close()
            return
        if self.wake_wanted:
            self.wake_wanted = False
            self.bring_forward()
        if self.page_wanted:
            page, self.page_wanted = self.page_wanted, ""
            self.show(page)
        if self.facts["dark"] != self.dark:
            self.dark = self.facts["dark"]
            self.c = self.palettes["dark" if self.dark else "light"]
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
        if "light" in self.widgets and self.breathing() and (looking or self.pulse):
            self.pulse = (self.pulse + 1) % PULSE_FRAMES
            self._draw_light()
        self.later("animate", 110 if looking else 600, self.animate)

    def _watch(self) -> None:
        """Facts that take a moment to find out, gathered outside the window's thread."""
        self.facts["version"] = self.service.app.updates.version()
        last_slow = last_theme = last_update = 0.0
        while not self.closing:
            if self.page in ("working", "done"):
                time.sleep(0.2)  # nothing to look at any more (the program may be gone)
                continue
            if time.time() - last_theme > 12:  # a change of theme is rare: no need to ask often
                last_theme = time.time()
                self.facts["dark"] = system_dark()
            if time.time() - last_slow > 15 or self.facts_wanted:
                last_slow, self.facts_wanted = time.time(), False
                try:
                    self.facts["sync"] = self.service.app.sync.status()
                except Exception:  # noqa: BLE001 — shown as "Needs attention" by the program next time
                    self.facts["sync"] = None
                if time.time() - last_update > UPDATE_FACTS_SECONDS or self.facts["update"] is None:
                    last_update = time.time()  # a few git commands: changes only after a look or an installation
                    try:
                        self.facts["update"] = self.service.app.updates.info()
                    except Exception:  # noqa: BLE001 — the version row waits
                        pass
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


def run(service: Service, demo: bool, instance: Instance,
        open_browser: Callable[[], None], browser_at_start: bool = True,
        closers: Optional[list] = None, prepare_uninstall: Optional[Callable[[], None]] = None):
    """Show the window, start Rota Planner, and wait until the window is closed.
    Returns ``UNINSTALLED`` when Rota Planner was uninstalled from it, else 0.
    ``prepare_uninstall``: given only where uninstalling from here is possible."""
    if tk is None:
        raise CannotOpen("Tk is not installed; on Linux: sudo apt install python3-tk")
    if tk.TkVersion < 8.6:
        raise CannotOpen(f"Tk {tk.TkVersion} is too old (8.6 is needed)")
    _dpi_aware()
    try:
        root = tk.Tk(className=TK_CLASS)
    except tk.TclError as error:
        raise CannotOpen(str(error).split("\n")[0]) from error
    try:
        window = Window(root, service, demo, open_browser, prepare_uninstall=prepare_uninstall)
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
    return UNINSTALLED if window.uninstalled else 0
