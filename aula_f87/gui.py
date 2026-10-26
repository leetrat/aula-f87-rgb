"""GTK4/libadwaita frontend for the AULA F87 Pro.

The window is deliberately a thin layer over :mod:`aula_f87.device`: every
write opens the hidraw control interface, sends the exact wireless packets,
saves them, and reads the profile back.  HID operations run on worker threads
so the animated keyboard preview stays responsive while a write is in flight.
"""

from __future__ import annotations

import colorsys
import json
import math
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango, PangoCairo
    import cairo
except (ImportError, ValueError) as exc:  # pragma: no cover - environment check
    raise SystemExit(
        "The frontend needs GTK4, libadwaita, PyGObject, cairo, and Pango. "
        f"Could not load them: {exc}"
    ) from exc

from . import protocol
from .device import (
    AulaError,
    UnsupportedSetting,
    WirelessKeyboard,
    open_keyboard,
)
from .keys import LAYOUT_KEYS, LAYOUT_ROWS, KeySpec


APP_ID = "io.github.aula_f87.AulaF87"
BACKUP_DIR = Path(
    os.environ.get("AULA_F87_BACKUP_DIR", str(Path.home() / "aula-f87" / "backups"))
)

EFFECT_CHOICES = tuple(
    effect for effect in protocol.EFFECTS if effect.id in protocol.WIRELESS_EFFECT_IDS
)
EFFECT_CHOICES_BY_ID = {effect.id: effect for effect in EFFECT_CHOICES}

PALETTE: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("Violet", (138, 92, 246)),
    ("Iris", (196, 167, 255)),
    ("Rose", (255, 107, 154)),
    ("Red", (255, 92, 92)),
    ("Orange", (255, 169, 77)),
    ("Amber", (255, 212, 59)),
    ("Lime", (105, 219, 124)),
    ("Cyan", (59, 201, 219)),
    ("Blue", (77, 171, 247)),
    ("White", (248, 249, 250)),
)

APP_CSS = b"""
window {
    background-color: #120f18;
}
headerbar {
    background-color: #181321;
    border-bottom: 1px solid #2a2338;
    box-shadow: none;
}
.panel {
    background-color: #1d1728;
    border: 1px solid #2e2640;
    border-radius: 16px;
    padding: 18px;
}
.page-title {
    font-size: 21px;
    font-weight: 700;
}
.panel-title {
    font-size: 16px;
    font-weight: 700;
}
.section-label {
    font-size: 11px;
    font-weight: 700;
    color: #a99dbd;
}
.dim {
    color: #b3a8c8;
}
.small {
    font-size: 11px;
}
.pill {
    padding: 4px 10px;
    border-radius: 999px;
    background-color: #2a2338;
    color: #bdb2d1;
    font-size: 11px;
    font-weight: 700;
}
.pill-ok {
    background-color: #173a30;
    color: #7ef0c0;
}
.pill-error {
    background-color: #3a2028;
    color: #ff9ab0;
}
.apply-button {
    padding: 9px 16px;
    font-weight: 700;
}
scale trough highlight {
    background-color: #8a5cf6;
}
scale trough {
    background-color: #332b44;
}
spinbutton {
    min-width: 58px;
}
"""


# ---------------------------------------------------------------------------
# Backend helpers (all called from worker threads)


def read_status() -> dict[str, object]:
    keyboard = open_keyboard()
    try:
        return keyboard.status()
    finally:
        keyboard.close()


def apply_effect(
    effect_id: int,
    brightness: int,
    speed: int,
    colorful: bool,
    color: Optional[tuple[int, int, int]],
) -> dict[str, object]:
    keyboard = open_keyboard()
    try:
        return keyboard.apply_effect(
            effect_id=effect_id,
            brightness=brightness,
            speed=speed,
            colorful=colorful,
            color=color,
        )
    finally:
        keyboard.close()


def apply_per_key(colors: dict[int, tuple[int, int, int]]) -> dict[str, object]:
    keyboard = open_keyboard()
    try:
        return keyboard.apply_per_key(colors)
    finally:
        keyboard.close()


def apply_sleep(minutes: int) -> dict[str, object]:
    keyboard = open_keyboard()
    try:
        return keyboard.apply_sleep(minutes)
    finally:
        keyboard.close()


def apply_debounce(milliseconds: int) -> dict[str, object]:
    keyboard = open_keyboard()
    try:
        return keyboard.apply_debounce(milliseconds)
    finally:
        keyboard.close()


def backup_now() -> Path:
    keyboard = open_keyboard()
    try:
        if not isinstance(keyboard, WirelessKeyboard):
            raise UnsupportedSetting(
                "configuration backups are available only on the 2.4 GHz receiver"
            )
        config = keyboard.read_config()
        payload: dict[str, object] = {
            "format": "aula-f87-config-v1",
            "protocol": "wireless",
            "device": {
                "path": keyboard.info.path,
                "vid": f"{keyboard.info.vid:04X}",
                "pid": f"{keyboard.info.pid:04X}",
                "name": keyboard.info.name,
            },
            "frames": [item.hex(" ") for item in config],
        }
    finally:
        keyboard.close()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"aula-f87-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def latest_backup() -> Optional[Path]:
    try:
        candidates = sorted(
            BACKUP_DIR.glob("aula-f87-*.json"),
            key=lambda item: item.stat().st_mtime,
        )
    except OSError:
        return None
    return candidates[-1] if candidates else None


def load_backup(path: Path) -> list[bytes]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AulaError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AulaError(f"invalid backup JSON in {path}: {exc}") from exc

    if not isinstance(data, dict) or data.get("format") != "aula-f87-config-v1":
        raise AulaError("this is not an aula-f87-config-v1 backup")
    if data.get("protocol") != "wireless":
        raise AulaError("only wireless configuration backups can be restored")
    frames = data.get("frames")
    if not isinstance(frames, list) or len(frames) != protocol.WIRELESS_CONFIG_FRAMES:
        raise AulaError("backup does not contain 10 configuration frames")

    result: list[bytes] = []
    for index, item in enumerate(frames):
        if not isinstance(item, str):
            raise AulaError(f"backup frame {index} is not a string")
        try:
            frame = bytes.fromhex(item)
        except ValueError as exc:
            raise AulaError(f"backup frame {index} is not valid hex") from exc
        if len(frame) != 20:
            raise AulaError(f"backup frame {index} is {len(frame)} bytes, expected 20")
        result.append(frame)
    return result


