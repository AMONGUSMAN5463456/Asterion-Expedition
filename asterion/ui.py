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
from panda3d.core import (
    AntialiasAttrib, CardMaker, Geom, GeomNode, GeomTriangles, GeomVertexData,
    GeomVertexFormat, GeomVertexWriter, LineSegs, NodePath, SamplerState, TextNode,
    TransparencyAttrib,
)

from . import __version__


# Celestial's interface uses ink, warm paper and a single spectral accent.
# Panels stay dark enough to read over both sunlit clouds and the night sky.
NAVY = (0.021, 0.041, 0.056, 0.94)
PANEL = (0.032, 0.060, 0.076, 0.97)
ROW = (0.056, 0.088, 0.103, 0.91)
CYAN = (0.56, 0.85, 0.86, 1.0)
CREAM = (0.96, 0.955, 0.90, 1.0)
MUTED = (0.68, 0.76, 0.77, 1.0)
DIM = (0.38, 0.48, 0.52, 1.0)
AMBER = (0.98, 0.74, 0.44, 1.0)
RED = (1.0, 0.43, 0.36, 1.0)
LINE = (0.50, 0.70, 0.72, 0.24)


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
                 bold=False, width=None, align=TextNode.ALeft, face=None):
        size = max(13.5, size)
        self.owner = owner
        self.node = TextNode("asterion-label")
        font = owner._font_for(bold or size <= 14, face)
        if font is not None:
            self.node.setFont(font)
        self.node.setAlign(align)
        self.node.setTextColor(*color)
        # Edge veils and cards provide contrast. A second offset glyph pass
        # makes small instrument numerals look doubled at common window sizes.
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
    ``collision_feedback`` (0..1). Optional ``quantum`` contains ``phase``
    (idle/spooling/ready/transit/cooldown), ``target``, ``progress``,
    ``spool_progress`` (0..1), ``remaining`` (metres), ``eta`` (seconds),
    ``cost`` (fuel percentage), and ``cooldown`` (seconds). None are required.

    A minimal/fake application without an aspect2d NodePath is supported: the
    public methods still store their models and report panel_open, with drawing
    disabled. An offscreen ShowBase uses the complete interface normally.
    """

    def __init__(self, app, callback: Callable[[str, Any], None]):
        self.app = app
        self.callback = callback
        self.font = None
        self.bold_font = None
        self.display_font = None
        self.instrument_font = None
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
        self.root.setAntialias(AntialiasAttrib.MLine)
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
        window = getattr(self.app, "win", None)
        gsg = window.getGsg() if window is not None else None
        software = bool(gsg and "tiny" in gsg.getDriverRenderer().lower())
        for attr, filenames in (
            ("font", ("Barlow-Regular.ttf", "DejaVuSans.ttf")),
            ("bold_font", ("Barlow-SemiBold.ttf", "DejaVuSans-Bold.ttf")),
            ("display_font", ("Barlow-Light.ttf", "DejaVuSans.ttf")),
            ("instrument_font", ("Rajdhani-Medium.ttf", "DejaVuSans.ttf")),
        ):
            for filename in filenames:
                path = directory / filename
                if not path.is_file():
                    continue
                try:
                    font = loader.loadFont(str(path))
                    if font is not None:
                        # Loader fonts are shared; atlas configuration needs a
                        # fresh copy when several interfaces are created.
                        font = font.makeCopy()
                        font.setPixelsPerUnit(128 if attr == "display_font" else 40)
                        font.setPageSize(1024, 1024)
                        # Atlas mip levels can mix adjacent glyphs on the macOS
                        # core-profile driver. A moderate native glyph size and
                        # linear filtering keep HUD text clean at both sizes.
                        font.setMinfilter(SamplerState.FTLinear)
                        font.setMagfilter(SamplerState.FTLinear)
                        # World texture anisotropy must not bleed adjacent atlas
                        # glyphs into text. Screen-aligned glyphs need no anisotropy.
                        font.setAnisotropicDegree(1)
                        font.setNativeAntialias(True)
                        if font.getNumPages() == 0:
                            font.setTextureMargin(2)
                        setattr(self, attr, font)
                        break
                except Exception:
                    # Distribution builds without optional fonts remain usable.
                    continue
        self.bold_font = self.bold_font or self.font
        self.display_font = self.display_font or self.font
        self.instrument_font = self.instrument_font or self.font
        self._unicode = self.font is not None

    def _font_for(self, bold=False, face=None):
        if face == "display":
            return self.display_font
        if face == "instrument":
            return self.instrument_font
        return self.bold_font if bold else self.font

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
               width=None, align=TextNode.ALeft, key=None, face=None):
        label = _Label(self, parent, text, x, y, size, color, bold, width, align, face)
        if key:
            self._hud_labels[key] = label
        return label

    def _measure(self, text, size=18, bold=False, width=None):
        size = max(13.5, size) if size != 1 else size
        node = TextNode("asterion-measure")
        font = self._font_for(bold or 1 < size <= 14)
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

    def _fit_lines(self, value, width, size, lines=2, bold=False):
        """Bound trackers by actual wrapped rows, including long-word cases."""
        text = self._clean(value)
        maximum = max(13.5, size) * 1.24 * max(1, lines) + .01
        if self._measure(text, size, bold, width)[1] <= maximum:
            return text
        while text and self._measure(text + "...", size, bold, width)[1] > maximum:
            text = text[:-1]
        return text.rstrip() + "..."

    def _gradient(self, parent, x, y, width, height, start, end, horizontal=False):
        """One native quad with interpolated color; no texture or per-frame work."""
        data = GeomVertexData("interface-gradient", GeomVertexFormat.getV3c4(), Geom.UHStatic)
        vertex = GeomVertexWriter(data, "vertex")
        color = GeomVertexWriter(data, "color")
        points = ((0, 0), (width, 0), (width, height), (0, height))
        for index, (px, py) in enumerate(points):
            vertex.addData3(px, 0, -py)
            finish = index in ((1, 2) if horizontal else (2, 3))
            color.addData4(*(end if finish else start))
        triangles = GeomTriangles(Geom.UHStatic)
        triangles.addVertices(0, 1, 2)
        triangles.addVertices(0, 2, 3)
        geom = Geom(data)
        geom.addPrimitive(triangles)
        node = GeomNode("interface-gradient")
        node.addGeom(geom)
        path = parent.attachNewNode(node)
        path.setPos(x, 0, -y)
        path.setTwoSided(True)
        path.setTransparency(TransparencyAttrib.MAlpha)
        path.setBin("fixed", self._layer(parent))
        return path

    def _arc(self, parent, x, y, radius, start=0, sweep=math.tau,
             color=LINE, thickness=1.0, squash=1.0):
        steps = max(6, int(abs(sweep) * radius / 7))
        return self._line(parent, [
            (x + math.cos(start + sweep * i / steps) * radius,
             y + math.sin(start + sweep * i / steps) * radius * squash)
            for i in range(steps + 1)], color, thickness)

    def _icon(self, parent, kind, x, y, size=26, color=CYAN):
        """A shared, stroke-based pictogram family with a 24-unit design grid."""
        group = parent.attachNewNode("interface-icon-" + str(kind))
        group.setPos(x, 0, -y)
        group.setScale(size / 24)
        paths = {
            "survey": [[(-9, -5), (-9, -9), (-5, -9)], [(5, -9), (9, -9), (9, -5)],
                       [(9, 5), (9, 9), (5, 9)], [(-5, 9), (-9, 9), (-9, 5)],
                       [(0, -5), (0, 5)], [(-5, 0), (5, 0)]],
            "cargo": [[(0, -10), (9, -5), (9, 6), (0, 11), (-9, 6), (-9, -5), (0, -10)],
                      [(-9, -5), (0, 0), (9, -5)], [(0, 0), (0, 11)],
                      [(-5, -7), (5, -2), (5, 2)]],
            "craft": [[(-9, -5), (0, -10), (9, -5), (9, 5), (0, 10), (-9, 5), (-9, -5)],
                      [(-5, 0), (0, -4), (5, 0), (0, 4), (-5, 0)], [(0, -10), (0, -4)], [(0, 4), (0, 10)]],
            "journal": [[(-9, -9), (-1, -7), (-1, 10), (-9, 8), (-9, -9)],
                        [(1, -7), (9, -9), (9, 8), (1, 10), (1, -7)],
                        [(-6, -3), (-4, -2)], [(4, -2), (6, -3)]],
            "build": [[(-10, 8), (10, 8)], [(-8, 8), (-8, -2), (0, -9), (8, -2), (8, 8)],
                      [(-3, 8), (-3, 1), (3, 1), (3, 8)], [(-11, -1), (0, -11), (11, -1)]],
            "ship": [[(0, -11), (10, 8), (0, 4), (-10, 8), (0, -11)], [(0, -2), (0, 4)],
                     [(-3, 9), (-3, 12)], [(3, 9), (3, 12)]],
            "signal": [[(0, 9), (0, -2)], [(-4, 10), (4, 10)],
                       [(-5, -5), (-8, -2), (-8, 3)], [(5, -5), (8, -2), (8, 3)],
                       [(-8, -9), (-12, -4), (-12, 5)], [(8, -9), (12, -4), (12, 5)]],
            "settings": [[(-10, -6), (10, -6)], [(-10, 0), (10, 0)], [(-10, 6), (10, 6)],
                         [(-4, -9), (-4, -3)], [(5, -3), (5, 3)], [(-1, 3), (-1, 9)]],
            "shield": [[(-9, -8), (0, -11), (9, -8), (8, 3), (0, 11), (-8, 3), (-9, -8)],
                       [(-4, 0), (-1, 4), (5, -4)]],
            "contract": [[(-7, -10), (7, -10), (7, 10), (-7, 10), (-7, -10)],
                         [(-3, -5), (3, -5)], [(-3, 0), (3, 0)], [(-3, 5), (1, 5)]],
        }
        if kind in ("planet", "map", "galaxy"):
            self._arc(group, 0, 0, 8, color=color, thickness=1.2)
            self._arc(group, 0, 0, 12, .15, math.pi * 1.8, color=color, squash=.38)
            self._line(group, [(6, -10), (9, -13), (12, -10)], color, 1.2)
        elif kind == "discovery":
            self._line(group, [(-8, 9), (0, 2), (8, -9)], color, 1.2)
            self._line(group, [(0, 2), (-8, 0), (-8, -8), (0, -5), (0, 2),
                               (8, 2), (10, -4), (5, -3)], color, 1.2)
        else:
            for points in paths.get(kind, paths["survey"]):
                self._line(group, points, color, 1.2)
        return group

    def _emblem(self, parent, x, y, size=38, color=CREAM):
        group = parent.attachNewNode("asterion-expedition-mark")
        group.setPos(x, 0, -y)
        group.setScale(size / 40)
        self._line(group, [(-18, 15), (0, -20), (18, 15), (8, 10), (0, -5), (-8, 10), (-18, 15)], color, 1.6)
        self._line(group, [(-12, 20), (0, 14), (12, 20)], CYAN, 1.0)
        self._rect(group, -1.5, 3, 3, 3, AMBER)
        return group

    def _card(self, parent, x, y, width, height, accent=CYAN, opacity=0.8):
        self._gradient(parent, x, y, width, height,
                       (NAVY[0], NAVY[1], NAVY[2], opacity),
                       (NAVY[0], NAVY[1], NAVY[2], opacity * .63))
        self._line(parent, [(x, y + height), (x, y), (x + width, y)], LINE)
        self._rect(parent, x, y, min(28, width), 2, accent)
        self._line(parent, [(x + width - 8, y + height), (x + width, y + height),
                            (x + width, y + height - 8)], LINE)

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
        w, cx = self.width, self.width / 2
        compact = w < 1350
        left_w = 315 if compact else 345
        # Soft edge veils keep type readable while the horizon remains open.
        self._gradient(self.hud, 0, 0, w, 188,
                       (0.009, .019, .028, .63), (0.009, .019, .028, 0))
        self._gradient(self.hud, 0, 726, w, 174,
                       (0.009, .019, .028, 0), (0.009, .019, .028, .63))
        self._emblem(self.hud, 56, 62, 30)
        self._label(self.hud, "ASTERION   /   FIELD OPERATIONS", 86, 53,
                    12, CYAN, bold=True)
        self._label(self.hud, "Uncharted world", 41, 99,
                    29 if compact else 32, CREAM, bold=True, key="location")
        self._line(self.hud, [(42, 111), (42 + left_w, 111)], LINE)
        self._label(self.hud, "SURFACE SURVEY", 42, 133, 14, CREAM, key="biome")
        self._label(self.hud, "", 42, 154, 13, MUTED, key="location_detail")

        right_w = 248 if compact else 270
        rx = w - right_w - 42
        self._label(self.hud, "EXPEDITION ACCOUNT", rx, 53, 12, CYAN, bold=True)
        self._label(self.hud, "0", w - 42, 96, 36, CREAM,
                    align=TextNode.ARight, key="credits", face="instrument")
        self._label(self.hud, "CR", rx, 91, 14, MUTED)
        self._line(self.hud, [(rx, 111), (w - 42, 111)], LINE)
        self._label(self.hud, "CARGO  0 / 0", rx, 133, 14, CREAM, key="cargo")
        self._cargo_bar = _Bar(self, self.hud, rx, 147, right_w, 3)

        compass_w = 258 if compact else 326
        self._compass_half = compass_w / 2
        self._compass_ticks = []
        for _ in range(19):
            tick = self.hud.attachNewNode("compass-tick")
            self._line(tick, [(0, 0), (0, 5)], (0.64, 0.82, 0.82, 0.52))
            label = self._label(tick, "", 0, -10, 14, MUTED, align=TextNode.ACenter,
                                face="instrument")
            self._compass_ticks.append((tick, label))
        self._line(self.hud, [(cx - self._compass_half, 75),
                             (cx + self._compass_half, 75)], (0.54, .72, .73, .23))
        self._line(self.hud, [(cx - 4, 82), (cx, 77), (cx + 4, 82)], AMBER, 1.2)
        self._label(self.hud, "000", cx, 108, 25, CREAM,
                    align=TextNode.ACenter, key="heading", face="instrument")
        self._label(self.hud, "", cx, 134, 12, CYAN, align=TextNode.ACenter,
                    key="scan_status")

        mission_w = 288 if compact else 316
        mx, my = w - mission_w - 42, 188
        self._mission_width = mission_w
        self._card(self.hud, mx, my, mission_w, 176, accent=AMBER, opacity=.64)
        self._icon(self.hud, "signal", mx + 24, my + 22, 19, AMBER)
        self._label(self.hud, "CURRENT OBJECTIVE", mx + 45, my + 27, 12, AMBER, bold=True)
        self._label(self.hud, "Beyond the horizon", mx + 19, my + 59, 21, CREAM,
                    bold=True, width=mission_w - 38, key="objective_title")
        self._label(self.hud, "Your next discovery is waiting.", mx + 19, my + 114,
                    16, MUTED, width=mission_w - 38, key="objective_description")
        self._label(self.hud, "", mx + 19, my + 157, 13, AMBER, key="objective_progress")
        self._objective_bar = _Bar(self, self.hud, mx + 19, my + 166, mission_w - 38, 2, AMBER)

        vitals_w = 306 if compact else 324
        vx, vy = 42, 671
        self._card(self.hud, vx, vy, vitals_w, 176, opacity=.65)
        self._icon(self.hud, "shield", vx + 22, vy + 23, 19, CYAN)
        self._label(self.hud, "EXOSUIT SYSTEMS", vx + 43, vy + 28, 12, CYAN,
                    bold=True, key="vitals_title")
        self._rect(self.hud, vx + vitals_w - 22, vy + 21, 4, 4, CYAN)
        vital_rows = (("oxygen", "OXYGEN", CYAN), ("hazard", "PROTECTION", AMBER),
                      ("energy", "JETPACK", CYAN), ("shield", "SHIELD", CYAN),
                      ("fuel", "LAUNCH FUEL", AMBER))
        for index, (key, label, color) in enumerate(vital_rows):
            y = vy + 55 + index * 26
            self._label(self.hud, label, vx + 19, y, 12.5, MUTED)
            self._vital_bars[key] = _Bar(self, self.hud, vx + 116, y - 7,
                                        vitals_w - 172, 3, color)
            self._label(self.hud, "100", vx + vitals_w - 19, y + 1, 19, CREAM,
                        align=TextNode.ARight, key="vital_" + key, face="instrument")

        telemetry_w = 248 if compact else 278
        tx = w - telemetry_w - 42
        self._card(self.hud, tx, 724, telemetry_w, 123, opacity=.63)
        self._label(self.hud, "SURVEY TELEMETRY", tx + 18, 748, 12, CYAN,
                    bold=True, key="telemetry_title")
        self._label(self.hud, "0.0", tx + 18, 790, 36, CREAM, key="speed", face="instrument")
        self._label(self.hud, "0", tx + telemetry_w / 2 + 6, 790, 36, CREAM,
                    key="altitude", face="instrument")
        self._label(self.hud, "VELOCITY  M/S", tx + 19, 807, 11, MUTED)
        self._label(self.hud, "ALTITUDE  M", tx + telemetry_w / 2 + 7, 807, 11, MUTED)
        self._line(self.hud, [(tx + 18, 818), (tx + telemetry_w - 18, 818)], LINE)
        self._label(self.hud, "X 0  /  Y 0", tx + 18, 839, 12, MUTED, key="coordinates")

        self._reticle = self.hud.attachNewNode("survey-reticle")
        self._reticle.setPos(cx, 0, -438)
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            self._line(self._reticle, [(sx * 14, sy * 6), (sx * 14, sy * 14),
                                      (sx * 6, sy * 14)], (.83, .93, .89, .66), 1.15)
        self._rect(self._reticle, -1, -1, 2, 2, CREAM)
        self._label(self.hud, "", cx, 488, 20, CREAM, bold=True,
                    align=TextNode.ACenter, key="target")
        self._prompt_back = self._gradient(self.hud, cx - 320, 499, 640, 34,
                                           (.012, .026, .035, .70), (.012, .026, .035, .47))
        self._label(self.hud, "", cx, 522, 16, AMBER,
                    align=TextNode.ACenter, key="prompt")
        self._mining_group = self.hud.attachNewNode("mining-progress")
        self._mining_bar = _Bar(self, self._mining_group, cx - 98, 545, 196, 3, AMBER)
        self._label(self._mining_group, "EXTRACTION", cx, 568, 11, AMBER,
                    align=TextNode.ACenter)
        self._mining_group.hide()
        self._quantum_group = self.hud.attachNewNode("quantum-drive-feedback")
        self._quantum_meter = self._quantum_group.attachNewNode("quantum-progress-meter")
        self._quantum_bar = _Bar(self, self._quantum_meter, cx - 116, 546, 232, 3, CYAN)
        self._label(self._quantum_group, "", cx, 570, 13, CYAN,
                    align=TextNode.ACenter, key="quantum_detail")
        self._quantum_group.hide()
        self._label(self.hud, "MULTITOOL / SURVEY", cx, 775, 11, MUTED,
                    align=TextNode.ACenter, key="tool")

        self._motion_group = self.hud.attachNewNode("movement-feedback")
        self._gradient(self._motion_group, cx - 159, 789, 318, 58,
                       (.018, .034, .046, .32), (.018, .034, .046, .65))
        self._label(self._motion_group, "ON FOOT", cx, 809, 14, CYAN, bold=True,
                    align=TextNode.ACenter, key="movement_status")
        self._movement_bar = _Bar(self, self._motion_group, cx - 78, 818, 156, 2)
        self._label(self._motion_group, "", cx, 839, 12, MUTED,
                    align=TextNode.ACenter, key="movement_detail")

        self._contact_group = self.hud.attachNewNode("contact-feedback")
        self._contact_group.setPos(cx, 0, -438)
        for side in (-1, 1):
            self._line(self._contact_group,
                       [(side * 28, -12), (side * 34, -12),
                        (side * 34, 12), (side * 28, 12)], AMBER, 1.4)
        self._label(self._contact_group, "CONTACT", 0, -34, 11, AMBER,
                    bold=True, align=TextNode.ACenter)
        self._contact_group.hide()

        notice_w = min(504, max(270, w - 750))
        self._notice_group = self.hud.attachNewNode("expedition-notice")
        self._card(self._notice_group, cx - notice_w / 2, 658, notice_w, 75,
                   accent=AMBER, opacity=.89)
        self._icon(self._notice_group, "discovery", cx - notice_w / 2 + 24, 692, 24, AMBER)
        self._label(self._notice_group, "EXPEDITION UPDATE", cx - notice_w / 2 + 48,
                    681, 11, AMBER, bold=True)
        self._label(self._notice_group, "", cx - notice_w / 2 + 48, 705,
                    16, CREAM, width=notice_w - 66, key="notice")
        self._notice_group.hide()

        self._line(self.hud, [(42, 864), (w - 42, 864)], (0.5, .7, .72, .15))
        self._label(self.hud, "", cx, 879, 13 if compact else 14, MUTED,
                    align=TextNode.ACenter, key="controls")
        self._label(self.hud, "", w - 43, 704, 11, MUTED,
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
    def _quantum_view(view):
        quantum = view.get("quantum")
        return quantum if isinstance(quantum, dict) else {}

    @staticmethod
    def _quantum_eta(value):
        seconds = min(359999, max(0, math.ceil(_number(value))))
        if seconds >= 3600:
            return f"{seconds // 3600}H {(seconds % 3600) // 60:02d}M"
        return f"{seconds // 60}:{seconds % 60:02d}"

    @staticmethod
    def _quantum_distance(value):
        distance = max(0, _number(value))
        if distance >= 1_000_000:
            return f"{distance / 1_000_000:.1f} MM"
        if distance >= 1000:
            return f"{distance / 1000:.0f} KM"
        return f"{distance:.0f} M"

    @staticmethod
    def _flight_status(view, speed=0):
        """Quantum states take over the retained flight status strip."""
        quantum = GameUI._quantum_view(view)
        phase = quantum.get("phase", "idle")
        spool = _clamp(quantum.get("spool_progress", quantum.get("progress", 0)))
        progress = _clamp(quantum.get("progress", 0))
        cost = _clamp(quantum.get("cost", 0), 0, 100)
        if phase == "spooling":
            return "QUANTUM SPOOLING", f"CALIBRATING {spool:.0%}  /  Q CANCEL", spool, CYAN
        if phase == "ready":
            return "QUANTUM READY", f"Q ENGAGE  /  FUEL {cost:.1f}%", 1, CYAN
        if phase == "transit":
            eta = GameUI._quantum_eta(quantum.get("eta", 0))
            return "QUANTUM TRANSIT", f"{progress:.0%}  /  ETA {eta}  /  Q/E ABORT", progress, CYAN
        if phase == "cooldown":
            cooldown = max(0, _number(quantum.get("cooldown", 0)))
            return "DRIVE COOLING", f"{cooldown:.1f}S  /  CONVENTIONAL FLIGHT", 0, AMBER
        throttle = _clamp(view.get("throttle", 0))
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

    @staticmethod
    def _movement_readout(view, flying, speed):
        """Return a truthful status even when older callers omit new fields."""
        if flying:
            return GameUI._flight_status(view, speed)
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
        quantum = self._quantum_view(view) if flying else {}
        quantum_phase = _as_text(quantum.get("phase", "idle"))
        quantum_active = quantum_phase in ("spooling", "ready", "transit", "cooldown")
        compact = self.width < 1350
        labels["location"].set(self._short(view.get("location", "Uncharted world"),
                                          315 if compact else 345,
                                          29 if compact else 32, True))
        biome = _as_text(view.get("biome", ""))
        mode_name = {"surface": "SURFACE", "flight": "ATMOSPHERIC FLIGHT",
                     "orbit": "ORBITAL FLIGHT", "ship": "VESSEL", "space": "DEEP SPACE"}.get(mode, mode.upper())
        if flying and view.get("flight_phase"):
            mode_name = _as_text(view["flight_phase"]).replace("_", " ").upper()
        if quantum_active:
            mode_name = "QUANTUM " + quantum_phase.upper()
        biome_line = mode_name + ((" / " + biome.upper()) if biome else "")
        labels["biome"].set(self._short(biome_line, 315 if compact else 345, 14))
        detail = view.get("location_detail") or view.get("system_name") or ""
        if flying and "density" in view:
            radial = _number(view.get("radial_speed", view.get("vertical_speed", 0)))
            direction = "ASCENDING" if radial > .5 else "DESCENDING" if radial < -.5 else "LEVEL FLIGHT"
            detail = f"{direction}  /  AIR {_clamp(view.get('density', 0)):.0%}"
            heat = _clamp(view.get("entry_heat", 0))
            if heat > .05 and radial < 0:
                detail += f"  /  HEAT {heat:.0%}"
        if view.get("storm"):
            detail = "ATMOSPHERIC STRESS  /  FIND SHELTER"
        labels["location_detail"].set(self._short(detail, 315 if compact else 345, 13))
        labels["location_detail"].color(AMBER if view.get("storm") or
                                        _clamp(view.get("entry_heat", 0)) > .5 else MUTED)
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
        labels["objective_title"].set(self._fit_lines(title, self._mission_width - 38, 21, bold=True))
        labels["objective_description"].set(self._fit_lines(description, self._mission_width - 38, 16))
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
        altitude = _number(view.get("planet_altitude", view.get("altitude", 0)))
        speed_text = (f"{speed:.1f}" if speed < 100 else
                      f"{speed:,.0f}" if speed < 100000 else f"{speed / 1000:.0f}k")
        labels["speed"].set(speed_text)
        labels["altitude"].set(f"{altitude:,.0f}" if abs(altitude) < 100000 else f"{altitude / 1000:.0f}k")
        coordinates = view.get("coordinates", "X 0  /  Y 0")
        if isinstance(coordinates, (list, tuple)):
            coordinates = " / ".join(f"{axis} {_number(value):,.0f}" for axis, value in zip("XYZ", coordinates))
        labels["coordinates"].set(self._short(coordinates, 230 if compact else 258, 12))
        target = view.get("target") or (quantum.get("target", "") if quantum_active else "")
        labels["target"].set(self._short(target, 560, 20, True))
        prompt = view.get("prompt", "")
        if not prompt and quantum_active:
            prompt = {"spooling": "Q / Cancel quantum spool", "ready": "Q / Engage quantum drive",
                      "transit": "Q or E / Abort quantum travel", "cooldown": ""}[quantum_phase]
        elif not prompt and quantum.get("target"):
            prompt = "Q / Spool quantum drive"
        labels["prompt"].set(self._short(prompt, 610, 16))
        self._prompt_back.show() if prompt else self._prompt_back.hide()
        tool = "VESSEL / QUANTUM DRIVE" if quantum_active else "VESSEL / FLIGHT SYSTEMS" if flying else "MULTITOOL / SURVEY"
        labels["tool"].set(view.get("tool", tool))
        status, detail, amount, color = self._movement_readout(view, flying, speed)
        labels["movement_status"].set(status)
        labels["movement_status"].color(color)
        labels["movement_detail"].set(self._short(detail, 298, 13.5))
        self._movement_bar.set(amount, color)
        if quantum_active:
            self._quantum_group.show()
            self._quantum_meter.show()
            self._quantum_bar.set(amount, color)
            if quantum_phase == "transit":
                remaining = self._quantum_distance(quantum.get("remaining", 0))
                eta = self._quantum_eta(quantum.get("eta", 0))
                quantum_detail = f"{amount:.0%}  /  {remaining} REMAINING  /  ETA {eta}"
            elif quantum_phase == "cooldown":
                self._quantum_meter.hide()
                cooldown = max(0, _number(quantum.get("cooldown", 0)))
                quantum_detail = f"DRIVE COOLING  /  {cooldown:.1f}S"
            else:
                cost = _clamp(quantum.get("cost", 0), 0, 100)
                calibration = "CALIBRATED" if quantum_phase == "ready" else f"CALIBRATING {amount:.0%}"
                quantum_detail = f"{calibration}  /  FUEL {cost:.1f}%"
            labels["quantum_detail"].set(self._short(quantum_detail, 520, 13.5))
            labels["quantum_detail"].color(color)
        else:
            self._quantum_group.hide()
            labels["quantum_detail"].set("")
        progress = _clamp(view.get("mining_progress", 0))
        if progress > 0 and not quantum_active:
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
            notice_width = min(504, max(270, self.width - 750)) - 66
            labels["notice"].set(self._fit_lines(notice, notice_width, 16))
        else:
            self._notice_group.hide()
        controls = view.get("controls")
        if controls is None:
            if quantum_phase == "transit":
                controls = "Q / E Abort quantum travel   M Map   H Help   ESC Menu"
            elif quantum_phase in ("spooling", "ready"):
                quantum_control = "Q Engage quantum" if quantum_phase == "ready" else "Q Cancel spool"
                controls = quantum_control + "   W Thrust   S Brake/reverse   M Map   H Help   ESC Menu"
            elif flying:
                quantum_control = "Q Quantum   " if quantum.get("target") or view.get("target") else ""
                controls = quantum_control + "W Thrust   S Brake/reverse   SHIFT Boost   SPACE/CTRL Rise/dive   E Dock   F Land   M Map   H Help   ESC Menu"
            else:
                controls = "WASD Move   SHIFT Sprint   SPACE Jump/jetpack   CTRL Air brake   E Interact   C Scan   I Cargo   H Help   ESC Menu"
        labels["controls"].set(self._short(controls, self.width - 84, 13 if compact else 14))
        fps = view.get("fps")
        labels["fps"].set(f"{_number(fps):.0f} FPS" if fps is not None else "")
        if flying:
            self._cockpit.show()
            labels["flight_speed"].set(f"VEL {speed:.0f}" if speed < 100000 else f"VEL {speed / 1000:.0f}k")
            labels["flight_altitude"].set(f"ALT {altitude:.0f}")
            vertical = _number(view.get("radial_speed", view.get("vertical_speed", 0)))
            labels["vertical_speed"].set(f"V/S {vertical:+.1f}")
            pitch = _clamp(_number(view.get("pitch", 0)), -90, 90)
            labels["flight_pitch"].set(f"PITCH {pitch:+.0f}")
            self._pitch_marker.setZ(-(438 + pitch * 1.12))
        else:
            self._cockpit.hide()

    def _button(self, parent, model, x, y, width=None, height=42, primary=False,
                active=False, icon=None, align_left=False):
        if not isinstance(model, dict):
            model = {"label": str(model), "enabled": False}
        label = self._clean(model.get("label", "Action"))
        enabled = bool(model.get("enabled", True)) and bool(model.get("action"))
        size = 15
        width = width or min(224, max(116, self._measure(label, size, True)[0] + 36))
        inset = 48 if icon else 22
        available = width - (inset + 14 if align_left or icon else 28)
        if primary and align_left:
            available -= 26
        if self._measure(label, size, True)[0] > available:
            size = max(10, available / max(1, self._measure(label, 1, True)[0]))
        if primary:
            colors = (CREAM, (.73, .83, .80, 1), (.71, .93, .91, 1), (.13, .20, .22, 1))
            text_colors = ((.024, .058, .070, 1),) * 3 + (DIM,)
        elif active:
            colors = ((.105, .202, .228, 1), (.12, .25, .27, 1),
                      (.16, .29, .31, 1), (.065, .102, .12, 1))
            text_colors = (CREAM, CREAM, CREAM, DIM)
        else:
            colors = ((.075, .127, .152, .94), (.11, .23, .25, 1),
                      (.145, .245, .265, 1), (.045, .078, .09, .88))
            text_colors = (CYAN, CREAM, CREAM, (.36, .46, .49, 1))
        left_aligned = align_left or bool(icon)
        kwargs = dict(parent=parent, text=label, text_scale=size,
                      text_align=TextNode.ALeft if left_aligned else TextNode.ACenter,
                      text_pos=(inset if left_aligned else width / 2, -height / 2 - size * .30),
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
        decoration = self._group(parent, "button-detail", self._layer(parent) + 4)
        if icon:
            self._icon(decoration, icon, x + 23, y + height / 2, 21, CREAM if active else CYAN)
        if active:
            self._rect(decoration, x, y + 11, 2, height - 22, AMBER)
        elif not primary:
            self._line(decoration, [(x, y + height), (x + width, y + height)],
                       (.46, .64, .66, .23 if enabled else .07))
        if primary and align_left:
            arrow_color = text_colors[0] if enabled else DIM
            self._line(decoration, [(x + width - 41, y + height / 2),
                                    (x + width - 24, y + height / 2)], arrow_color, 1.2)
            self._line(decoration, [(x + width - 29, y + height / 2 - 5),
                                    (x + width - 24, y + height / 2),
                                    (x + width - 29, y + height / 2 + 5)], arrow_color, 1.2)
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

    @staticmethod
    def _section_icon(section):
        return {"inventory": "cargo", "trade": "cargo", "craft": "craft",
                "upgrades": "craft", "map": "map", "galaxy": "galaxy",
                "journal": "journal", "discoveries": "discovery", "build": "build",
                "settings": "settings", "help": "journal", "signal": "signal",
                "contracts": "contract", "rescue": "ship", "pause": "survey"}.get(section, "survey")

    def _build_panel(self):
        self._clear_menu()
        self.menu = self._group(self.root, "expedition-terminal", 100)
        panel = self._panel or {}
        self._modal_shade(self.menu, .72)
        tabs = [tab for tab in panel.get("tabs", []) if isinstance(tab, dict)]
        width = min(1424 if tabs else 1270, self.width - 88)
        height = 794
        x, y = (self.width - width) / 2, 53
        card = self.menu.attachNewNode("terminal-card")
        card.setPos(x, 0, -y)
        self._gradient(card, 0, 0, width, height, PANEL, (.021, .041, .055, .98))
        self._line(card, [(0, height), (0, 0), (width, 0), (width, height)], LINE)
        self._rect(card, 0, 0, 56, 2, AMBER)
        rail_width = 211 if tabs else 0
        content_x = rail_width + (31 if tabs else 38)
        content_width = width - content_x - 38
        section = _as_text(panel.get("id", ""))
        section_icon = self._section_icon(section)
        if tabs:
            self._gradient(card, 0, 0, rail_width, height,
                           (.018, .035, .047, .93), (.025, .047, .062, .85))
            self._line(card, [(rail_width, 26), (rail_width, height - 26)], LINE)
            self._emblem(card, 37, 47, 30)
            self._label(card, "ASTERION", 64, 46, 17, CREAM, bold=True)
            self._label(card, "EXPEDITION TERMINAL", 25, 92, 11, CYAN, bold=True)
            self._line(card, [(25, 111), (rail_width - 24, 111)], LINE)
            for index, tab in enumerate(tabs):
                icon = self._section_icon(_as_text(tab.get("payload", "")))
                self._button(card, tab, 13, 134 + index * 53, rail_width - 26, 45,
                             active=bool(tab.get("active")), icon=icon)
            self._icon(card, section_icon, rail_width / 2, height - 153, 48, (.47, .69, .71, .44))
            self._label(card, "FIELD SYSTEMS", rail_width / 2, height - 103, 11, MUTED,
                        align=TextNode.ACenter)
            self._label(card, "CELESTIAL", rail_width / 2, height - 80, 14, CYAN,
                        align=TextNode.ACenter, face="instrument")
        self._icon(card, section_icon, content_x + 12, 35, 21, CYAN)
        eyebrow = panel.get("eyebrow", "FIELD TERMINAL   /   " + (section or "EXPEDITION").replace("_", " ").upper())
        self._label(card, self._short(eyebrow, content_width - 190, 12),
                    content_x + 33, 40, 12, CYAN, bold=True)
        self._label(card, self._short(panel.get("title", "Expedition"), content_width - 8, 40),
                    content_x - 1, 97, 40, CREAM, face="display")
        subtitle = panel.get("subtitle", "")
        self._label(card, subtitle, content_x, 129, 17, MUTED, width=content_width)
        subtitle_h = self._measure(subtitle, 17, width=content_width)[1] if subtitle else 0
        if panel.get("show_close", True):
            self._button(card, {"label": "ESC   /   CLOSE", "action": panel.get("close_action", "close"),
                                "payload": panel.get("close_payload")}, width - 174, 18, 137, 36)
        content_y = max(160, 135 + subtitle_h)
        top_buttons = [button for button in panel.get("buttons", []) if isinstance(button, dict)]
        if top_buttons:
            placements, button_h = self._button_placements(top_buttons, content_width)
            for button, bx, by, bw in placements:
                selected = button.get("action") == "open" and button.get("payload") == section
                self._button(card, button, content_x + bx, content_y + by, bw, 38,
                             primary=bool(button.get("primary")), active=selected)
            content_y += button_h + 17
        self._line(card, [(content_x, content_y - 3), (width - 38, content_y - 3)], LINE)
        rows = [row for row in panel.get("rows", []) if isinstance(row, dict)]
        self._label(card, {"inventory": "MATERIALS & SUPPLIES", "map": "DESTINATIONS & WAYPOINTS",
                           "galaxy": "SYSTEM CATALOG", "journal": "EXPEDITION CHAPTERS",
                           "discoveries": "SURVEY RECORDS", "settings": "PREFERENCES"}.get(section, "FIELD RECORDS"),
                    content_x, content_y + 20, 11, MUTED, bold=True)
        self._label(card, f"{len(rows):02d} ENTRIES", width - 38, content_y + 20, 11,
                    CYAN, align=TextNode.ARight)
        content_y += 34
        content_height = max(150, height - content_y - 63)
        viewport_width = content_width
        row_width = viewport_width - 18
        scroll = DirectScrolledFrame(parent=card, pos=(content_x, 0, -content_y),
                                     frameSize=(0, viewport_width, -content_height, 0),
                                     canvasSize=(0, row_width, -content_height, 0),
                                     frameColor=(0, 0, 0, 0), relief=None,
                                     borderWidth=(0, 0), scrollBarWidth=5,
                                     autoHideScrollBars=True)
        scroll.setBin("fixed", self._layer(card) + 1)
        self._menu_widgets.append(scroll)
        self._scroll = scroll
        self._style_scrollbar(scroll)
        canvas = scroll.getCanvas()
        canvas.setTag("ui_layer", "101")
        cursor_y = 5
        for index, row in enumerate(rows):
            row_height = self._build_row(canvas, row, index, 0, cursor_y, row_width)
            cursor_y += row_height + 11
        if not rows:
            self._icon(canvas, section_icon, row_width / 2, 89, 55, DIM)
            self._label(canvas, "Your next discovery belongs here.", row_width / 2, 154,
                        25, CREAM, align=TextNode.ACenter, face="display")
            self._label(canvas, panel.get("empty_message", "New discoveries and expedition records will appear here."),
                        row_width / 2, 191, 17, MUTED, width=row_width - 100,
                        align=TextNode.ACenter)
            cursor_y = 230
        total_height = max(content_height, cursor_y + 8)
        scroll["canvasSize"] = (0, row_width, -total_height, 0)
        self._scroll_extent = max(1, total_height - content_height)
        active = tuple(_as_text(tab.get("label", "")) for tab in tabs if tab.get("active"))
        self._scroll_key = (_as_text(panel.get("id", panel.get("title", "panel"))), active)
        scroll.verticalScroll["value"] = _clamp(self._scroll_positions.get(self._scroll_key, 0))
        self._line(card, [(content_x, height - 48), (width - 38, height - 48)], LINE)
        footer = panel.get("footer", "Mouse wheel to browse    /    ESC to return to your expedition")
        self._label(card, self._short(footer, content_width - 12, 12),
                    content_x, height - 22, 12, MUTED)

    def _modal_shade(self, parent, alpha):
        # A real PGItem consumes clicks in the gaps around the terminal window.
        shade = DirectFrame(parent=parent, frameSize=(0, self.width, -900, 0),
                            frameColor=(0.007, 0.024, 0.045, alpha), relief=None,
                            state=DGG.NORMAL, suppressMouse=True)
        shade.setBin("fixed", self._layer(parent))
        self._menu_widgets.append(shade)

    def _style_scrollbar(self, scroll):
        for scrollbar in (scroll.verticalScroll, scroll.horizontalScroll):
            scrollbar["frameColor"] = (0.13, 0.22, 0.25, 0.45)
            scrollbar["relief"] = DGG.FLAT
            scrollbar["borderWidth"] = (0, 0)
            scrollbar["thumb_frameColor"] = (0.52, 0.74, 0.75, 0.82)
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
            button_width = min(224, max(116, self._measure(label, 15, True)[0] + 36))
            button_width = min(button_width, width)
            if x and x + button_width > width:
                x = 0
                y += 46
            placements.append((button, x, y, button_width))
            x += button_width + 8
        return placements, y + 38 if buttons else 0

    def _planet_glyph(self, parent, x, y, radius, tint=CYAN, seed=0, detailed=False):
        """A lit, shaded atlas globe made from vertex color, with no bitmap assets."""
        rings, segments = (28, 96) if detailed else (10, 40)
        data = GeomVertexData("atlas-globe", GeomVertexFormat.getV3c4(), Geom.UHStatic)
        vertex, color = GeomVertexWriter(data, "vertex"), GeomVertexWriter(data, "color")
        for ring in range(rings + 1):
            r = ring / rings
            nz = math.sqrt(max(0, 1 - r * r))
            for step in range(segments + 1):
                angle = step / segments * math.tau
                nx, ny = math.cos(angle) * r, math.sin(angle) * r
                light = max(0, -.59 * nx - .48 * ny + .65 * nz)
                # Broad contour bands read as an atlas, not invented surface data.
                band = math.sin(nx * 7.3 + math.sin(ny * 5 + seed) * 1.8 + nz * 6.2)
                terrain = .93 + .07 * band
                shade = (.11 + .81 * light) * terrain
                rim = (1 - nz) ** 4 * max(.08, .28 - nx * .17)
                vertex.addData3(nx * radius, 0, -ny * radius)
                color.addData4(min(1, tint[0] * shade + rim * .29),
                               min(1, tint[1] * shade + rim * .59),
                               min(1, tint[2] * shade + rim * .67), 1)
        triangles = GeomTriangles(Geom.UHStatic)
        for ring in range(rings):
            for step in range(segments):
                first = ring * (segments + 1) + step
                triangles.addVertices(first, first + segments + 1, first + 1)
                triangles.addVertices(first + 1, first + segments + 1, first + segments + 2)
        geom = Geom(data)
        geom.addPrimitive(triangles)
        node = GeomNode("atlas-globe")
        node.addGeom(geom)
        globe = parent.attachNewNode(node)
        globe.setPos(x, 0, -y)
        globe.setTwoSided(True)
        globe.setBin("fixed", self._layer(parent))
        self._arc(parent, x, y, radius + 1, math.pi * .82, math.pi * 1.04,
                  (*tint[:3], .59), 1.15)
        if detailed:
            for offset in (-.6, -.3, 0, .3, .6):
                latitude_r = math.sqrt(1 - offset * offset) * radius
                self._arc(parent, x, y + radius * offset, latitude_r,
                          .13, math.pi - .26, (.49, .72, .73, .13), .8, .13)
            self._arc(parent, x, y, radius * 1.025, math.pi * 1.0, math.pi * .62,
                      (.6, .89, .88, .23), 2.0)
        return globe

    def _record_visual(self, row, index):
        section = _as_text((self._panel or {}).get("id", ""))
        icon = self._section_icon(section)
        title = _as_text(row.get("title", "")).lower()
        body = _as_text(row.get("body", "")).lower()
        tint = _color(row.get("accent"), CYAN)
        actions = {button.get("action") for button in row.get("buttons", []) if isinstance(button, dict)}
        if "travel" in actions or "warp" in actions:
            icon = "planet"
            if any(word in body for word in ("desert", "arid", "volcanic", "scorched")):
                tint = (.88, .61, .39, 1)
            elif any(word in body for word in ("frozen", "ice", "glacial")):
                tint = (.69, .81, .88, 1)
            elif any(word in body for word in ("lush", "forest", "verdant", "tropical")):
                tint = (.49, .76, .64, 1)
        elif "ship" in title or "courier" in title:
            icon = "ship"
        elif "ruin" in title or "signal" in title:
            icon = "signal"
        elif "exchange" in title:
            icon = "cargo"
        elif section in ("inventory", "trade"):
            if any(word in title for word in ("cell", "gel", "repair")):
                icon = "craft"
            elif "carbon" in title:
                icon, tint = "discovery", (.57, .80, .64, 1)
            elif "copper" in title or "sodium" in title:
                tint = AMBER
        return icon, tint

    def _build_row(self, parent, row, index, x, y, width):
        buttons = [button for button in row.get("buttons", []) if isinstance(button, dict)]
        title, body, meta = (_as_text(row.get(key, "")) for key in ("title", "body", "meta"))
        icon, accent = self._record_visual(row, index)
        padding, text_x = 22, 91
        right_buttons = bool(buttons) and len(buttons) <= 3 and width >= 1000
        side_width = 0
        if right_buttons:
            side_width = min(358, max(244, sum(min(176, max(116, self._measure(
                button.get("label", "Action"), 15, True)[0] + 36)) for button in buttons)
                + 8 * (len(buttons) - 1)))
        text_width = width - text_x - padding - (side_width + 25 if right_buttons else 0)
        title_width = max(150, text_width - (148 if row.get("tag") and not right_buttons else 0))
        _, title_h = self._measure(title or "Record", 22, True, title_width)
        _, body_h = self._measure(body, 17, width=text_width)
        _, meta_h = self._measure(meta, 14, width=text_width)
        body_h = body_h if body else 0
        meta_h = meta_h if meta else 0
        text_h = title_h + (8 + body_h if body else 0) + (11 + meta_h if meta else 0)
        has_progress = isinstance(row.get("progress"), (float, int))
        if has_progress:
            text_h += 16
        button_width = side_width if right_buttons else text_width
        placements, buttons_h = self._button_placements(buttons, button_width)
        row_h = max(106, padding * 2 + text_h)
        if right_buttons:
            row_h = max(row_h, buttons_h + 40)
        elif buttons:
            row_h += 15 + buttons_h
        group = parent.attachNewNode(f"terminal-record-{index}")
        group.setPos(x, 0, -y)
        self._gradient(group, 0, 0, width, row_h,
                       (.066, .102, .117, .86), (.044, .078, .095, .68), horizontal=True)
        self._line(group, [(0, 0), (width, 0)], (.44, .61, .64, .12))
        self._line(group, [(text_x, row_h - 1), (width - padding, row_h - 1)],
                   (.39, .55, .58, .15))
        self._rect(group, 0, 23, 2, 28, (*accent[:3], .65))
        if icon == "planet":
            self._planet_glyph(group, 45, 48, 26, accent, index)
            self._arc(group, 45, 48, 34, .14, math.pi * 1.78, (*accent[:3], .28), .9, .38)
        else:
            self._rect(group, 21, 22, 48, 48, (.025, .052, .065, .69))
            self._icon(group, icon, 45, 46, 30, accent)
        self._label(group, f"{index + 1:02d}", 45, 91, 13, DIM,
                    align=TextNode.ACenter, face="instrument")
        text_y = padding + 18
        self._label(group, title or "Record", text_x, text_y, 22, CREAM,
                    bold=True, width=title_width)
        next_y = text_y + title_h
        if body:
            self._label(group, body, text_x, next_y + 4, 17, MUTED, width=text_width)
            next_y += body_h + 8
        if meta:
            self._label(group, meta, text_x, next_y + 6, 14, accent, width=text_width)
            next_y += meta_h + 11
        if has_progress:
            bar = _Bar(self, group, text_x, next_y + 5, min(330, text_width), 3, accent)
            bar.set(row["progress"])
        if row.get("tag") and not right_buttons:
            tag = self._short(_as_text(row["tag"]).upper(), 135, 12)
            self._label(group, tag, width - padding, text_y - 2, 12, accent,
                        align=TextNode.ARight)
        if right_buttons:
            button_x = width - padding - side_width
            button_y = (row_h - buttons_h) / 2
        else:
            button_x = text_x
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
        self._modal_shade(self.menu, .36)
        w = self.width
        compact = w < 1350
        left = 68 if compact else 91
        self._gradient(self.menu, 0, 0, w * .72, 900,
                       (.008, .020, .030, .96), (.008, .020, .030, .02), horizontal=True)
        self._gradient(self.menu, 0, 0, w, 175,
                       (.006, .017, .028, .86), (.006, .017, .028, 0))
        self._gradient(self.menu, 0, 688, w, 212,
                       (.006, .017, .025, 0), (.006, .017, .025, .94))
        self._emblem(self.menu, left + 19, 54, 35)
        self._label(self.menu, "A S T E R I O N", left + 57, 51, 17, CREAM, bold=True)
        self._label(self.menu, "INDEPENDENT EXPLORATION PROGRAM", left + 58, 72, 10.5, MUTED)
        self._label(self.menu, "CELESTIAL", w - left, 49, 21, CREAM,
                    align=TextNode.ARight, face="instrument")
        self._label(self.menu, f"GRAPHICS EDITION  /  v{__version__}", w - left, 72,
                    10.5, CYAN, align=TextNode.ARight)
        self._line(self.menu, [(left, 104), (w - left, 104)], LINE)

        self._rect(self.menu, left, 188, 26, 2, AMBER)
        self._label(self.menu, "A JOURNEY BEYOND THE CHART", left + 40, 194, 13, AMBER, bold=True)
        self._label(self.menu, "ASTERION", left - 4, 298, 87 if compact else 101,
                    CREAM, face="display")
        self._label(self.menu, "E X P E D I T I O N", left, 342, 24 if compact else 27, CYAN)
        self._label(self.menu, "Every horizon is a beginning.", left, 402, 23, CREAM)
        self._label(self.menu, "Chart distant worlds. Follow ancient signals.\nMake a home among the stars.",
                    left, 438, 18, MUTED, width=462 if not compact else 396)
        button_width = 354 if compact else 405
        primary = {"label": "CONTINUE EXPEDITION" if self._has_save else "BEGIN EXPEDITION",
                   "action": "continue" if self._has_save else "new_game"}
        self._button(self.menu, primary, left, 507, button_width, 59, primary=True, align_left=True)
        next_y = 580
        if self._has_save:
            self._button(self.menu, {"label": "NEW EXPEDITION", "action": "new_game"},
                         left, next_y, button_width, 45, align_left=True)
            next_y += 57
        self._button(self.menu, {"label": "FIELD GUIDE", "action": "help"},
                     left, next_y, (button_width - 10) / 2, 43, icon="journal")
        self._button(self.menu, {"label": "SETTINGS", "action": "settings"},
                     left + (button_width + 10) / 2, next_y,
                     (button_width - 10) / 2, 43, icon="settings")
        self._button(self.menu, {"label": "QUIT", "action": "quit"},
                     left, next_y + 58, 112, 36)

        chart_x = w * (.753 if compact else .745)
        chart_y = 410
        radius = 125 if compact else 167
        chart = self._group(self.menu, "title-celestial-atlas", 151)
        self._label(chart, "THE ASTERION REACH", chart_x, 171, 13, CYAN, bold=True,
                    align=TextNode.ACenter)
        self._label(chart, "EXPLORATION ATLAS   /   VOL. 01", chart_x, 194, 10.5, MUTED,
                    align=TextNode.ACenter)
        # Sparse chart points and a meridian scale frame the shaded globe.
        for index in range(37):
            phase = index * 2.3999632297
            distance = radius * (1.10 + ((index * 13) % 19) / 24)
            px = chart_x + math.cos(phase) * distance
            py = chart_y + math.sin(phase) * distance * .86
            alpha = .22 + (index % 4) * .09
            self._rect(chart, px, py, 1.1 if index % 3 else 1.6,
                       1.1 if index % 3 else 1.6, (.7, .85, .85, alpha))
        self._arc(chart, chart_x, chart_y, radius * 1.29,
                  .09, math.tau - .3, (.5, .75, .77, .22), .9)
        self._arc(chart, chart_x, chart_y, radius * 1.37,
                  math.pi * 1.04, math.pi * .70, (.63, .82, .83, .35), 1)
        for index in range(72):
            angle = index / 72 * math.tau
            outer = radius * 1.39
            inner = outer - (8 if index % 6 == 0 else 3)
            self._line(chart, [(chart_x + math.cos(angle) * inner,
                                chart_y + math.sin(angle) * inner),
                               (chart_x + math.cos(angle) * outer,
                                chart_y + math.sin(angle) * outer)],
                       (.5, .73, .75, .33 if index % 6 == 0 else .15), .8)
        self._planet_glyph(chart, chart_x, chart_y, radius, (.53, .79, .76, 1), 4, detailed=True)
        # An inclined orbital track gives the atlas depth without covering its type.
        orbit_points = []
        for step in range(145):
            angle = step / 144 * math.tau
            px, py = math.cos(angle) * radius * 1.74, math.sin(angle) * radius * .39
            rotation = -.34
            orbit_points.append((chart_x + px * math.cos(rotation) - py * math.sin(rotation),
                                 chart_y + px * math.sin(rotation) + py * math.cos(rotation)))
        self._line(chart, orbit_points, (.66, .83, .81, .39), 1.0)
        angle = 5.45
        px, py = math.cos(angle) * radius * 1.74, math.sin(angle) * radius * .39
        pin_x = chart_x + px * math.cos(-.34) - py * math.sin(-.34)
        pin_y = chart_y + px * math.sin(-.34) + py * math.cos(-.34)
        self._arc(chart, pin_x, pin_y, 7, color=AMBER, thickness=1.2)
        self._rect(chart, pin_x - 2, pin_y - 2, 4, 4, CREAM)
        self._line(chart, [(pin_x + 8, pin_y - 8), (pin_x + 29, pin_y - 28),
                           (pin_x + 68, pin_y - 28)], (.91, .72, .47, .52))
        self._label(chart, "01", pin_x + 33, pin_y - 36, 16, AMBER, face="instrument")
        self._line(chart, [(chart_x - 17, chart_y + radius * 1.58),
                           (chart_x + 17, chart_y + radius * 1.58)], AMBER, 1.0)
        metric_y = 726 if not compact else 675
        self._label(chart, "24", chart_x - 103, metric_y, 43, CREAM,
                    align=TextNode.ACenter, face="instrument")
        self._label(chart, "96", chart_x + 103, metric_y, 43, CREAM,
                    align=TextNode.ACenter, face="instrument")
        self._label(chart, "STAR SYSTEMS", chart_x - 103, metric_y + 25, 11, CYAN,
                    align=TextNode.ACenter)
        self._label(chart, "WORLDS TO CHART", chart_x + 103, metric_y + 25, 11, CYAN,
                    align=TextNode.ACenter)
        self._line(chart, [(chart_x, metric_y - 29), (chart_x, metric_y + 22)], LINE)
        self._line(self.menu, [(left, 810), (w - left, 810)], LINE)
        self._label(self.menu, "EXPLORE   /   DISCOVER   /   BUILD   /   BELONG", left, 842, 12, MUTED)
        self._label(self.menu, "YOUR NEXT HORIZON AWAITS", w - left, 842, 12,
                    CYAN, align=TextNode.ARight)

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
