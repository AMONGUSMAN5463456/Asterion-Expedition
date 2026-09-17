"""Bounded, shader-free screen-edge atmosphere effects for continuous flight.

The scene has no transition timer or camera transform ownership. All intensity
comes from the nearest physical atmosphere; the middle of the view stays clear.
Meshes are built once and are also supported by Panda's software renderer.
"""
from __future__ import annotations

import math

from panda3d.core import ColorAttrib, TransparencyAttrib

from .geometry import Mesh


def _finite(value, default=0.0, low=0.0, high=1.0):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(low, min(high, value))


def atmosphere_envelope(data, mode="flight"):
    """Return finite effects strengths; outbound speed can never create heat.

    ``entry`` and ``exit`` already include the field's continuous density and
    speed envelopes. Sign gating independently keeps malformed or stale heat
    hints from producing a fiery ascent. There are no altitude trigger gates.
    """
    if not isinstance(data, dict) or mode not in ("flight", "orbit"):
        return dict(haze=0.0, plasma=0.0, cloud=0.0, exit=0.0)
    density = _finite(data.get("density", 0))
    space = _finite(data.get("space_blend", 1 - density))
    radial = _finite(data.get("radial_speed", 0), low=-100000, high=100000)
    inward = _finite(-radial / 95.0)
    outward = _finite(radial / 75.0)
    heat = _finite(data.get("heat", 0))
    entry = _finite(data.get("entry", heat))
    leaving = _finite(data.get("exit", 0))
    # Clouds remain perceptible while hovering; moving through the band gives
    # stronger condensation. Vacuum suppresses invalid nonzero cloud hints.
    cloud = _finite(data.get("cloud", 0)) * (0.45 + 0.55 * _finite(abs(radial) / 90))
    present = _finite(density * 30)
    return {
        "haze": density * (0.34 + 0.18 * (1 - space)),
        "plasma": math.sqrt(heat * entry) * inward * present,
        "cloud": cloud * present,
        "exit": leaving * outward * present,
    }


def _edge(angle, radius):
    """Point on a rounded rectangular ring in normalized screen space."""
    x, z = math.cos(angle), math.sin(angle)
    divisor = max(abs(x), abs(z))
    return (radius * x / divisor, 0, radius * z / divisor)


def _strip(mesh, a, b, c, d, ca, cb, cc, cd):
    mesh.tri(a, b, c, ca, colors=(ca, cb, cc))
    mesh.tri(a, c, d, ca, colors=(ca, cc, cd))