def restore_backup(path: Path) -> dict[str, object]:
    frames = load_backup(path)
    keyboard = open_keyboard()
    try:
        if not isinstance(keyboard, WirelessKeyboard):
            raise UnsupportedSetting(
                "configuration restore is available only on the 2.4 GHz receiver"
            )
        return keyboard.restore_config(frames)
    finally:
        keyboard.close()


def factory_reset() -> dict[str, object]:
    keyboard = open_keyboard()
    try:
        return keyboard.factory_reset()
    finally:
        keyboard.close()


# ---------------------------------------------------------------------------
# Small drawing helpers


def _rgba(color: tuple[int, int, int], alpha: float = 1.0) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.red = max(0.0, min(1.0, color[0] / 255.0))
    rgba.green = max(0.0, min(1.0, color[1] / 255.0))
    rgba.blue = max(0.0, min(1.0, color[2] / 255.0))
    rgba.alpha = max(0.0, min(1.0, alpha))
    return rgba


def _rgb_from_rgba(rgba: Gdk.RGBA) -> tuple[int, int, int]:
    return (
        int(round(max(0.0, min(1.0, rgba.red)) * 255)),
        int(round(max(0.0, min(1.0, rgba.green)) * 255)),
        int(round(max(0.0, min(1.0, rgba.blue)) * 255)),
    )


def _hex_color(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def _rounded_rect(
    cr: "cairo.Context[object]", x: float, y: float, width: float, height: float, radius: float
) -> None:
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    cr.new_sub_path()
    cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    cr.arc(x + width - radius, y + radius, radius, 1.5 * math.pi, 2.0 * math.pi)
    cr.arc(x + width - radius, y + height - radius, radius, 0.0, 0.5 * math.pi)
    cr.arc(x + radius, y + height - radius, radius, 0.5 * math.pi, math.pi)
    cr.close_path()


def _layout_rects(width: int, height: int) -> list[tuple[KeySpec, float, float, float, float]]:
    """Return key rectangles in pixels, scaled to fit the drawing area."""

    pad = 16.0
    spacing = 0.075
    row_spacing = 0.16

    def row_width(row: object) -> float:
        x = 0.0
        gaps = dict(getattr(row, "gaps"))
        for index, key in enumerate(getattr(row, "keys")):
            if index in gaps:
                x += gaps[index] + spacing
            x += key.width + (key.width - 1.0) * spacing + spacing
        return x - spacing

    total_width = max(row_width(row) for row in LAYOUT_ROWS)
    total_height = len(LAYOUT_ROWS) + (len(LAYOUT_ROWS) - 1) * row_spacing
    scale = min(
        max(1.0, (width - 2.0 * pad) / max(total_width, 1.0)),
        max(1.0, (height - 2.0 * pad) / max(total_height, 1.0)),
    )
    origin_x = (width - total_width * scale) / 2.0
    origin_y = (height - total_height * scale) / 2.0

    result: list[tuple[KeySpec, float, float, float, float]] = []
    for row_index, row in enumerate(LAYOUT_ROWS):
        y = origin_y + row_index * (1.0 + row_spacing) * scale
        x = origin_x
        gaps = dict(row.gaps)
        for index, key in enumerate(row.keys):
            if index in gaps:
                x += (gaps[index] + spacing) * scale
            key_width = (key.width + (key.width - 1.0) * spacing) * scale
            result.append((key, x, y, key_width, scale))
            x += key_width + spacing * scale
    return result


class ColorSwatch(Gtk.DrawingArea):
    def __init__(
        self,
        color: tuple[int, int, int],
        callback: Callable[[tuple[int, int, int]], None],
        name: str,
    ):
        super().__init__()
        self._color = color
        self._callback = callback
        self.set_size_request(28, 28)
        self.set_tooltip_text(f"{name} · {_hex_color(color)}")
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

    def _draw(self, _area: Gtk.DrawingArea, cr: "cairo.Context[object]", width: int, height: int) -> None:
        radius = min(width, height) / 2.0
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.12)
        cr.arc(width / 2.0, height / 2.0, radius - 1.0, 0.0, 2.0 * math.pi)
        cr.fill()
        cr.set_source_rgba(
            self._color[0] / 255.0,
            self._color[1] / 255.0,
            self._color[2] / 255.0,
            1.0,
        )
        cr.arc(width / 2.0, height / 2.0, radius - 3.0, 0.0, 2.0 * math.pi)
        cr.fill()

    def _on_pressed(self, _gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float) -> None:
        self._callback(self._color)


