"""Responsive, retained Panda3D interface for Asterion Expedition.

This module deliberately knows nothing about game state.  The application sends
small view dictionaries and menu models, and receives (action, payload) callbacks.
All positioning uses a 900-unit-high canvas, so a 720p window and a 900p window
share the same composition without making the DirectGUI hit regions inaccurate.
"""

from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any, Callable

from direct.gui import DirectGuiGlobals as DGG
from direct.gui.DirectGui import DirectButton, DirectFrame, DirectScrolledFrame
from direct.showbase.DirectObject import DirectObject
from panda3d.core import CardMaker, LineSegs, NodePath, TextNode, TransparencyAttrib


NAVY = (0.026, 0.065, 0.105, 0.94)
PANEL = (0.038, 0.087, 0.129, 0.96)
ROW = (0.061, 0.119, 0.158, 0.93)
CYAN = (0.38, 0.88, 0.88, 1.0)
CREAM = (0.94, 0.94, 0.87, 1.0)
MUTED = (0.59, 0.72, 0.75, 1.0)
DIM = (0.32, 0.46, 0.52, 1.0)
AMBER = (1.0, 0.69, 0.36, 1.0)
RED = (1.0, 0.42, 0.36, 1.0)
LINE = (0.31, 0.61, 0.65, 0.35)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, _number(value, low)))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return "   /   ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "   /   ".join(str(v) for v in value)
    return str(value)


def _color(value: Any, default=CYAN):
    if isinstance(value, str):
        return {"cyan": CYAN, "amber": AMBER, "orange": AMBER,
                "red": RED, "muted": MUTED, "green": CYAN}.get(value, default)
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        parts = tuple(_clamp(v) for v in value)
        return parts if len(parts) == 4 else parts + (1.0,)
    return default


class _Label:
    """A cached TextNode; changing a value never creates a new GUI widget."""

    def __init__(self, owner, parent, text, x, y, size=18, color=CREAM,
                 bold=False, width=None, align=TextNode.ALeft):
        self.owner = owner
        self.node = TextNode("asterion-label")
        font = owner.bold_font if bold else owner.font
        if font is not None:
            self.node.setFont(font)
        self.node.setAlign(align)
        self.node.setTextColor(*color)
        self.node.setShadow(0.0, 0.04)
        self.node.setShadowColor(0.0, 0.01, 0.018, 0.36)
        if width is not None:
            self.node.setWordwrap(max(1.0, width / size))
        self.path = parent.attachNewNode(self.node)
        self.path.setPos(x, 0, -y)
        self.path.setScale(size)
        self.path.setBin("fixed", owner._layer(parent) + 2)
        self._value = None
        self.set(text)

    def set(self, text):
        text = self.owner._clean(text)
        if text != self._value:
            self.node.setText(text)
            self._value = text

    def color(self, color):
        self.node.setTextColor(*color)


class _Bar:
    def __init__(self, owner, parent, x, y, width, height=5, color=CYAN):
        owner._rect(parent, x, y, width, height, (0.17, 0.28, 0.33, 0.7))
        self.fill = owner._rect(parent, x, y, width, height, color)
        self._value = None

    def set(self, value, color=None):
        value = _clamp(value)
        if value != self._value:
            self.fill.setSx(max(0.00001, value))
            self._value = value
        if color is not None:
            self.fill.setColor(*color)