class AtmosphereEffects:
    """Edge plasma, cloud wisps and a cool, outward-moving exit signature.

    ``dt=0`` freezes the complete presentation, including envelope smoothing.
    ``camera_motion=0`` freezes all decorative movement while allowing changes
    in physical atmosphere to alter colour/intensity. No task is registered.
    """

    def __init__(self, app):
        parent = getattr(app, "render2d", None)
        self._screen_space = parent is not None
        if parent is None:
            parent = app.camera
        self.root = parent.attachNewNode("continuous-atmosphere-effects")
        self.root.setLightOff(1)
        self.root.setFogOff(1)
        self.root.setShaderOff(1)
        self.root.setMaterialOff(1)
        self.root.setAttrib(ColorAttrib.makeVertex(), 1)
        self.root.setTransparency(TransparencyAttrib.MAlpha)
        self.root.setDepthTest(False)
        self.root.setDepthWrite(False)
        self.root.setTwoSided(True)
        self.root.setBin("fixed", 8)
        if not self._screen_space:
            # Lightweight scene hosts have no render2d. Real ShowBase instances
            # use the screen-space path above, which is independent of FOV.
            self.root.setY(1.2)
            self.root.setScale(.92, 1, .55)
        self.haze = self.root.attachNewNode("atmospheric-edge-haze")
        self.plasma = self.root.attachNewNode("inbound-edge-plasma")
        self.clouds = self.root.attachNewNode("cloud-condensation-wisps")
        self.exit = self.root.attachNewNode("outbound-blue-wash")
        self._time = 0.0
        self._destroyed = False
        self._levels = dict(haze=0.0, plasma=0.0, cloud=0.0, exit=0.0)
        self._plumes = []
        self._wisps = []
        self._streaks = []
        self._ring(self.haze, "air-haze-ring", (0.34, 0.63, 0.79), .73, .32)
        self._ring(self.plasma, "ionized-entry-ring", (1.0, .27, .065), .78, .53)
        self._ring(self.exit, "cool-exit-ring", (.18, .59, 1.0), .71, .37)
        self._build_plasma()
        self._build_wisps()
        self._build_exit()
        self.root.hide()

    @staticmethod
    def _ring(parent, name, color, inner, alpha):
        mesh = Mesh()
        for index in range(64):
            a, b = index * math.tau / 64, (index + 1) * math.tau / 64
            ripple_a = .018 * math.sin(a * 7 + .9)
            ripple_b = .018 * math.sin(b * 7 + .9)
            for lo, hi, alo, ahi in ((inner, .93, 0, alpha * .40),
                                     (.93, 1.06, alpha * .40, alpha)):
                _strip(mesh, _edge(a, lo + ripple_a), _edge(b, lo + ripple_b),
                       _edge(b, hi), _edge(a, hi), (*color, alo), (*color, alo),
                       (*color, ahi), (*color, ahi))
        mesh.node(name, parent, two_sided=True, unlit=True)

    def _build_plasma(self):
        for index in range(20):
            angle = math.tau * (index + .23) / 20
            mesh = Mesh()
            # Curved filaments widen into the edge, with a violet ion fringe
            # and a pale hot core. Per-vertex alpha gives soft, tapered ends.
            for segment in range(6):
                u, v = segment / 6, (segment + 1) / 6
                r0, r1 = .75 + u * .39, .75 + v * .39
                bend0 = .055 * math.sin(u * 4 + index * 1.7)
                bend1 = .055 * math.sin(v * 4 + index * 1.7)
                w0, w1 = .010 + u * .020, .010 + v * .020
                alpha0, alpha1 = .80 * math.sin(u * math.pi), .80 * math.sin(v * math.pi)
                color = (1.0, .57 + .12 * math.sin(index), .16)
                _strip(mesh, _edge(angle + bend0 - w0, r0),
                       _edge(angle + bend0 + w0, r0),
                       _edge(angle + bend1 + w1, r1),
                       _edge(angle + bend1 - w1, r1),
                       (*color, alpha0 * .08), (*color, alpha0),
                       (*color, alpha1), (.72, .28, 1.0, alpha1 * .08))
            self._plumes.append(mesh.node("entry-plasma-filament", self.plasma,
                                          two_sided=True, unlit=True))

    def _build_wisps(self):
        for index in range(12):
            mesh = Mesh()
            angle = math.tau * (index + .38) / 12
            for segment in range(8):
                u, v = segment / 8, (segment + 1) / 8
                a, b = angle + (u - .5) * .43, angle + (v - .5) * .43
                r0 = .84 + .035 * math.sin(u * 5 + index)
                r1 = .84 + .035 * math.sin(v * 5 + index)
                c0 = (.72, .87, .97, math.sin(u * math.pi) * .20)
                c1 = (.72, .87, .97, math.sin(v * math.pi) * .20)
                clear = (.72, .87, .97, 0)
                _strip(mesh, _edge(a, r0 - .065), _edge(b, r1 - .065),
                       _edge(b, r1), _edge(a, r0), clear, clear, c1, c0)
                _strip(mesh, _edge(a, r0), _edge(b, r1),
                       _edge(b, r1 + .11), _edge(a, r0 + .11), c0, c1, clear, clear)
            self._wisps.append(mesh.node("passing-cloud-wisp", self.clouds,
                                         two_sided=True, unlit=True))

    def _build_exit(self):
        for index in range(24):
            mesh = Mesh()
            angle = math.tau * (index + .41) / 24
            start = .65 + (index % 3) * .027
            end = start + .19 + (index % 4) * .023
            width = .0045 + (index % 3) * .0018
            tip = _edge(angle, end)
            middle = _edge(angle, start + .055)
            mesh.tri(_edge(angle - width, start), _edge(angle + width, start), tip,
                     (.39, .77, 1.0, .5),
                     colors=((.27, .64, 1.0, 0), (.27, .64, 1.0, 0),
                             (.53, .88, 1.0, .52)))
            mesh.tri(_edge(angle - width * .42, start + .055),
                     _edge(angle + width * .42, start + .055), tip,
                     (.8, .95, 1, .64))
            self._streaks.append(mesh.node("outward-exit-streak", self.exit,
                                           two_sided=True, unlit=True))

    def update(self, dt, data, mode="flight", camera_motion=.35):
        if self._destroyed:
            return
        dt = _finite(dt, high=.25)
        if dt <= 0:
            return
        motion = _finite(camera_motion, default=.35)
        self._time = (self._time + dt * motion) % 3600
        targets = atmosphere_envelope(data, mode)
        blend = 1 - math.exp(-7.0 * dt)
        for key, value in targets.items():
            self._levels[key] += (value - self._levels[key]) * blend
        if mode not in ("flight", "orbit"):
            self.root.hide()
            return
        if max(self._levels.values()) < .001:
            self.root.hide()
            return
        self.root.show()
        for key, node in (("haze", self.haze), ("plasma", self.plasma),
                          ("cloud", self.clouds), ("exit", self.exit)):
            node.setColorScale(1, 1, 1, self._levels[key])
            node.show() if self._levels[key] > .001 else node.hide()
        t = self._time
        for index, node in enumerate(self._plumes):
            node.setScale(1 + .017 * math.sin(t * 8 + index * 2.1))
            node.setR(.62 * math.sin(t * 6 + index))
            pulse = .76 + .24 * math.sin(t * 13 + index * 2.4) ** 2
            node.setColorScale(1, 1, 1, pulse)
        for index, node in enumerate(self._wisps):
            node.setScale(1.01 + .09 * math.sin(t * 1.8 + index * 2.3))
            node.setR(1.6 * math.sin(t * .9 + index))
        for index, node in enumerate(self._streaks):
            phase = (t * 1.45 + index * .61803398875) % 1
            node.setScale(.94 + .34 * phase)
            node.setColorScale(1, 1, 1, math.sin(phase * math.pi) ** 2)

    def destroy(self):
        if not self._destroyed:
            self.root.removeNode()
            self._destroyed = True