class KeyboardPreview(Gtk.DrawingArea):
    """A scaled F87 layout with optional click-and-drag painting."""

    def __init__(
        self,
        color_for_led: Callable[[int], Optional[tuple[int, int, int]]],
        editable: bool = False,
        on_paint: Optional[Callable[[int, int], None]] = None,
    ):
        super().__init__()
        self._color_for_led = color_for_led
        self._editable = editable
        self._on_paint = on_paint
        self._key_rects: dict[int, tuple[float, float, float, float]] = {}
        self._painting = False
        self._drag_last: Optional[int] = None
        self.set_size_request(480, 280)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

        if editable and on_paint is not None:
            primary_click = Gtk.GestureClick.new()
            primary_click.set_button(Gdk.BUTTON_PRIMARY)
            primary_click.connect("pressed", self._on_primary_press)
            primary_click.connect("released", self._on_primary_release)
            self.add_controller(primary_click)

            secondary_click = Gtk.GestureClick.new()
            secondary_click.set_button(Gdk.BUTTON_SECONDARY)
            secondary_click.connect("pressed", self._on_secondary_click)
            self.add_controller(secondary_click)

            motion = Gtk.EventControllerMotion.new()
            motion.connect("motion", self._on_motion)
            motion.connect("leave", self._on_leave)
            self.add_controller(motion)

    def _draw(self, _area: Gtk.DrawingArea, cr: "cairo.Context[object]", width: int, height: int) -> None:
        cr.set_antialias(cairo.ANTIALIAS_BEST)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        cr.set_source_rgba(0.050, 0.040, 0.070, 1.0)
        _rounded_rect(cr, 0.0, 0.0, width, height, 14.0)
        cr.fill()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.07)
        _rounded_rect(cr, 0.5, 0.5, width - 1.0, height - 1.0, 14.0)
        cr.set_line_width(1.0)
        cr.stroke()

        self._key_rects = {}
        for key, x, y, key_width, key_height in _layout_rects(width, height):
            self._key_rects[key.led] = (x, y, key_width, key_height)
            color = self._color_for_led(key.led)
            lit = color is not None and max(color) >= 16

            radius = max(4.0, min(9.0, key_height * 0.18))
            if lit and color is not None:
                cr.set_source_rgba(
                    color[0] / 255.0,
                    color[1] / 255.0,
                    color[2] / 255.0,
                    0.18,
                )
                _rounded_rect(
                    cr,
                    x - 1.5,
                    y - 1.5,
                    key_width + 3.0,
                    key_height + 3.0,
                    radius + 1.5,
                )
                cr.fill()

            cr.set_source_rgba(0.0, 0.0, 0.0, 0.42)
            _rounded_rect(cr, x + 1.5, y + 2.0, key_width, key_height, radius)
            cr.fill()

            if lit and color is not None:
                face = (
                    color[0] / 255.0,
                    color[1] / 255.0,
                    color[2] / 255.0,
                )
                luminance = (
                    0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
                ) / 255.0
                text = (
                    (0.05, 0.035, 0.08)
                    if luminance > 0.56
                    else (0.93, 0.90, 0.98)
                )
            else:
                face = (0.14, 0.115, 0.19)
                text = (0.67, 0.62, 0.77)

            cr.set_source_rgba(*face, 1.0)
            _rounded_rect(cr, x, y, key_width, key_height, radius)
            cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.10 if lit else 0.08)
            _rounded_rect(cr, x + 0.5, y + 0.5, key_width - 1.0, key_height - 1.0, radius)
            cr.set_line_width(1.0)
            cr.stroke()

            if key.label:
                font_size = max(6.0, min(10.0, key_height * 0.23))
                layout = PangoCairo.create_layout(cr)
                layout.set_text(key.label, -1)
                layout.set_font_description(
                    Pango.FontDescription.from_string(f"Sans Bold {font_size}")
                )
                layout.set_width(max(1, int((key_width - 7.0) * Pango.SCALE)))
                layout.set_alignment(Pango.Alignment.CENTER)
                layout.set_ellipsize(Pango.EllipsizeMode.END)
                text_width, text_height = layout.get_pixel_size()
                cr.set_source_rgba(*text, 0.95 if lit else 0.86)
                cr.move_to(
                    x + (key_width - text_width) / 2.0,
                    y + (key_height - text_height) / 2.0,
                )
                PangoCairo.show_layout(cr, layout)

    def _key_at(self, x: float, y: float) -> Optional[int]:
        for led, (left, top, width, height) in self._key_rects.items():
            if left <= x <= left + width and top <= y <= top + height:
                return led
        return None

    def _paint_at(self, x: float, y: float, button: int, dedupe: bool) -> None:
        if self._on_paint is None:
            return
        led = self._key_at(x, y)
        if led is None:
            return
        if dedupe and led == self._drag_last:
            return
        self._drag_last = led
        self._on_paint(led, button)

    def _on_primary_press(
        self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        self._painting = True
        self._drag_last = None
        self._paint_at(x, y, Gdk.BUTTON_PRIMARY, dedupe=False)

    def _on_primary_release(
        self, _gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float
    ) -> None:
        self._painting = False
        self._drag_last = None

    def _on_secondary_click(
        self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        self._drag_last = None
        self._paint_at(x, y, Gdk.BUTTON_SECONDARY, dedupe=False)

    def _on_motion(
        self, _controller: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        if self._painting:
            self._paint_at(x, y, Gdk.BUTTON_PRIMARY, dedupe=True)

    def _on_leave(self, _controller: Gtk.EventControllerMotion) -> None:
        self._painting = False
        self._drag_last = None


class UiState:
    def __init__(self) -> None:
        self.connected = False
        self.summary: dict[str, object] = {}
        self.target_effect_id = 1
        self.brightness = 3
        self.speed = 2
        self.colorful = False
        self.effect_color: tuple[int, int, int] = (138, 92, 246)
        self.paint_color: tuple[int, int, int] = (138, 92, 246)
        self.per_key: dict[int, tuple[int, int, int]] = {}
        self.phase = 0.0


class AulaWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(application=application)
        self.state = UiState()
        self._busy = False
        self._syncing = False
        self._tick_id: Optional[int] = None
        self._reset_dialog: Optional[Adw.AlertDialog] = None

        self.set_title("AULA F87 Pro")
        self.set_default_size(1180, 800)
        self.set_size_request(920, 640)
        self.set_icon_name("input-keyboard")

        self._install_css()
        self._build_ui()
        self._refresh_controls()
        self._update_status_labels()
        self._update_previews()

        self._tick_id = GLib.timeout_add(40, self._on_tick)
        self.connect("close-request", self._on_close)
        GLib.timeout_add(180, self._initial_refresh)

    # ------------------------------------------------------------------
    # UI construction

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build_ui(self) -> None:
        self.stack = Adw.ViewStack()
        self.effects_page = self._build_effects_page()
        self.perkey_page = self._build_perkey_page()
        self.device_page = self._build_device_page()
        self.stack.add_titled(self.effects_page, "lighting", "Lighting")
        self.stack.add_titled(self.perkey_page, "perkey", "Per-key")
        self.stack.add_titled(self.device_page, "device", "Device")
        self.stack.get_page(self.effects_page).set_icon_name(
            "preferences-desktop-screensaver-symbolic"
        )
        self.stack.get_page(self.perkey_page).set_icon_name("input-keyboard-symbolic")
        self.stack.get_page(self.device_page).set_icon_name(
            "preferences-system-symbolic"
        )
        start_page = os.environ.get("AULA_F87_START_PAGE")
        if start_page in {"lighting", "perkey", "device"}:
            self.stack.set_visible_child_name(start_page)

        switcher = Adw.ViewSwitcherTitle()
        switcher.set_stack(self.stack)
        switcher.set_title("AULA F87 Pro")
        switcher.set_subtitle("Onboard RGB")

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)

        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Read the current settings from the keyboard")
        self.refresh_button.connect("clicked", lambda *_args: self.refresh_status())
        header.pack_start(self.refresh_button)

        self.connection_pill = Gtk.Label(label="Checking…")
        self.connection_pill.add_css_class("pill")
        header.pack_end(self.connection_pill)

        menu = Gio.Menu()
        menu.append("About AULA F87", "app.about")
        menu.append("Quit", "app.quit")
        menu_button = Gtk.MenuButton.new()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text("Menu")
        header.pack_end(menu_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

    def _build_effects_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_top(22)
        page.set_margin_bottom(22)
        page.set_margin_start(22)
        page.set_margin_end(22)
        page.set_vexpand(True)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        columns.set_vexpand(True)
        page.append(columns)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.add_css_class("panel")
        left.set_size_request(340, -1)
        left.set_valign(Gtk.Align.START)
        columns.append(left)

        title = Gtk.Label(label="Lighting")
        title.set_xalign(0.0)
        title.add_css_class("page-title")
        left.append(title)
        left.append(
            self._dim_label(
                "Choose an effect, tune it, then save it into the keyboard's onboard memory."
            )
        )

        left.append(self._section_label("EFFECT"))
        self.effect_dropdown = Gtk.DropDown.new_from_strings(
            [effect.name for effect in EFFECT_CHOICES]
        )
        self.effect_dropdown.set_hexpand(True)
        self.effect_dropdown.set_selected(self._effect_index(self.state.target_effect_id))
        self.effect_dropdown.connect("notify::selected", self._on_effect_changed)
        left.append(self.effect_dropdown)

        self.brightness_scale = self._make_scale(0, 4, self.state.brightness)
        left.append(self._slider_block("Brightness", self.brightness_scale))

        self.speed_scale = self._make_scale(0, 4, self.state.speed)
        left.append(self._slider_block("Speed", self.speed_scale))

        spectrum_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        spectrum_label = Gtk.Label(label="Spectrum colours")
        spectrum_label.set_xalign(0.0)
        spectrum_label.set_hexpand(True)
        self.spectrum_switch = Gtk.Switch()
        self.spectrum_switch.set_valign(Gtk.Align.CENTER)
        self.spectrum_switch.set_active(self.state.colorful)
        self.spectrum_switch.connect("notify::active", self._on_spectrum_changed)
        spectrum_row.append(spectrum_label)
        spectrum_row.append(self.spectrum_switch)
        left.append(spectrum_row)

        color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        color_label = Gtk.Label(label="Custom colour")
        color_label.set_xalign(0.0)
        color_label.set_hexpand(True)
        self.color_button = Gtk.ColorDialogButton.new(Gtk.ColorDialog.new())
        self.color_button.set_rgba(_rgba(self.state.effect_color))
        self.color_button.set_valign(Gtk.Align.CENTER)
        self.color_button.connect("notify::rgba", self._on_effect_color_changed)
        color_row.append(color_label)
        color_row.append(self.color_button)
        left.append(color_row)

        left.append(self._build_swatches(self._set_effect_color))

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.apply_effect_button = Gtk.Button(label="Apply to keyboard")
        self.apply_effect_button.add_css_class("suggested-action")
        self.apply_effect_button.add_css_class("apply-button")
        self.apply_effect_button.set_hexpand(True)
        self.apply_effect_button.connect("clicked", self._apply_effect)
        button_row.append(self.apply_effect_button)
        self.off_button = Gtk.Button(label="Off")
        self.off_button.add_css_class("destructive-action")
        self.off_button.connect("clicked", self._turn_off)
        button_row.append(self.off_button)
        left.append(button_row)

        self.effect_status = Gtk.Label(label="Reading keyboard…")
        self.effect_status.set_xalign(0.0)
        self.effect_status.set_wrap(True)
        self.effect_status.add_css_class("dim")
        self.effect_status.add_css_class("small")
        left.append(self.effect_status)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.add_css_class("panel")
        right.set_hexpand(True)
        right.set_vexpand(True)
        columns.append(right)

        preview_title = Gtk.Label(label="Preview")
        preview_title.set_xalign(0.0)
        preview_title.add_css_class("panel-title")
        right.append(preview_title)
        right.append(
            self._dim_label(
                "Animated approximation. The keyboard stores and renders the real effect."
            )
        )

        self.effect_preview = KeyboardPreview(self._effect_color_for_led)
        right.append(self.effect_preview)

        self.preview_caption = Gtk.Label(label="")
        self.preview_caption.set_xalign(0.0)
        self.preview_caption.add_css_class("dim")
        self.preview_caption.add_css_class("small")
        right.append(self.preview_caption)

        return page

    def _build_perkey_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_top(22)
        page.set_margin_bottom(22)
        page.set_margin_start(22)
        page.set_margin_end(22)
        page.set_vexpand(True)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        columns.set_vexpand(True)
        page.append(columns)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.add_css_class("panel")
        left.set_size_request(340, -1)
        left.set_valign(Gtk.Align.START)
        columns.append(left)

        title = Gtk.Label(label="Per-key painting")
        title.set_xalign(0.0)
        title.add_css_class("page-title")
        left.append(title)
        left.append(
            self._dim_label(
                "Pick a colour, then click or drag across the keyboard. "
                "Unpainted keys stay off in custom mode."
            )
        )

        left.append(self._section_label("PAINT COLOUR"))
        color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        color_label = Gtk.Label(label="Colour")
        color_label.set_xalign(0.0)
        color_label.set_hexpand(True)
        self.paint_color_button = Gtk.ColorDialogButton.new(Gtk.ColorDialog.new())
        self.paint_color_button.set_rgba(_rgba(self.state.paint_color))
        self.paint_color_button.set_valign(Gtk.Align.CENTER)
        self.paint_color_button.connect("notify::rgba", self._on_paint_color_changed)
        color_row.append(color_label)
        color_row.append(self.paint_color_button)
        left.append(color_row)

        left.append(self._build_swatches(self._set_paint_color))

        tool_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.eraser_toggle = Gtk.ToggleButton(label="Eraser")
        self.eraser_toggle.set_tooltip_text("Click or drag to turn keys off")
        tool_row.append(self.eraser_toggle)
        fill_button = Gtk.Button(label="Fill all")
        fill_button.set_hexpand(True)
        fill_button.connect("clicked", lambda *_args: self._fill_all())
        tool_row.append(fill_button)
        clear_button = Gtk.Button(label="Clear")
        clear_button.connect("clicked", lambda *_args: self._clear_all())
        tool_row.append(clear_button)
        left.append(tool_row)

        left.append(
            self._dim_label(
                "Left-click paints · right-click erases · choose a swatch or the colour picker."
            )
        )

        self.perkey_apply_button = Gtk.Button(label="Save per-key layout")
        self.perkey_apply_button.add_css_class("suggested-action")
        self.perkey_apply_button.add_css_class("apply-button")
        self.perkey_apply_button.connect("clicked", self._apply_per_key)
        left.append(self.perkey_apply_button)

        self.perkey_count_label = Gtk.Label(
            label=f"0 / {len(LAYOUT_KEYS)} keys painted"
        )
        self.perkey_count_label.set_xalign(0.0)
        self.perkey_count_label.add_css_class("dim")
        self.perkey_count_label.add_css_class("small")
        left.append(self.perkey_count_label)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.add_css_class("panel")
        right.set_hexpand(True)
        right.set_vexpand(True)
        columns.append(right)

        preview_title = Gtk.Label(label="Paint the layout")
        preview_title.set_xalign(0.0)
        preview_title.add_css_class("panel-title")
        right.append(preview_title)
        right.append(
            self._dim_label(
                "This is sent as three 126-position colour planes and saved in custom mode."
            )
        )

        self.perkey_preview = KeyboardPreview(
            self._perkey_color_for_led,
            editable=True,
            on_paint=self._on_paint,
        )
        right.append(self.perkey_preview)

        return page

    def _build_device_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        connection = Adw.PreferencesGroup(title="Connection")
        self.conn_status_row = Adw.ActionRow(title="Status", subtitle="Checking…")
        self.device_refresh_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic"
        )
        self.device_refresh_button.set_valign(Gtk.Align.CENTER)
        self.device_refresh_button.set_tooltip_text("Read the current settings")
        self.device_refresh_button.connect(
            "clicked", lambda *_args: self.refresh_status()
        )
        self.conn_status_row.add_suffix(self.device_refresh_button)
        connection.add(self.conn_status_row)

        self.device_row = Adw.ActionRow(title="Device", subtitle="—")
        connection.add(self.device_row)
        self.profile_row = Adw.ActionRow(title="On keyboard", subtitle="—")
        connection.add(self.profile_row)
        page.add(connection)

        timing = Adw.PreferencesGroup(title="Timing · 2.4 GHz only")
        self.sleep_spin = Gtk.SpinButton.new_with_range(0, 60, 1)
        self.sleep_spin.set_valign(Gtk.Align.CENTER)
        self.sleep_spin.set_tooltip_text("Minutes of inactivity before the keyboard sleeps")
        self.sleep_apply = Gtk.Button(label="Apply")
        self.sleep_apply.set_valign(Gtk.Align.CENTER)
        self.sleep_apply.connect("clicked", self._apply_sleep)
        sleep_row = Adw.ActionRow(
            title="Sleep timer",
            subtitle="Minutes of inactivity before the wireless keyboard sleeps",
        )
        sleep_row.add_suffix(self.sleep_spin)
        sleep_row.add_suffix(self.sleep_apply)
        timing.add(sleep_row)

        self.debounce_spin = Gtk.SpinButton.new_with_range(1, 5, 1)
        self.debounce_spin.set_valign(Gtk.Align.CENTER)
        self.debounce_spin.set_tooltip_text("Debounce time in milliseconds")
        self.debounce_apply = Gtk.Button(label="Apply")
        self.debounce_apply.set_valign(Gtk.Align.CENTER)
        self.debounce_apply.connect("clicked", self._apply_debounce)
        debounce_row = Adw.ActionRow(
            title="Debounce",
            subtitle="Milliseconds the firmware waits before accepting another press",
        )
        debounce_row.add_suffix(self.debounce_spin)
        debounce_row.add_suffix(self.debounce_apply)
        timing.add(debounce_row)
        page.add(timing)

        backup = Adw.PreferencesGroup(title="Backups")
        self.backup_button = Gtk.Button(label="Back up")
        self.backup_button.set_valign(Gtk.Align.CENTER)
        self.backup_button.connect("clicked", self._backup_now)
        backup_row = Adw.ActionRow(
            title="Back up current profile",
            subtitle="Save the 10 onboard configuration frames as JSON",
        )
        backup_row.add_suffix(self.backup_button)
        backup.add(backup_row)

        self.restore_button = Gtk.Button(label="Restore latest")
        self.restore_button.set_valign(Gtk.Align.CENTER)
        self.restore_button.connect("clicked", self._restore_latest)
        self.restore_row = Adw.ActionRow(
            title="Restore latest backup",
            subtitle="No frontend backups yet",
        )
        self.restore_row.add_suffix(self.restore_button)
        backup.add(self.restore_row)

        backup_folder_row = Adw.ActionRow(
            title="Backup folder",
            subtitle=str(BACKUP_DIR),
        )
        backup.add(backup_folder_row)
        page.add(backup)

        danger = Adw.PreferencesGroup(title="Danger zone")
        self.reset_button = Gtk.Button(label="Reset")
        self.reset_button.add_css_class("destructive-action")
        self.reset_button.set_valign(Gtk.Align.CENTER)
        self.reset_button.connect("clicked", self._confirm_reset)
        reset_row = Adw.ActionRow(
            title="Factory reset",
            subtitle="Restore the firmware defaults. This overwrites the current profile.",
        )
        reset_row.add_suffix(self.reset_button)
        danger.add(reset_row)
        page.add(danger)

        return page

    @staticmethod
    def _section_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(0.0)
        label.add_css_class("section-label")
        return label

    @staticmethod
    def _dim_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.add_css_class("dim")
        label.add_css_class("small")
        return label

    @staticmethod
    def _make_scale(minimum: int, maximum: int, value: int) -> Gtk.Scale:
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, float(minimum), float(maximum), 1.0
        )
        scale.set_digits(0)
        scale.set_draw_value(True)
        scale.set_hexpand(True)
        scale.set_value(value)
        scale.set_increments(1.0, 1.0)
        for mark in range(minimum, maximum + 1):
            scale.add_mark(float(mark), Gtk.PositionType.BOTTOM, None)
        return scale

    def _slider_block(self, title: str, scale: Gtk.Scale) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        label = Gtk.Label(label=title)
        label.set_xalign(0.0)
        label.add_css_class("dim")
        label.add_css_class("small")
        box.append(label)
        box.append(scale)
        return box

    def _build_swatches(
        self, callback: Callable[[tuple[int, int, int]], None]
    ) -> Gtk.Grid:
        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(6)
        for index, (name, color) in enumerate(PALETTE):
            swatch = ColorSwatch(color, callback, name)
            grid.attach(swatch, index % 5, index // 5, 1, 1)
        return grid

    # ------------------------------------------------------------------
    # State and controls

    @staticmethod
    def _effect_index(effect_id: int) -> int:
        for index, effect in enumerate(EFFECT_CHOICES):
            if effect.id == effect_id:
                return index
        return 0

    def _sync_controls_from_state(self) -> None:
        self._syncing = True
        try:
            self.effect_dropdown.set_selected(
                self._effect_index(self.state.target_effect_id)
            )
            self.brightness_scale.set_value(self.state.brightness)
            self.speed_scale.set_value(self.state.speed)
            self.spectrum_switch.set_active(self.state.colorful)
            self.color_button.set_rgba(_rgba(self.state.effect_color))
            self.paint_color_button.set_rgba(_rgba(self.state.paint_color))
        finally:
            self._syncing = False
        self._update_effect_control_sensitivity()

    def _update_effect_control_sensitivity(self) -> None:
        effect = EFFECT_CHOICES_BY_ID.get(self.state.target_effect_id)
        if effect is None:
            return
        is_off = effect.id == 0
        supports_color = effect.supports_color and not is_off
        self.brightness_scale.set_sensitive(not is_off)
        self.speed_scale.set_sensitive(effect.supports_speed and not is_off)
        self.spectrum_switch.set_sensitive(supports_color)
        self.color_button.set_sensitive(
            supports_color and not self.spectrum_switch.get_active()
        )
        if supports_color and not self.spectrum_switch.get_active():
            self.color_button.set_sensitive(True)
        if not supports_color and not is_off and not self.spectrum_switch.get_active():
            self.spectrum_switch.set_active(True)

    def _refresh_controls(self) -> None:
        connected = self.state.connected
        wireless = connected and self.state.summary.get("protocol") == "wireless"
        idle = not self._busy

        self.refresh_button.set_sensitive(idle)
        self.device_refresh_button.set_sensitive(idle)
        self.apply_effect_button.set_sensitive(connected and idle)
        self.off_button.set_sensitive(connected and idle)
        self.perkey_apply_button.set_sensitive(connected and idle)
        self.backup_button.set_sensitive(connected and idle)

        for widget in (
            self.sleep_spin,
            self.sleep_apply,
            self.debounce_spin,
            self.debounce_apply,
            self.restore_button,
            self.reset_button,
        ):
            widget.set_sensitive(wireless and idle)

    def _set_pill(self, text: str, state: Optional[bool]) -> None:
        self.connection_pill.set_text(text)
        for css_class in ("pill-ok", "pill-error"):
            self.connection_pill.remove_css_class(css_class)
        if state is True:
            self.connection_pill.add_css_class("pill-ok")
        elif state is False:
            self.connection_pill.add_css_class("pill-error")

    def _toast(self, message: str, timeout: int = 4) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    # ------------------------------------------------------------------
    # Background operations

    def _run_async(
        self,
        action: str,
        function: Callable[[], object],
        on_success: Callable[[object], None],
    ) -> None:
        if self._busy:
            self._toast("Please wait for the current operation to finish.")
            return
        self._busy = True
        self._set_pill("Working…", None)
        self.effect_status.set_text(f"{action}…")
        self._refresh_controls()

        def worker() -> None:
            try:
                result = function()
            except Exception as exc:  # keep worker failures off the GTK loop
                detail = str(exc).strip() or exc.__class__.__name__
                print(f"aula-f87-gui: {action}: {detail}", file=sys.stderr, flush=True)
                GLib.idle_add(self._async_failed, action, detail)
            else:
                GLib.idle_add(self._async_succeeded, action, result, on_success)

        threading.Thread(target=worker, name="aula-f87-hid", daemon=True).start()

    def _async_succeeded(
        self, _action: str, result: object, on_success: Callable[[object], None]
    ) -> None:
        self._busy = False
        self._refresh_controls()
        on_success(result)

    def _async_failed(self, action: str, detail: str) -> None:
        self._busy = False
        if action == "Reading keyboard":
            self.state.connected = False
            self.state.summary = {}
        self._refresh_controls()
        self._update_status_labels()
        short = detail.replace("\n", " ")
        if len(short) > 190:
            short = short[:187] + "…"
        self._toast(f"{action} failed: {short}", timeout=7)

    # ------------------------------------------------------------------
    # Status

    def _initial_refresh(self) -> bool:
        self.refresh_status()
        return GLib.SOURCE_REMOVE

    def refresh_status(self) -> None:
        if self._busy:
            return
        self._run_async("Reading keyboard", read_status, self._apply_summary)

    def _apply_summary(self, result: object, sync_target: bool = True) -> None:
        summary = result if isinstance(result, dict) else {}
        self.state.connected = True
        self.state.summary = dict(summary)

        current_id = int(summary.get("effect_id", 0) or 0)
        if sync_target and current_id in EFFECT_CHOICES_BY_ID:
            self.state.target_effect_id = current_id

        brightness = summary.get("brightness")
        if isinstance(brightness, int):
            self.state.brightness = max(0, min(4, brightness))
        speed = summary.get("speed")
        if isinstance(speed, int):
            self.state.speed = max(0, min(4, speed))
        colorful = summary.get("colorful")
        if isinstance(colorful, bool):
            self.state.colorful = colorful
        elif current_id == 0:
            self.state.colorful = False

        self._sync_controls_from_state()
        self._update_status_labels()
        self._update_previews()
        self._refresh_controls()

    @staticmethod
    def _summary_line(summary: dict[str, object]) -> str:
        parts: list[str] = [str(summary.get("effect", "Unknown"))]
        brightness = summary.get("brightness")
        if isinstance(brightness, int):
            parts.append(f"brightness {brightness}/4")
        speed = summary.get("speed")
        if isinstance(speed, int):
            parts.append(f"speed {speed}/4")
        colorful = summary.get("colorful")
        if isinstance(colorful, bool):
            parts.append("spectrum" if colorful else "custom colour")
        return " · ".join(parts)

    def _update_status_labels(self) -> None:
        if not self.state.connected:
            self._set_pill("Offline", False)
            self.conn_status_row.set_subtitle("No response from the control interface")
            self.device_row.set_subtitle("—")
            self.profile_row.set_subtitle("—")
            self.effect_status.set_text(
                "No response from the keyboard — press a key to wake it, then click Refresh."
            )
            self.preview_caption.set_text("Preview only")
            self.effect_preview.queue_draw()
        else:
            summary = self.state.summary
            protocol_name = str(summary.get("protocol", ""))
            mode = str(summary.get("mode", "Unknown"))
            path = str(summary.get("path", "—"))
            device = str(summary.get("device", "AULA F87 Pro"))
            vid = str(summary.get("vid", "????"))
            pid = str(summary.get("pid", "????"))
            self._set_pill("2.4 GHz" if protocol_name == "wireless" else "USB", True)
            self.conn_status_row.set_subtitle(f"Connected · {mode} · {path}")
            self.device_row.set_subtitle(f"{device} · {vid}:{pid} · {path}")
            line = self._summary_line(summary)
            self.profile_row.set_subtitle(line)
            self.effect_status.set_text(f"On keyboard: {line}")

            sleep_minutes = summary.get("sleep_minutes")
            if isinstance(sleep_minutes, int):
                self.sleep_spin.set_value(sleep_minutes)
            debounce_ms = summary.get("debounce_ms")
            if isinstance(debounce_ms, int):
                self.debounce_spin.set_value(debounce_ms)

            self._update_preview_caption()
            self.effect_preview.queue_draw()

        latest = latest_backup()
        if latest is None:
            self.restore_row.set_subtitle("No frontend backups yet")
        else:
            self.restore_row.set_subtitle(f"Newest: {latest.name}")

    def _update_preview_caption(self) -> None:
        effect = EFFECT_CHOICES_BY_ID.get(self.state.target_effect_id)
        if effect is None:
            self.preview_caption.set_text("Preview only")
            return
        if effect.id == 0:
            mode = "Off"
        elif not effect.supports_color or self.state.colorful:
            mode = "Spectrum"
        else:
            mode = _hex_color(self.state.effect_color)
        self.preview_caption.set_text(
            f"{effect.name} · brightness {self.state.brightness}/4 · {mode}"
        )

    # ------------------------------------------------------------------
    # Preview colours and painting

    def _effect_color_for_led(self, led: int) -> Optional[tuple[int, int, int]]:
        effect = EFFECT_CHOICES_BY_ID.get(self.state.target_effect_id)
        if effect is None or effect.id == 0:
            return None

        level = max(0.0, min(4.0, float(self.state.brightness))) / 4.0
        if level <= 0.0:
            return (0, 0, 0)
        value = 0.18 + 0.82 * level

        if effect.supports_color and not self.state.colorful:
            red, green, blue = self.state.effect_color
            pulse = 1.0
            if effect.id == 2:  # Breathing
                pulse = 0.55 + 0.45 * (
                    0.5 + 0.5 * math.sin(self.state.phase * 2.1 + led * 0.04)
                )
            elif effect.id == 12:  # Reaction
                pulse = 0.65 + 0.35 * math.sin(self.state.phase * 3.0 - led * 0.12)
            return (
                max(0, min(255, int(red * value * pulse))),
                max(0, min(255, int(green * value * pulse))),
                max(0, min(255, int(blue * value * pulse))),
            )

        hue = (led / 126.0 + self.state.phase * 0.012) % 1.0
        if effect.id == 15:  # Windmill
            hue = (led * 0.006 + self.state.phase * 0.03) % 1.0
        elif effect.id == 13:  # Sine Wave
            hue = (
                led * 0.004
                + math.sin(self.state.phase + led * 0.05) * 0.15
            ) % 1.0
        elif effect.id == 17:  # Blossoming
            hue = (led * 0.003 - self.state.phase * 0.02) % 1.0
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.78, value)
        return (int(red * 255), int(green * 255), int(blue * 255))

    def _perkey_color_for_led(self, led: int) -> Optional[tuple[int, int, int]]:
        return self.state.per_key.get(led)

    def _set_effect_color(self, color: tuple[int, int, int]) -> None:
        self.state.effect_color = color
        self._syncing = True
        try:
            self.color_button.set_rgba(_rgba(color))
        finally:
            self._syncing = False
        effect = EFFECT_CHOICES_BY_ID.get(self.state.target_effect_id)
        if effect is not None and effect.supports_color and effect.id != 0:
            self.spectrum_switch.set_active(False)
        self._update_effect_control_sensitivity()
        self._update_previews()

    def _set_paint_color(self, color: tuple[int, int, int]) -> None:
        self.state.paint_color = color
        self._syncing = True
        try:
            self.paint_color_button.set_rgba(_rgba(color))
        finally:
            self._syncing = False
        self.eraser_toggle.set_active(False)
        self._update_previews()

    def _on_effect_color_changed(
        self, _button: Gtk.ColorDialogButton, _pspec: object
    ) -> None:
        if self._syncing:
            return
        self.state.effect_color = _rgb_from_rgba(self.color_button.get_rgba())
        self._update_previews()

    def _on_paint_color_changed(
        self, _button: Gtk.ColorDialogButton, _pspec: object
    ) -> None:
        if self._syncing:
            return
        self.state.paint_color = _rgb_from_rgba(self.paint_color_button.get_rgba())
        self._update_previews()

    def _on_paint(self, led: int, button: int) -> None:
        if self.eraser_toggle.get_active() or button == Gdk.BUTTON_SECONDARY:
            self.state.per_key.pop(led, None)
        else:
            self.state.per_key[led] = self.state.paint_color
        self._update_perkey_status()
        self.perkey_preview.queue_draw()

    def _fill_all(self) -> None:
        for key in LAYOUT_KEYS:
            self.state.per_key[key.led] = self.state.paint_color
        self._update_perkey_status()
        self.perkey_preview.queue_draw()

    def _clear_all(self) -> None:
        self.state.per_key.clear()
        self._update_perkey_status()
        self.perkey_preview.queue_draw()

    def _update_perkey_status(self) -> None:
        self.perkey_count_label.set_text(
            f"{len(self.state.per_key)} / {len(LAYOUT_KEYS)} keys painted"
        )

    def _update_previews(self) -> None:
        self.effect_preview.queue_draw()
        self.perkey_preview.queue_draw()
        self._update_preview_caption()
        self._update_perkey_status()

    def _on_tick(self) -> bool:
        self.state.phase = (self.state.phase + 0.035) % 1000.0
        self.effect_preview.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _on_close(self, *_args: object) -> bool:
        if self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None
        return False

    # ------------------------------------------------------------------
    # Signal handlers

    def _on_effect_changed(self, _dropdown: Gtk.DropDown, _pspec: object) -> None:
        if self._syncing:
            return
        index = self.effect_dropdown.get_selected()
        if index < 0 or index >= len(EFFECT_CHOICES):
            return
        effect = EFFECT_CHOICES[index]
        self.state.target_effect_id = effect.id
        if effect.id == 0:
            self.state.colorful = False
        elif not effect.supports_color:
            self.state.colorful = True
        self._syncing = True
        try:
            self.spectrum_switch.set_active(self.state.colorful)
        finally:
            self._syncing = False
        self._update_effect_control_sensitivity()
        self._update_previews()

    def _on_spectrum_changed(self, _switch: Gtk.Switch, _pspec: object) -> None:
        if self._syncing:
            return
        self.state.colorful = self.spectrum_switch.get_active()
        self._update_effect_control_sensitivity()
        self._update_previews()

    # ------------------------------------------------------------------
    # Write actions

    def _apply_effect(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        effect = EFFECT_CHOICES_BY_ID.get(self.state.target_effect_id)
        if effect is None:
            self._toast("Choose a lighting effect first.")
            return

        brightness = int(round(self.brightness_scale.get_value()))
        speed = int(round(self.speed_scale.get_value()))
        colorful = False
        color: Optional[tuple[int, int, int]] = None

        if effect.id == 0:
            brightness = 0
            speed = 0
        elif effect.supports_color:
            colorful = self.spectrum_switch.get_active()
            if not colorful:
                color = self.state.effect_color
        else:
            colorful = True

        self.state.brightness = brightness
        self.state.speed = speed
        self.state.colorful = colorful

        self._run_async(
            f"Writing {effect.name} to the keyboard",
            lambda: apply_effect(effect.id, brightness, speed, colorful, color),
            self._effect_applied,
        )

    def _effect_applied(self, result: object) -> None:
        self._apply_summary(result, sync_target=True)
        summary = self.state.summary
        self._toast(f"Saved {summary.get('effect', 'lighting')} to the keyboard.")

    def _turn_off(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        self._run_async(
            "Turning the lighting off",
            lambda: apply_effect(0, 0, 0, False, None),
            self._effect_applied,
        )

    def _apply_per_key(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        if not self.state.per_key:
            self._toast("Paint at least one key before saving.")
            return
        colors = dict(self.state.per_key)
        self._run_async(
            f"Writing {len(colors)} per-key colours",
            lambda: apply_per_key(colors),
            self._perkey_applied,
        )

    def _perkey_applied(self, result: object) -> None:
        self._apply_summary(result, sync_target=False)
        summary = self.state.summary
        count = summary.get("per_key_count", len(self.state.per_key))
        self._toast(f"Saved {count} per-key colours. The keyboard is in custom mode.")

    def _apply_sleep(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        minutes = int(round(self.sleep_spin.get_value()))
        self._run_async(
            f"Setting the sleep timer to {minutes} minutes",
            lambda: apply_sleep(minutes),
            lambda result: self._timing_applied(
                result, f"Sleep timer saved ({minutes} min)."
            ),
        )

    def _apply_debounce(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        milliseconds = int(round(self.debounce_spin.get_value()))
        self._run_async(
            f"Setting debounce to {milliseconds} ms",
            lambda: apply_debounce(milliseconds),
            lambda result: self._timing_applied(
                result, f"Debounce saved ({milliseconds} ms)."
            ),
        )

    def _timing_applied(self, result: object, message: str) -> None:
        self._apply_summary(result, sync_target=True)
        self._toast(message)

    def _backup_now(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        self._run_async(
            "Backing up the configuration",
            backup_now,
            self._backup_done,
        )

    def _backup_done(self, result: object) -> None:
        path = Path(result) if isinstance(result, (str, os.PathLike)) else None
        self._update_status_labels()
        if path is not None:
            self._toast(f"Backup saved: {path.name}")
        else:
            self._toast("Backup saved.")

    def _restore_latest(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        path = latest_backup()
        if path is None:
            self._toast("No frontend backups yet. Use Back up first.")
            return
        self._run_async(
            f"Restoring {path.name}",
            lambda: restore_backup(path),
            self._restore_done,
        )

    def _restore_done(self, result: object) -> None:
        self._apply_summary(result, sync_target=True)
        summary = self.state.summary
        self._toast(f"Restored the saved configuration ({summary.get('effect', 'unknown')}).")

    def _confirm_reset(self, *_args: object) -> None:
        if not self.state.connected:
            self._toast("No keyboard is connected.")
            return
        dialog = Adw.AlertDialog.new(
            "Factory reset?",
            "This overwrites the current effect, colours, timing, and per-key data "
            "with the firmware defaults. The current profile cannot be recovered "
            "unless you have a backup.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_response)
        self._reset_dialog = dialog
        dialog.present(self)

    def _on_reset_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        self._reset_dialog = None
        if response != "reset":
            return
        self._run_async(
            "Restoring factory defaults",
            factory_reset,
            self._reset_done,
        )

    def _reset_done(self, result: object) -> None:
        self._apply_summary(result, sync_target=True)
        self._toast("Factory defaults restored to the keyboard.")

    # ------------------------------------------------------------------
    # About

    def show_about(self) -> None:
        about = Adw.AboutWindow()
        about.set_application_name("AULA F87 Pro")
        about.set_application_icon("input-keyboard")
        about.set_version("0.1.0")
        about.set_developer_name("aula-f87")
        about.set_comments(
            "Persistent onboard RGB control for the AULA F87 Pro, including "
            "the 2.4 GHz wireless receiver."
        )
        about.set_modal(True)
        about.set_transient_for(self)
        about.present()


class AulaApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.window: Optional[AulaWindow] = None

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def _on_quit(self, *_args: object) -> None:
        self.quit()

    def _on_about(self, *_args: object) -> None:
        if self.window is not None:
            self.window.show_about()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = AulaWindow(self)
            self.window.present()
        else:
            self.window.present()


def main(argv: Optional[list[str]] = None) -> int:
    application = AulaApplication()
    return application.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