class GameUI:
    """HUD, title screen, and scrollable data-driven terminal menus.

    Optional panel additions: ``eyebrow``, ``empty_message``, ``show_close``,
    ``close_action``, ``close_payload``. Rows may provide ``accent``, ``tag`` or
    ``progress``. Optional view additions: ``controls``, ``system_name``,
    ``location_detail``, ``tool`` and ``pitch``. Movement feedback accepts
    ``grounded``, ``jetpacking``, ``boosting``, ``braking``, ``drifting``,
    ``flight_assist``, ``throttle``, ``vertical_speed`` and
    ``collision_feedback`` (0..1). None are required.

    A minimal/fake application without an aspect2d NodePath is supported: the
    public methods still store their models and report panel_open, with drawing
    disabled. An offscreen ShowBase uses the complete interface normally.
    """

    def __init__(self, app, callback: Callable[[str, Any], None]):
        self.app = app
        self.callback = callback
        self.font = None
        self.bold_font = None
        self._unicode = False
        self._view = {}
        self._panel = None
        self._panel_kind = None
        self._has_save = False
        self._visible = True
        self._destroyed = False
        self._menu_widgets = []
        self._scroll = None
        self._scroll_extent = 1.0
        self._scroll_positions = {}
        self._scroll_key = None
        self._events = DirectObject()
        self.root = None
        self.hud = None
        self.menu = None
        self.width = 1600.0
        self.height = 900.0
        self._hud_labels = {}
        self._vital_bars = {}
        parent = getattr(app, "aspect2d", None)
        self._available = isinstance(parent, NodePath) and not parent.isEmpty()
        if not self._available:
            return
        self._load_fonts()
        self.root = parent.attachNewNode("asterion-interface")
        self.root.setDepthTest(False)
        self.root.setDepthWrite(False)
        self.root.setTransparency(TransparencyAttrib.MAlpha)
        self.root.setScale(2.0 / self.height)
        self._layout(force=True)
        self._events.accept("window-event", self._on_window)
        self._events.accept("wheel_up", self._scroll_menu, [-1])
        self._events.accept("wheel_down", self._scroll_menu, [1])
        self._events.accept("page_up", self._scroll_menu, [-5])
        self._events.accept("page_down", self._scroll_menu, [5])

    @property
    def panel_open(self):
        return self._panel_kind is not None

    def _load_fonts(self):
        directory = Path(__file__).resolve().parent.parent / "assets" / "fonts"
        loader = getattr(self.app, "loader", None)
        if loader is None or not hasattr(loader, "loadFont"):
            return
        for attr, filename in (("font", "DejaVuSans.ttf"),
                               ("bold_font", "DejaVuSans-Bold.ttf")):
            path = directory / filename
            if not path.is_file():
                continue
            try:
                font = loader.loadFont(str(path))
                if font is not None:
                    font.setPixelsPerUnit(48)
                    font.setPageSize(1024, 1024)
                    setattr(self, attr, font)
            except Exception:
                # Font assets are optional; Panda's built-in font is functional.
                continue
        if self.bold_font is None:
            self.bold_font = self.font
        self._unicode = self.font is not None

    def _clean(self, value):
        value = _as_text(value)
        value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
        if not self._unicode:
            for old, new in (("—", " - "), ("–", "-"), ("•", " / "),
                             ("·", " / "), ("…", "..."), ("→", ">"),
                             ("←", "<"), ("°", " deg"), ("×", "x"),
                             ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
                value = value.replace(old, new)
            value = value.encode("ascii", "replace").decode("ascii")
        return value

    @staticmethod
    def _layer(parent):
        try:
            return int(parent.getNetTag("ui_layer") or 20)
        except (TypeError, ValueError):
            return 20

    def _group(self, parent, name, layer):
        group = parent.attachNewNode(name)
        group.setTag("ui_layer", str(layer))
        group.setBin("fixed", layer)
        return group

    def _rect(self, parent, x, y, width, height, color):
        maker = CardMaker("asterion-card")
        maker.setFrame(0, max(0.01, width), -max(0.01, height), 0)
        node = parent.attachNewNode(maker.generate())
        node.setPos(x, 0, -y)
        node.setColor(*color)
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setBin("fixed", self._layer(parent))
        return node

    def _line(self, parent, points, color=LINE, thickness=1.0):
        lines = LineSegs("asterion-rule")
        lines.setThickness(thickness)
        lines.setColor(*color)
        for index, (x, y) in enumerate(points):
            if index == 0:
                lines.moveTo(x, 0, -y)
            else:
                lines.drawTo(x, 0, -y)
        node = parent.attachNewNode(lines.create())
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setBin("fixed", self._layer(parent) + 1)
        return node

    def _label(self, parent, text, x, y, size=18, color=CREAM, bold=False,
               width=None, align=TextNode.ALeft, key=None):
        label = _Label(self, parent, text, x, y, size, color, bold, width, align)
        if key:
            self._hud_labels[key] = label
        return label

    def _measure(self, text, size=18, bold=False, width=None):
        node = TextNode("asterion-measure")
        font = self.bold_font if bold else self.font
        if font is not None:
            node.setFont(font)
        text = self._clean(text)
        if width is not None:
            node.setWordwrap(max(1.0, width / size))
        node.setText(text)
        rows = max(1, node.getNumRows())
        return node.getWidth() * size, rows * size * 1.24

    def _short(self, value, width, size, bold=False):
        text = self._clean(value).replace("\n", " ")
        if self._measure(text, size, bold)[0] <= width:
            return text
        while text and self._measure(text + "...", size, bold)[0] > width:
            text = text[:-1]
        return text.rstrip() + "..."

    def _card(self, parent, x, y, width, height, accent=CYAN, opacity=0.8):
        self._rect(parent, x, y, width, height, (NAVY[0], NAVY[1], NAVY[2], opacity))
        self._rect(parent, x, y, 3, min(44, height), accent)
        self._line(parent, [(x + 12, y), (x + width, y),
                            (x + width, y + min(16, height))])
        self._line(parent, [(x, y + height - 12), (x, y + height),
                            (x + width - 12, y + height)])

    def _aspect(self):
        window = getattr(self.app, "win", None)
        if window is not None:
            try:
                width, height = window.getXSize(), window.getYSize()
                if width > 0 and height > 0:
                    return width / height
            except (AttributeError, TypeError):
                pass
        try:
            ratio = _number(self.app.getAspectRatio(), 16.0 / 9.0)
            return ratio if ratio > 0.1 else 16.0 / 9.0
        except (AttributeError, TypeError):
            return 16.0 / 9.0

    def _layout(self, force=False):
        if not self._available or self._destroyed:
            return
        aspect = self._aspect()
        width = aspect * self.height
        if not force and abs(width - self.width) < 0.5:
            return
        self.width = width
        self.root.setPos(-aspect, 0, 1)
        self._build_hud()
        if self._panel_kind == "panel" and self._panel is not None:
            self._build_panel()
        elif self._panel_kind == "title":
            self._build_title()
        self._apply_visibility()

    def _on_window(self, window=None):
        self._layout()

    def _build_hud(self):
        if self.hud is not None:
            self.hud.removeNode()
        self.hud = self._group(self.root, "asterion-hud", 20)
        self._hud_labels = {}
        self._vital_bars = {}
        w = self.width
        cx = w / 2
        compact = w < 1350
        left_w = 300 if compact else 345
        right_w = 260 if compact else 280
        self._card(self.hud, 32, 30, left_w, 121, opacity=0.70)
        self._label(self.hud, "ASTERION  /  EXPEDITION", 50, 54, 12, CYAN, bold=True)
        self._label(self.hud, "Uncharted world", 50, 91, 26 if compact else 29,
                    CREAM, bold=True, key="location")
        self._label(self.hud, "SURFACE SURVEY", 50, 120, 14, MUTED, key="biome")
        self._label(self.hud, "", 50, 140, 12, AMBER, key="location_detail")

        rx = w - right_w - 32
        self._card(self.hud, rx, 30, right_w, 121, opacity=0.70)
        self._label(self.hud, "EXPEDITION RESOURCES", rx + 18, 54, 12, CYAN, bold=True)
        self._label(self.hud, "0", rx + 18, 87, 26, CREAM, bold=True, key="credits")
        self._label(self.hud, "CREDITS", rx + right_w - 17, 86, 12, MUTED,
                    align=TextNode.ARight)
        self._label(self.hud, "CARGO  0 / 0", rx + 18, 116, 14, MUTED, key="cargo")
        self._cargo_bar = _Bar(self, self.hud, rx + 18, 130, right_w - 36, 4)

        compass_w = 276 if compact else 348
        self._compass_half = compass_w / 2
        self._rect(self.hud, cx - self._compass_half - 16, 30, compass_w + 32,
                   60, (NAVY[0], NAVY[1], NAVY[2], 0.45))
        self._compass_ticks = []
        for _ in range(19):
            tick = self.hud.attachNewNode("compass-tick")
            self._line(tick, [(0, 0), (0, 7)], (0.52, 0.78, 0.79, 0.72))
            label = self._label(tick, "", 0, -8, 12, MUTED, align=TextNode.ACenter)
            self._compass_ticks.append((tick, label))
        self._line(self.hud, [(cx - 6, 81), (cx, 74), (cx + 6, 81)], AMBER, 1.4)
        self._label(self.hud, "000", cx, 112, 13, CREAM, bold=True,
                    align=TextNode.ACenter, key="heading")
        self._label(self.hud, "", cx, 140, 13, CYAN, align=TextNode.ACenter,
                    key="scan_status")

        mission_w = 285 if compact else 333
        mx = w - mission_w - 32
        self._mission_width = mission_w
        self._card(self.hud, mx, 177, mission_w, 188, accent=AMBER, opacity=0.76)
        self._label(self.hud, "CURRENT OBJECTIVE", mx + 19, 202, 12, AMBER, bold=True)
        self._label(self.hud, "Beyond the horizon", mx + 19, 234, 20, CREAM,
                    bold=True, width=mission_w - 38, key="objective_title")
        self._label(self.hud, "Your next discovery is waiting.", mx + 19, 282,
                    15, MUTED, width=mission_w - 38, key="objective_description")
        self._label(self.hud, "", mx + 19, 341, 13, AMBER, key="objective_progress")
        self._objective_bar = _Bar(self, self.hud, mx + 19, 352, mission_w - 38, 3, AMBER)

        vitals_w = 300 if compact else 328
        vx, vy = 32, 648
        self._card(self.hud, vx, vy, vitals_w, 198, opacity=0.78)
        self._label(self.hud, "EXOSUIT SYSTEMS", vx + 18, vy + 27, 12, CYAN,
                    bold=True, key="vitals_title")
        vital_rows = (("oxygen", "OXYGEN", CYAN), ("hazard", "PROTECTION", AMBER),
                      ("energy", "JETPACK", CYAN), ("shield", "SHIELD", CYAN),
                      ("fuel", "LAUNCH FUEL", AMBER))
        for index, (key, label, color) in enumerate(vital_rows):
            y = vy + 56 + index * 29
            self._label(self.hud, label, vx + 18, y, 11.5, MUTED)
            self._vital_bars[key] = _Bar(self, self.hud, vx + 112, y - 8,
                                        vitals_w - 173, 5, color)
            self._label(self.hud, "100", vx + vitals_w - 18, y, 13, CREAM,
                        align=TextNode.ARight, key="vital_" + key)

        telemetry_w = 264 if compact else 294
        tx = w - telemetry_w - 32
        self._card(self.hud, tx, 714, telemetry_w, 132, opacity=0.78)
        self._label(self.hud, "SURVEY TELEMETRY", tx + 18, 739, 12, CYAN,
                    bold=True, key="telemetry_title")
        self._label(self.hud, "0.0", tx + 18, 776, 28, CREAM, bold=True, key="speed")
        self._label(self.hud, "0", tx + telemetry_w / 2 + 6, 776, 28, CREAM,
                    bold=True, key="altitude")
        self._label(self.hud, "SPEED  M/S", tx + 19, 797, 11, MUTED)
        self._label(self.hud, "ALTITUDE  M", tx + telemetry_w / 2 + 7, 797, 11, MUTED)
        self._line(self.hud, [(tx + 18, 810), (tx + telemetry_w - 18, 810)])
        self._label(self.hud, "X 0  /  Y 0", tx + 18, 833, 12, MUTED, key="coordinates")

        self._reticle = self.hud.attachNewNode("survey-reticle")
        self._reticle.setPos(cx, 0, -438)
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            self._line(self._reticle,
                       [(sx * 18, sy * 7), (sx * 18, sy * 18), (sx * 7, sy * 18)],
                       (0.69, 0.89, 0.87, 0.67), 1.2)
        self._rect(self._reticle, -1.5, -1.5, 3, 3, CREAM)
        self._label(self.hud, "", cx, 492, 19, CREAM, bold=True,
                    align=TextNode.ACenter, key="target")
        self._prompt_back = self._rect(self.hud, cx - 345, 500, 690, 29,
                                       (0.018, 0.045, 0.070, 0.76))
        self._label(self.hud, "", cx, 520, 15, AMBER,
                    align=TextNode.ACenter, key="prompt")
        self._mining_group = self.hud.attachNewNode("mining-progress")
        self._mining_bar = _Bar(self, self._mining_group, cx - 110, 539, 220, 5, AMBER)
        self._label(self._mining_group, "EXTRACTION", cx, 561, 10, AMBER,
                    align=TextNode.ACenter)
        self._mining_group.hide()
        self._label(self.hud, "MULTITOOL / SURVEY", cx, 779, 11, MUTED,
                    align=TextNode.ACenter, key="tool")

        # A small, stable status strip makes acceleration and airborne motion
        # legible without another large panel over the world.
        self._motion_group = self.hud.attachNewNode("movement-feedback")
        self._rect(self._motion_group, cx - 159, 789, 318, 58,
                   (0.018, 0.045, 0.070, 0.66))
        self._label(self._motion_group, "ON FOOT", cx, 808, 14, CYAN, bold=True,
                    align=TextNode.ACenter, key="movement_status")
        self._movement_bar = _Bar(self, self._motion_group, cx - 86, 818, 172, 3)
        self._label(self._motion_group, "", cx, 838, 11.5, MUTED,
                    align=TextNode.ACenter, key="movement_detail")

        self._contact_group = self.hud.attachNewNode("contact-feedback")
        self._contact_group.setPos(cx, 0, -438)
        for side in (-1, 1):
            self._line(self._contact_group,
                       [(side * 32, -13), (side * 37, -13),
                        (side * 37, 13), (side * 32, 13)], AMBER, 1.4)
        self._label(self._contact_group, "CONTACT", 0, -34, 10, AMBER,
                    bold=True, align=TextNode.ACenter)
        self._contact_group.hide()

        notice_w = min(520, max(270, w - 750))
        self._notice_group = self.hud.attachNewNode("expedition-notice")
        self._card(self._notice_group, cx - notice_w / 2, 668, notice_w, 75,
                   accent=AMBER, opacity=0.88)
        self._label(self._notice_group, "EXPEDITION UPDATE", cx - notice_w / 2 + 17,
                    690, 10, AMBER, bold=True)
        self._label(self._notice_group, "", cx - notice_w / 2 + 17, 715,
                    15, CREAM, width=notice_w - 34, key="notice")
        self._notice_group.hide()

        self._rect(self.hud, 0, 863, w, 37, (0.014, 0.043, 0.068, 0.72))
        self._label(self.hud, "", cx, 886, 12 if compact else 13, MUTED,
                    align=TextNode.ACenter, key="controls")
        self._label(self.hud, "", w - 34, 687, 11, MUTED,
                    align=TextNode.ARight, key="fps")
        self._build_cockpit()
        self._update_hud(self._view)

    def _build_cockpit(self):
        w, cx = self.width, self.width / 2
        self._cockpit = self._group(self.hud, "vessel-cockpit-overlay", 18)
        color = (0.38, 0.88, 0.88, 0.30)
        for side in (-1, 1):
            self._line(self._cockpit,
                       [(cx + side * 274, 322), (cx + side * 294, 322),
                        (cx + side * 294, 554), (cx + side * 274, 554)], color)
            for y in range(343, 544, 25):
                self._line(self._cockpit, [(cx + side * 285, y),
                                           (cx + side * 294, y)], color)
        self._line(self._cockpit, [(cx - 114, 438), (cx - 58, 438),
                                   (cx - 42, 451)], color)
        self._line(self._cockpit, [(cx + 114, 438), (cx + 58, 438),
                                   (cx + 42, 451)], color)
        self._label(self._cockpit, "", cx - 309, 305, 11, CYAN,
                    align=TextNode.ACenter, key="flight_speed")
        self._label(self._cockpit, "", cx + 309, 305, 11, CYAN,
                    align=TextNode.ACenter, key="flight_altitude")
        self._label(self._cockpit, "", cx + 309, 580, 11, MUTED,
                    align=TextNode.ACenter, key="vertical_speed")
        self._label(self._cockpit, "", cx - 309, 580, 11, MUTED,
                    align=TextNode.ACenter, key="flight_pitch")
        self._pitch_marker = self._cockpit.attachNewNode("flight-pitch-marker")
        for side in (-1, 1):
            self._line(self._pitch_marker,
                       [(side * 268, -5), (side * 276, 0), (side * 268, 5)],
                       (0.66, 0.92, 0.88, .65), 1.1)
        self._pitch_marker.setPos(cx, 0, -438)
        self._cockpit.hide()

    @staticmethod
    def _movement_readout(view, flying, speed):
        """Return a truthful status even when older callers omit new fields."""
        throttle = _clamp(view.get("throttle", 0))
        if flying:
            assist = bool(view.get("flight_assist", True))
            detail = f"ASSIST {'ON' if assist else 'OFF'}  /  THRUST {throttle:.0%}"
            if view.get("braking"):
                return "BRAKING", detail, 1.0, AMBER
            if view.get("boosting"):
                return "BOOST ACTIVE", detail, max(throttle, .85), AMBER
            if throttle > .025:
                return "ENGINE THRUST", detail, throttle, CYAN
            if view.get("drifting"):
                return "INERTIAL DRIFT", detail, throttle, MUTED
            if speed < .4:
                return "STATION KEEPING" if assist else "FREE FLIGHT", detail, 0, CYAN
            return "ASSIST DECELERATING" if assist else "COASTING", detail, 0, CYAN
        vertical = _number(view.get("vertical_speed", 0))
        if view.get("jetpacking"):
            vitals = view.get("vitals") or {}
            energy = vitals.get("energy", 100) if isinstance(vitals, dict) else 100
            if view.get("braking"):
                return "AIR BRAKE ACTIVE", "HOLD CTRL  /  RELEASE TO DESCEND", _clamp(energy, 0, 100) / 100, AMBER
            return "JETPACK ACTIVE", "HOLD SPACE  /  RELEASE TO DESCEND", _clamp(energy, 0, 100) / 100, CYAN
        if not bool(view.get("grounded", True)):
            direction = "ASCENDING" if vertical > .2 else "DESCENDING" if vertical < -.2 else "AIRBORNE"
            return "AIRBORNE", f"{direction}  {abs(vertical):.1f} M/S", _clamp(abs(vertical) / 22), MUTED
        if speed > .4:
            sprinting = bool(view.get("sprinting", speed > 9.5))
            return "SPRINTING" if sprinting else "TRAVERSING", "GROUNDED  /  SPACE TO JUMP", _clamp(speed / 12), CYAN
        return "ON FOOT", "GROUNDED  /  SPACE TO JUMP", 0, CYAN

    def update(self, view: dict):
        if self._destroyed:
            return
        self._view = dict(view or {})
        if not self._available:
            return
        self._layout()
        self._update_hud(self._view)

    def _update_hud(self, view):
        if not self._hud_labels:
            return
        labels = self._hud_labels
        mode = _as_text(view.get("mode", "surface"))
        flying = mode in ("orbit", "flight", "ship", "space")
        compact = self.width < 1350
        labels["location"].set(self._short(view.get("location", "Uncharted world"),
                                          265 if compact else 310,
                                          26 if compact else 29, True))
        biome = _as_text(view.get("biome", ""))
        mode_name = {"surface": "SURFACE", "flight": "ATMOSPHERIC FLIGHT",
                     "orbit": "ORBITAL FLIGHT", "ship": "VESSEL", "space": "DEEP SPACE"}.get(mode, mode.upper())
        biome_line = mode_name + ((" / " + biome.upper()) if biome else "")
        labels["biome"].set(self._short(biome_line, 266 if compact else 310, 14))
        detail = view.get("location_detail") or view.get("system_name") or ""
        if view.get("storm"):
            detail = "ATMOSPHERIC STRESS  /  FIND SHELTER"
        labels["location_detail"].set(self._short(detail, 266 if compact else 310, 12))
        labels["location_detail"].color(AMBER if view.get("storm") else MUTED)
        credits = max(0, int(_number(view.get("credits", 0))))
        labels["credits"].set(f"{credits:,}")
        cargo = max(0, int(_number(view.get("cargo", 0))))
        capacity = max(0, int(_number(view.get("capacity", 0))))
        labels["cargo"].set(f"CARGO  {cargo:,} / {capacity:,}")
        self._cargo_bar.set(cargo / max(1, capacity), AMBER if cargo >= capacity > 0 else CYAN)

        heading = _number(view.get("heading", 0)) % 360
        labels["heading"].set(f"{int(heading):03d}")
        center_degree = math.floor(heading / 5) * 5
        px_per_degree = self._compass_half / 43
        cardinals = {0: "N", 45: "NE", 90: "E", 135: "SE", 180: "S",
                     225: "SW", 270: "W", 315: "NW"}
        for index, (tick, label) in enumerate(self._compass_ticks):
            degree = center_degree + (index - 9) * 5
            offset = (degree - heading) * px_per_degree
            if abs(offset) > self._compass_half:
                tick.hide()
                continue
            tick.show()
            tick.setPos(self.width / 2 + offset, 0, -67)
            normalized = degree % 360
            label.set(cardinals.get(normalized, str(normalized) if normalized % 30 == 0 else ""))
            label.color(CYAN if normalized in cardinals else MUTED)
        labels["scan_status"].set("SPECTRAL SCAN ACTIVE" if view.get("scanner") else "")

        objective = view.get("objective") or {}
        if not isinstance(objective, dict):
            objective = {"title": str(objective)}
        title = _as_text(objective.get("title", "Beyond the horizon"))
        description = _as_text(objective.get("description", "Your next discovery is waiting."))
        # The compact objective card is a tracker; complete prose lives in Journal.
        labels["objective_title"].set(self._short(title, (self._mission_width - 38) * 1.78, 20, True))
        labels["objective_description"].set(self._short(description, (self._mission_width - 38) * 2.6, 15))
        progress = objective.get("progress", "")
        labels["objective_progress"].set(self._short(progress, self._mission_width - 38, 13))
        ratio = objective.get("fraction", objective.get("ratio", None))
        if ratio is None and isinstance(progress, (float, int)):
            ratio = progress / 100 if progress > 1 else progress
        if ratio is None and isinstance(progress, str):
            match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", progress)
            if match and _number(match.group(2)) > 0:
                ratio = _number(match.group(1)) / _number(match.group(2))
        self._objective_bar.set(0 if ratio is None else ratio)
        vitals = view.get("vitals") or {}
        if not isinstance(vitals, dict):
            vitals = {}
        for key, bar in self._vital_bars.items():
            value = _clamp(vitals.get(key, 100), 0, 100)
            color = RED if value <= 20 else AMBER if value <= 40 or key in ("hazard", "fuel") else CYAN
            bar.set(value / 100, color)
            labels["vital_" + key].set(f"{math.ceil(value):d}")
            labels["vital_" + key].color(RED if value <= 20 else CREAM)
        labels["vitals_title"].set("VESSEL SYSTEMS" if flying else "EXOSUIT SYSTEMS")
        labels["telemetry_title"].set("FLIGHT TELEMETRY" if flying else "SURVEY TELEMETRY")
        speed = abs(_number(view.get("speed", 0)))
        altitude = _number(view.get("altitude", 0))
        labels["speed"].set(f"{speed:.1f}" if speed < 100 else f"{speed:,.0f}")
        labels["altitude"].set(f"{altitude:,.0f}" if abs(altitude) < 100000 else f"{altitude / 1000:.0f}k")
        coordinates = view.get("coordinates", "X 0  /  Y 0")
        if isinstance(coordinates, (list, tuple)):
            coordinates = " / ".join(f"{axis} {_number(value):,.0f}" for axis, value in zip("XYZ", coordinates))
        labels["coordinates"].set(self._short(coordinates, 230 if compact else 258, 12))
        labels["target"].set(self._short(view.get("target", ""), 560, 19, True))
        prompt = view.get("prompt", "")
        labels["prompt"].set(self._short(prompt, 650, 15))
        self._prompt_back.show() if prompt else self._prompt_back.hide()
        labels["tool"].set(view.get("tool", "VESSEL / FLIGHT SYSTEMS" if flying else "MULTITOOL / SURVEY"))
        status, detail, amount, color = self._movement_readout(view, flying, speed)
        labels["movement_status"].set(status)
        labels["movement_status"].color(color)
        labels["movement_detail"].set(detail)
        self._movement_bar.set(amount, color)
        progress = _clamp(view.get("mining_progress", 0))
        if progress > 0:
            self._mining_group.show()
            self._mining_bar.set(progress)
        else:
            self._mining_group.hide()
        self._reticle.setColorScale(*(CYAN if view.get("scanner") else CREAM))
        self._reticle.setScale(1 + (0 if flying else _clamp(speed / 12) * .13))
        contact = _clamp(view.get("collision_feedback", 0))
        if contact > .08:
            self._contact_group.show()
            self._contact_group.setColorScale(1, 1, 1, min(1, contact * 2))
        else:
            self._contact_group.hide()
        notice = view.get("notice", "")
        if notice:
            self._notice_group.show()
            notice_width = min(520, max(270, self.width - 750)) - 34
            labels["notice"].set(self._short(notice, notice_width * 1.8, 15))
        else:
            self._notice_group.hide()
        controls = view.get("controls")
        if controls is None:
            controls = ("W Thrust   S Brake/reverse   SHIFT Boost   SPACE/CTRL Rise/dive   E Dock   F Land   M Map   H Help   ESC Menu" if flying else
                        "WASD Move   SHIFT Sprint   SPACE Jump/jetpack   CTRL Air brake   E Interact   C Scan   I Cargo   H Help   ESC Menu")
        labels["controls"].set(self._short(controls, self.width - 70, 12 if compact else 13))
        fps = view.get("fps")
        labels["fps"].set(f"{_number(fps):.0f} FPS" if fps is not None else "")
        if flying:
            self._cockpit.show()
            labels["flight_speed"].set(f"VEL {speed:.0f}")
            labels["flight_altitude"].set(f"ALT {altitude:.0f}")
            vertical = _number(view.get("vertical_speed", 0))
            labels["vertical_speed"].set(f"V/S {vertical:+.1f}")
            pitch = _clamp(_number(view.get("pitch", 0)), -90, 90)
            labels["flight_pitch"].set(f"PITCH {pitch:+.0f}")
            self._pitch_marker.setZ(-(438 + pitch * 1.12))
        else:
            self._cockpit.hide()

    def _button(self, parent, model, x, y, width=None, height=42, primary=False,
                active=False):
        if not isinstance(model, dict):
            model = {"label": str(model), "enabled": False}
        label = self._clean(model.get("label", "Action"))
        enabled = bool(model.get("enabled", True)) and bool(model.get("action"))
        size = 14
        width = width or min(216, max(110, self._measure(label, size, True)[0] + 30))
        if self._measure(label, size, True)[0] > width - 22:
            size = max(10, (width - 22) / max(1, self._measure(label, 1, True)[0]))
        if primary:
            colors = ((0.38, 0.88, 0.88, 1), (0.26, 0.66, 0.68, 1),
                      (0.61, 0.97, 0.92, 1), (0.12, 0.23, 0.27, 1))
            text_colors = ((0.025, 0.09, 0.12, 1),) * 3 + (DIM,)
        elif active:
            colors = ((0.16, 0.34, 0.38, 1), (0.13, 0.28, 0.32, 1),
                      (0.22, 0.43, 0.46, 1), (0.09, 0.17, 0.22, 1))
            text_colors = (CREAM, CREAM, CREAM, DIM)
        else:
            colors = ((0.10, 0.20, 0.25, 1), (0.12, 0.29, 0.34, 1),
                      (0.17, 0.33, 0.38, 1), (0.062, 0.115, 0.15, 1))
            text_colors = (CYAN, CREAM, CREAM, (0.35, 0.45, 0.48, 1))
        kwargs = dict(parent=parent, text=label, text_scale=size,
                      text_align=TextNode.ACenter, text_pos=(width / 2, -height / 2 - size * 0.30),
                      text_fg=text_colors[0], frameSize=(0, width, -height, 0),
                      frameColor=colors, relief=DGG.FLAT, borderWidth=(0, 0),
                      pos=(x, 0, -y), pressEffect=False, rolloverSound=None,
                      clickSound=None, state=DGG.NORMAL if enabled else DGG.DISABLED,
                      command=self._emit_button,
                      extraArgs=[model.get("action"), model.get("payload"), enabled])
        if self.bold_font is not None:
            kwargs["text_font"] = self.bold_font
        for index, text_color in enumerate(text_colors):
            kwargs[f"text{index}_fg"] = text_color
        button = DirectButton(**kwargs)
        button.setBin("fixed", self._layer(parent) + 3)
        self._menu_widgets.append(button)
        if active:
            self._rect(parent, x, y + height - 2, width, 2, AMBER)
        return button

    def _emit_button(self, action, payload, enabled=True):
        if enabled and action and not self._destroyed:
            self.callback(action, payload)

    def _remember_scroll(self):
        if self._scroll is not None and self._scroll_key is not None:
            try:
                self._scroll_positions[self._scroll_key] = float(self._scroll.verticalScroll["value"])
            except (AttributeError, TypeError, KeyError):
                pass

    def _clear_menu(self):
        self._remember_scroll()
        self._scroll = None
        for widget in reversed(self._menu_widgets):
            try:
                widget.destroy()
            except (AttributeError, KeyError):
                pass
        self._menu_widgets = []
        if self.menu is not None:
            self.menu.removeNode()
            self.menu = None

    def show_panel(self, panel: dict):
        if self._destroyed:
            return
        panel = panel if isinstance(panel, dict) else {"title": str(panel)}
        try:
            model = copy.deepcopy(panel)
        except (TypeError, ValueError):
            model = dict(panel)
        unchanged = self._panel_kind == "panel" and model == self._panel
        self._panel = model
        self._panel_kind = "panel"
        if self._available:
            self._layout()
            if not unchanged or self.menu is None:
                self._build_panel()
            self._apply_visibility()

    def _build_panel(self):
        self._clear_menu()
        self.menu = self._group(self.root, "expedition-terminal", 100)
        panel = self._panel or {}
        self._modal_shade(self.menu, 0.79)
        width = min(1360, self.width - 88)
        height = 786
        x, y = (self.width - width) / 2, 57
        card = self.menu.attachNewNode("terminal-card")
        card.setPos(x, 0, -y)
        self._rect(card, 0, 0, width, height, PANEL)
        self._rect(card, 0, 0, 4, 109, AMBER)
        self._line(card, [(4, 0), (width, 0), (width, 22)], CYAN)
        self._line(card, [(0, height - 22), (0, height), (width - 24, height)], LINE)
        eyebrow = panel.get("eyebrow", "ASTERION  /  EXPEDITION TERMINAL")
        self._label(card, eyebrow, 31, 32, 12, CYAN, bold=True)
        self._label(card, self._short(panel.get("title", "Expedition"), width - 226, 34, True),
                    30, 77, 34, CREAM, bold=True)
        subtitle = panel.get("subtitle", "")
        subtitle_width = width - 64
        self._label(card, subtitle, 32, 110, 16, MUTED, width=subtitle_width)
        subtitle_h = self._measure(subtitle, 16, width=subtitle_width)[1] if subtitle else 0
        if panel.get("show_close", True):
            self._button(card, {"label": "ESC / CLOSE", "action": panel.get("close_action", "close"),
                                "payload": panel.get("close_payload")}, width - 152, 24, 122, 38)
        content_y = max(139, 113 + subtitle_h)
        tabs = [tab for tab in panel.get("tabs", []) if isinstance(tab, dict)]
        if tabs:
            cursor_x = 31
            tab_y = content_y
            for tab in tabs:
                tab_width = min(224, max(110, self._measure(tab.get("label", ""), 14, True)[0] + 34))
                if cursor_x + tab_width > width - 31:
                    cursor_x = 31
                    tab_y += 49
                self._button(card, tab, cursor_x, tab_y, tab_width, 40,
                             active=bool(tab.get("active")))
                cursor_x += tab_width + 8
            content_y = tab_y + 56
        top_buttons = [button for button in panel.get("buttons", []) if isinstance(button, dict)]
        if top_buttons:
            placements, button_h = self._button_placements(top_buttons, width - 62)
            for button, bx, by, bw in placements:
                self._button(card, button, 31 + bx, content_y + by, bw, 38,
                             primary=bool(button.get("primary")))
            content_y += button_h + 15
        self._line(card, [(31, content_y - 6), (width - 31, content_y - 6)], LINE)
        content_height = max(150, height - content_y - 66)
        viewport_width = width - 62
        row_width = viewport_width - 20
        scroll = DirectScrolledFrame(parent=card, pos=(31, 0, -content_y),
                                     frameSize=(0, viewport_width, -content_height, 0),
                                     canvasSize=(0, row_width, -content_height, 0),
                                     frameColor=(0, 0, 0, 0), relief=None,
                                     borderWidth=(0, 0), scrollBarWidth=8,
                                     autoHideScrollBars=True)
        scroll.setBin("fixed", self._layer(card) + 1)
        self._menu_widgets.append(scroll)
        self._scroll = scroll
        self._style_scrollbar(scroll)
        canvas = scroll.getCanvas()
        canvas.setTag("ui_layer", "101")
        rows = [row for row in panel.get("rows", []) if isinstance(row, dict)]
        cursor_y = 8
        for index, row in enumerate(rows):
            row_height = self._build_row(canvas, row, index, 0, cursor_y, row_width)
            cursor_y += row_height + 10
        if not rows:
            self._label(canvas, "NO RECORDS IN THIS VIEW", row_width / 2, 97,
                        18, CREAM, bold=True, align=TextNode.ACenter)
            self._label(canvas, panel.get("empty_message", "New discoveries and expedition records will appear here."),
                        row_width / 2, 136, 16, MUTED, width=row_width - 100,
                        align=TextNode.ACenter)
            cursor_y = 200
        total_height = max(content_height, cursor_y + 8)
        scroll["canvasSize"] = (0, row_width, -total_height, 0)
        self._scroll_extent = max(1, total_height - content_height)
        active = tuple(_as_text(tab.get("label", "")) for tab in tabs if tab.get("active"))
        self._scroll_key = (_as_text(panel.get("id", panel.get("title", "panel"))), active)
        scroll.verticalScroll["value"] = _clamp(self._scroll_positions.get(self._scroll_key, 0))
        self._line(card, [(31, height - 52), (width - 31, height - 52)], LINE)
        footer = panel.get("footer", "Mouse wheel to browse    /    ESC to return to your expedition")
        self._label(card, self._short(footer, width - 217, 12), 32, height - 27, 12, MUTED)
        self._label(card, f"{len(rows):02d} RECORDS", width - 32, height - 27, 11,
                    CYAN, align=TextNode.ARight)

    def _modal_shade(self, parent, alpha):
        # A real PGItem consumes clicks in the gaps around the terminal window.
        shade = DirectFrame(parent=parent, frameSize=(0, self.width, -900, 0),
                            frameColor=(0.007, 0.024, 0.045, alpha), relief=None,
                            state=DGG.NORMAL, suppressMouse=True)
        shade.setBin("fixed", self._layer(parent))
        self._menu_widgets.append(shade)

    def _style_scrollbar(self, scroll):
        for scrollbar in (scroll.verticalScroll, scroll.horizontalScroll):
            scrollbar["frameColor"] = (0.12, 0.23, 0.27, 0.75)
            scrollbar["relief"] = DGG.FLAT
            scrollbar["borderWidth"] = (0, 0)
            scrollbar["thumb_frameColor"] = (0.33, 0.64, 0.67, 0.9)
            scrollbar["thumb_relief"] = DGG.FLAT
            scrollbar["thumb_borderWidth"] = (0, 0)
            scrollbar["incButton_relief"] = None
            scrollbar["decButton_relief"] = None
            scrollbar["incButton_text"] = ""
            scrollbar["decButton_text"] = ""
            scrollbar["scrollSize"] = 0.06
            scrollbar["pageSize"] = 0.3

    def _button_placements(self, buttons, width):
        placements = []
        x = y = 0
        for button in buttons:
            label = button.get("label", "Action")
            button_width = min(216, max(110, self._measure(label, 14, True)[0] + 30))
            button_width = min(button_width, width)
            if x and x + button_width > width:
                x = 0
                y += 46
            placements.append((button, x, y, button_width))
            x += button_width + 8
        return placements, y + 38 if buttons else 0

    def _build_row(self, parent, row, index, x, y, width):
        buttons = [button for button in row.get("buttons", []) if isinstance(button, dict)]
        title, body, meta = (_as_text(row.get(key, "")) for key in ("title", "body", "meta"))
        accent = _color(row.get("accent"), CYAN)
        padding = 22
        right_buttons = bool(buttons) and len(buttons) <= 3 and width >= 970
        side_width = 0
        if right_buttons:
            side_width = min(390, max(248, sum(min(172, max(110, self._measure(
                button.get("label", "Action"), 14, True)[0] + 30)) for button in buttons)
                + 8 * (len(buttons) - 1)))
        text_width = width - padding * 2 - (side_width + 24 if right_buttons else 0)
        if row.get("tag") and not right_buttons:
            title_width = max(200, text_width - 150)
        else:
            title_width = text_width
        _, title_h = self._measure(title or "Record", 20, True, title_width)
        _, body_h = self._measure(body, 16, width=text_width)
        _, meta_h = self._measure(meta, 13, width=text_width)
        body_h = body_h if body else 0
        meta_h = meta_h if meta else 0
        text_h = title_h + (7 + body_h if body else 0) + (9 + meta_h if meta else 0)
        has_progress = isinstance(row.get("progress"), (float, int))
        if has_progress:
            text_h += 16
        button_width = side_width if right_buttons else text_width
        placements, buttons_h = self._button_placements(buttons, button_width)
        row_h = max(82, padding * 2 + text_h)
        if right_buttons:
            row_h = max(row_h, buttons_h + 36)
        elif buttons:
            row_h += 15 + buttons_h
        group = parent.attachNewNode(f"terminal-record-{index}")
        group.setPos(x, 0, -y)
        self._rect(group, 0, 0, width, row_h, ROW)
        self._rect(group, 0, 16, 2, min(34, row_h - 32), accent)
        self._line(group, [(padding, row_h - 1), (width - padding, row_h - 1)],
                   (0.23, 0.43, 0.49, 0.22))
        text_y = padding + 17
        self._label(group, title or "Record", padding, text_y, 20, CREAM,
                    bold=True, width=title_width)
        next_y = text_y + title_h
        if body:
            self._label(group, body, padding, next_y + 3, 16, MUTED, width=text_width)
            next_y += body_h + 7
        if meta:
            self._label(group, meta, padding, next_y + 3, 13, accent, width=text_width)
            next_y += meta_h + 9
        if has_progress:
            bar = _Bar(self, group, padding, next_y + 3, min(330, text_width), 4, accent)
            bar.set(row["progress"])
        if row.get("tag") and not right_buttons:
            tag = self._short(_as_text(row["tag"]).upper(), 135, 11)
            self._label(group, tag, width - padding, text_y - 2, 11, accent,
                        align=TextNode.ARight)
        if right_buttons:
            button_x = width - padding - side_width
            button_y = (row_h - buttons_h) / 2
        else:
            button_x = padding
            button_y = padding + text_h + 15
        for button, bx, by, bw in placements:
            self._button(group, button, button_x + bx, button_y + by, bw, 38,
                         primary=bool(button.get("primary")))
        return row_h

    def _scroll_menu(self, direction):
        if not self._visible or self._panel_kind != "panel" or self._scroll is None:
            return
        try:
            current = float(self._scroll.verticalScroll["value"])
            self._scroll.verticalScroll["value"] = _clamp(current + direction * 82 / self._scroll_extent)
        except (AttributeError, TypeError, KeyError):
            pass

    def show_title(self, has_save: bool):
        if self._destroyed:
            return
        self._panel = None
        self._panel_kind = "title"
        self._has_save = bool(has_save)
        if self._available:
            self._layout()
            self._build_title()
            self._apply_visibility()

    def _build_title(self):
        self._clear_menu()
        self.menu = self._group(self.root, "asterion-title", 150)
        self._modal_shade(self.menu, 0.55)
        w = self.width
        compact = w < 1350
        left = 77 if compact else 98
        # Restrained opaque panels give the menu reliable contrast over any sky.
        self._rect(self.menu, 0, 0, min(670, w * 0.48), 900, (0.012, 0.039, 0.065, 0.67))
        self._rect(self.menu, 0, 0, w, 108, (0.014, 0.038, 0.060, 0.69))
        self._line(self.menu, [(left, 107), (w - left, 107)], LINE)
        self._label(self.menu, "A S T E R I O N", left, 63, 18, CREAM, bold=True)
        self._label(self.menu, "INDEPENDENT EXPLORATION PROGRAM", w - left, 63,
                    11, CYAN, align=TextNode.ARight)
        self._rect(self.menu, left, 194, 32, 3, AMBER)
        self._label(self.menu, "THE UNIVERSE IS STILL UNWRITTEN", left, 226, 12, AMBER, bold=True)
        self._label(self.menu, "ASTERION", left - 5, 309, 66 if compact else 79,
                    CREAM, bold=True)
        self._label(self.menu, "E X P E D I T I O N", left, 352, 24 if compact else 27, CYAN)
        self._label(self.menu, "Chart distant worlds. Follow ancient signals.\nMake a home among the stars.",
                    left, 405, 18, MUTED, width=470 if not compact else 415)
        button_width = 360 if compact else 402
        primary = {"label": "CONTINUE EXPEDITION" if self._has_save else "BEGIN EXPEDITION",
                   "action": "continue" if self._has_save else "new_game"}
        self._button(self.menu, primary, left, 475, button_width, 59, primary=True)
        next_y = 547
        if self._has_save:
            self._button(self.menu, {"label": "NEW EXPEDITION", "action": "new_game"},
                         left, next_y, button_width, 48)
            next_y += 61
        self._button(self.menu, {"label": "FIELD GUIDE", "action": "help"},
                     left, next_y, (button_width - 10) / 2, 47)
        self._button(self.menu, {"label": "SETTINGS", "action": "settings"},
                     left + (button_width + 10) / 2, next_y, (button_width - 10) / 2, 47)
        self._button(self.menu, {"label": "QUIT", "action": "quit"},
                     left, next_y + 60, 112, 39)
        chart_x = w * (0.757 if compact else 0.74)
        chart_y = 400
        radius = 134 if compact else 176
        chart = self.menu.attachNewNode("title-orbital-chart")
        orbital = (0.37, 0.72, 0.73, 0.34)
        for scale, squash in ((1.0, 0.54), (0.74, 1.0), (1.3, 0.83)):
            points = []
            for step in range(97):
                angle = step / 96 * math.tau
                points.append((chart_x + math.cos(angle) * radius * scale,
                               chart_y + math.sin(angle) * radius * scale * squash))
            self._line(chart, points, orbital)
        self._line(chart, [(chart_x - radius * 1.5, chart_y),
                           (chart_x + radius * 1.5, chart_y)], (0.27, 0.55, 0.60, 0.21))
        self._line(chart, [(chart_x, chart_y - radius * 1.42),
                           (chart_x, chart_y + radius * 1.42)], (0.27, 0.55, 0.60, 0.21))
        self._line(chart, [(chart_x - 21, chart_y), (chart_x, chart_y - 21),
                           (chart_x + 21, chart_y), (chart_x, chart_y + 21),
                           (chart_x - 21, chart_y)], AMBER, 1.6)
        self._rect(chart, chart_x - 4, chart_y - 4, 8, 8, CREAM)
        for angle, scale, squash, color in ((0.5, 1.0, 0.54, CYAN),
                                            (3.9, 0.74, 1.0, AMBER),
                                            (5.35, 1.3, 0.83, CREAM)):
            px = chart_x + math.cos(angle) * radius * scale
            py = chart_y + math.sin(angle) * radius * scale * squash
            self._rect(chart, px - 4, py - 4, 8, 8, color)
            self._line(chart, [(px + 9, py), (px + 36, py - 16),
                               (px + 68, py - 16)], orbital)
        self._label(self.menu, "LONG RANGE EXPLORATION", chart_x, 183, 12, CYAN,
                    align=TextNode.ACenter)
        self._label(self.menu, "A SIGNAL. A WORLD. A NEW HORIZON.", chart_x,
                    668 if not compact else 627, 11, MUTED, align=TextNode.ACenter)
        self._label(self.menu, "24", chart_x - 74, 724, 37, CREAM, bold=True,
                    align=TextNode.ACenter)
        self._label(self.menu, "96", chart_x + 74, 724, 37, CREAM, bold=True,
                    align=TextNode.ACenter)
        self._label(self.menu, "STAR SYSTEMS", chart_x - 74, 749, 10, CYAN,
                    align=TextNode.ACenter)
        self._label(self.menu, "WORLDS TO CHART", chart_x + 74, 749, 10, CYAN,
                    align=TextNode.ACenter)
        self._line(self.menu, [(left, 813), (w - left, 813)], LINE)
        self._label(self.menu, "EXPLORE  /  DISCOVER  /  BUILD  /  BELONG", left, 846, 11, MUTED)
        self._label(self.menu, "NATIVE 3D  /  SINGLE PLAYER", w - left, 846, 11,
                    MUTED, align=TextNode.ARight)

    def hide_panel(self):
        if self._destroyed:
            return
        self._panel_kind = None
        self._panel = None
        if self._available:
            self._clear_menu()
            self._apply_visibility()

    def _apply_visibility(self):
        if self.root is None:
            return
        if self._visible:
            self.root.show()
        else:
            self.root.hide()
        if self.hud is not None:
            self.hud.hide() if self.panel_open else self.hud.show()
        if self.menu is not None:
            self.menu.show()

    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if not self._destroyed:
            self._apply_visibility()

    def destroy(self):
        if self._destroyed:
            return
        self._events.ignoreAll()
        self._clear_menu()
        if self.root is not None:
            self.root.removeNode()
        self._destroyed = True
        self._available = False
        self._panel_kind = None
        self.root = self.hud = self.menu = None
