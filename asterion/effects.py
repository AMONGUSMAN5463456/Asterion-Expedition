"""Original camera-mounted exploration equipment, built from coloured geometry.

No textures, shaders, lights, global tasks, or gameplay dependencies are used.
The application's camera owns every node; :meth:`destroy` removes them together.
"""
from __future__ import annotations

import math

from panda3d.core import ColorAttrib, TransparencyAttrib

from .geometry import Mesh


_GRAPHITE = (0.042, 0.063, 0.087)
_FRAME = (0.115, 0.175, 0.205)
_ALLOY = (0.35, 0.44, 0.46)
_IVORY = (0.68, 0.73, 0.69)
_CYAN = (0.19, 0.91, 0.95)
_AMBER = (1.0, 0.55, 0.19)
_SCREEN = (0.025, 0.14, 0.19)
_SIGNAL = (0.94, 0.97, 0.97)


def _finite(value, default=0.0, low=0.0, high=1.0):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(low, min(high, value))


class PlayerEffects:
    """Survey tool on foot, a light cockpit frame in surface/orbital flight.

    ``thrust`` is a normalized motion/intensity hint (0..1); it only controls
    subtle equipment sway and indicator animation. ``mining`` adds tool recoil,
    while ``scanner`` illuminates its separate rotating optical crown. Optional
    ``camera_motion`` scales equipment motion only; the controller exclusively
    owns camera transforms and field of view.
    """

    def __init__(self, app):
        self.root = app.camera.attachNewNode("player-equipment")
        self.root.setLightOff(1)
        self.root.setFogOff(1)
        self.root.setShaderOff(1)
        # These are deliberately prelit vertex materials. An inherited world
        # material or flat ColorAttrib must never turn the equipment white.
        self.root.setMaterialOff(1)
        self.root.setAttrib(ColorAttrib.makeVertex(), 1)
        self.root.setDepthTest(True)
        self.root.setDepthWrite(True)
        self.tool = self.root.attachNewNode("survey-tool")
        self.cockpit = self.root.attachNewNode("cockpit")
        self._time = 0.0
        self._motion = 0.0
        self._mining = 0.0
        self._scan = 0.0
        self._boost = 0.0
        self._contact = 0.0
        self._emission_visible = False
        self._mode = None
        self._destroyed = False
        # Last applied color/brightness values. setColorScale dirties Panda3D
        # render state even when the value is unchanged, so per-frame updates
        # below skip the call unless the value moved past a visible epsilon.
        self._last_pulse = -1.0
        self._last_crown = -1.0
        self._last_scan_ring = -1.0
        self._last_charge = -1.0
        self._last_brightness = -1.0
        self._last_indicator = None
        self._last_thrust = None
        self._make_tool()
        self.tool.setScale(0.54)
        self._make_cockpit()
        self.tool.hide()
        self.cockpit.hide()

    def _make_tool(self):
        body = Mesh()
        # Short, twin-pronged survey head, with a recessed optical core.
        body.box((0, -0.005, 0), (0.29, 0.39, 0.22), _GRAPHITE)
        body.box((0.015, 0.015, 0.088), (0.285, 0.34, 0.085), _IVORY)
        body.box((0.01, -0.005, -0.096), (0.28, 0.38, 0.07), _ALLOY)
        body.box((0.15, -0.015, -0.005), (0.065, 0.28, 0.17), _IVORY)
        body.box((-0.155, 0.015, 0), (0.04, 0.31, 0.17), _FRAME)
        body.tube((0, 0.13, 0.022), (0, 0.40, 0.022), 0.081, _FRAME, 12)
        body.tube((0, 0.36, 0.022), (0, 0.47, 0.022), 0.107, _ALLOY, 12,
                  end_radius=0.094)
        for side in (-1, 1):
            body.box((side * 0.11, 0.27, 0.071), (0.056, 0.40, 0.058), _IVORY)
            body.box((side * 0.11, 0.475, 0.071), (0.057, 0.026, 0.060), _AMBER)
            body.box((side * 0.108, 0.185, -0.067), (0.045, 0.23, 0.04), _FRAME)
        # Amber carrying brace and ribbed, compact grip keep the silhouette
        # industrial rather than turning the device into a large weapon.
        body.tube((0.12, -0.12, -0.055), (0.14, -0.12, -0.31), 0.062,
                  _GRAPHITE, 6, end_radius=0.049)
        for i in range(5):
            body.box((0.14, -0.13, -0.12 - i * 0.037), (0.11, 0.085, 0.016), _FRAME)
        body.tube((-0.10, -0.16, 0.12), (-0.10, 0.095, 0.19), 0.018, _AMBER, 6)
        body.tube((-0.10, 0.095, 0.19), (0.10, 0.095, 0.19), 0.018, _AMBER, 6)
        body.tube((0.10, 0.095, 0.19), (0.10, -0.16, 0.12), 0.018, _AMBER, 6)
        # An exposed side lens, fasteners, cooling slots and cartridge seams.
        body.tube((-0.18, 0.007, 0), (-0.202, 0.007, 0), 0.067, _ALLOY, 12)
        for y in (-0.12, -0.06, 0.0, 0.06):
            body.box((0.014, y, 0.133), (0.13, 0.013, 0.009), _GRAPHITE)
        for z in (-0.061, 0.061):
            for y in (-0.12, 0.12):
                body.tube((-0.176, y, z), (-0.181, y, z), 0.010, _ALLOY, 6)
        body.node("survey-tool-body", self.tool)

        optics = Mesh()
        optics.tube((-0.204, 0.007, 0), (-0.207, 0.007, 0), 0.045, _CYAN, 16)
        optics.tube((0, 0.395, 0.022), (0, 0.471, 0.022), 0.070, _CYAN, 16)
        optics.box((-0.021, -0.207, 0.036), (0.17, 0.009, 0.084), _SCREEN)
        for i in range(6):
            optics.box((-0.083 + i * 0.024, -0.213, 0.012 + (i % 3) * 0.007),
                       (0.012, 0.005, 0.016 + (i % 3) * 0.014), _CYAN)
        optics.box((0.11, -0.214, 0.04), (0.018, 0.006, 0.032), _AMBER)
        optics.box((-0.177, 0.01, -0.075), (0.006, 0.20, 0.010), _CYAN)
        self.optics = optics.node("survey-optics", self.tool, unlit=True)

        self.crown = self.tool.attachNewNode("survey-optical-crown")
        self.crown.setPos(0, 0.475, 0.022)
        crown = Mesh()
        crown.ring((0, 0, 0), 0.080, 0.091, _CYAN, 24, tilt=90)
        for i in range(4):
            angle = i * math.tau / 4
            x, z = math.cos(angle) * 0.094, math.sin(angle) * 0.094
            crown.box((x, 0, z), (0.016, 0.014, 0.016), _AMBER)
        crown.node("survey-crown-ring", self.crown, two_sided=True, unlit=True)
        self.scan_ring = self.tool.attachNewNode("survey-scan-ring")
        self.scan_ring.setPos(0, 0.475, 0.022)
        scan = Mesh()
        scan.ring((0, 0, 0), 0.112, 0.120, _CYAN, 32, tilt=90)
        for i in range(2):
            angle = i * math.pi + math.pi / 4
            x, z = math.cos(angle) * 0.116, math.sin(angle) * 0.116
            scan.box((x, 0, z), (0.018, 0.012, 0.018), _AMBER)
        scan.node("survey-scan-holo", self.scan_ring, two_sided=True, unlit=True)
        # Floating sight reticle hovering just past the emitter tip.
        sight = Mesh()
        half, w, y = 0.030, 0.007, 0.62
        sight.box((0, y, -half), (half * 2 + w, 0.004, w), _CYAN)
        sight.box((0, y, half), (half * 2 + w, 0.004, w), _CYAN)
        sight.box((-half, y, 0), (w, 0.004, half * 2 + w), _CYAN)
        sight.box((half, y, 0), (w, 0.004, half * 2 + w), _CYAN)
        sight.box((0, y, 0), (0.010, 0.004, 0.010), _AMBER)
        self._sight = sight.node("survey-sight-reticle", self.tool, two_sided=True, unlit=True)
        # Charge-status light strip on the camera-facing rear face.
        strip = Mesh()
        strip.box((0.10, -0.201, -0.045), (0.028, 0.008, 0.024), (0.25, 1.0, 0.45))
        strip.box((0.10, -0.201, 0.0), (0.028, 0.008, 0.024), _AMBER)
        strip.box((0.10, -0.201, 0.045), (0.028, 0.008, 0.024), (1.0, 0.30, 0.22))
        self._charge_strip = strip.node("survey-charge-strip", self.tool, unlit=True)

        glow = Mesh()
        glow.sphere((0, 0, 0), (0.065, 0.025, 0.065), (0.53, 1.0, 1.0, 0.35),
                    segments=12, rings=5)
        self.emission = glow.node("survey-active-emission", self.tool, unlit=True)
        self.emission.setPos(0, 0.49, 0.022)
        self.emission.setTransparency(TransparencyAttrib.MAlpha)
        self.emission.setDepthWrite(False)
        self.emission.hide()

    def _make_cockpit(self):
        shell = Mesh()
        instruments = Mesh()
        indicators = Mesh()
        self.thrust_segments = []
        # Separated instrument shoulders leave a generous view between them.
        for side in (-1, 1):
            x = side * 0.75
            # Chamfered leading corners and a sloped, narrow alloy lip give
            # each console depth without putting a bright slab on the horizon.
            front = [(x - .31, 1.28, -.66), (x + .31, 1.28, -.66),
                     (x + .31, 1.28, -.515), (x + .274, 1.28, -.484),
                     (x - .274, 1.28, -.484), (x - .31, 1.28, -.515)]
            back = [(px, 1.55, pz + .035) for px, _, pz in front]
            for i in range(len(front)):
                j = (i + 1) % len(front)
                shell.tri((x, 1.28, -.58), front[i], front[j], _GRAPHITE)
                tone = _ALLOY if i in (2, 3, 4) else _FRAME
                shell.quad(front[j], front[i], back[i], back[j], tone)
            shell.box((x, 1.274, -.559), (.53, .012, .108), _FRAME)
            shell.box((x - side * .025, 1.264, -.552), (.385, .008, .069), _SCREEN)
            shell.box((x + side * .237, 1.261, -.552), (.043, .014, .08), _GRAPHITE)
            shell.box((x - side * .284, 1.268, -.560), (.012, .012, .098), _AMBER)
            for i in range(8):
                bx = x - .163 + i * .043
                height = .011 + i * .0035
                bar = Mesh()
                bar.box((bx, 1.257, -.571 + height * .5),
                        (.019, .005, height), _SIGNAL)
                node = bar.node("cockpit-thrust-segment", self.cockpit, unlit=True)
                self.thrust_segments.append((i, node))
            for i in range(3):
                indicators.box((x + side * .237, 1.25, -.531 - i * .020),
                               (.021, .008, .006), _SIGNAL)
            instruments.box((x, 1.257, -.598), (.46, .006, .004), _CYAN)
            for bx in (x - .25, x + .25):
                shell.tube((bx, 1.263, -.604), (bx, 1.256, -.604),
                           .007, _ALLOY, 6)
            # Small vents and layered rim seams read as metal at close range.
            for i in range(5):
                shell.box((x - .10 + i * .05, 1.41, -.465),
                          (.027, .088, .005), _GRAPHITE)
            # Thin canopy edges live at the edges and top of the viewport.
            foot = (side * 1.055, 1.42, -0.52)
            elbow = (side * 1.18, 1.9, 0.53)
            roof = (side * 0.80, 2.30, 0.99)
            shell.tube(foot, elbow, 0.023, _FRAME, 6)
            shell.tube(elbow, roof, 0.026, _FRAME, 6)
            shell.tube((side * 1.046, 1.421, -0.51),
                       (side * 1.169, 1.902, 0.52), 0.004, _ALLOY, 4)
            shell.tube((side * 0.98, 1.47, -0.47),
                       (side * 0.32, 1.45, -0.69), 0.026, _FRAME, 6)
            indicators.tube((side * 1.048, 1.415, -0.50),
                            (side * 1.067, 1.495, -0.33), 0.004, _SIGNAL, 4)
        shell.tube((-0.80, 2.30, 0.99), (0.80, 2.30, 0.99), 0.022, _FRAME, 6)
        shell.tube((-0.35, 1.52, -0.70), (0.35, 1.52, -0.70), 0.025, _FRAME, 6)
        for side in (-1, 1):
            shell.box((side * 0.62, 2.02, 0.80), (0.035, 0.44, 0.035), _GRAPHITE)
        # Faint holographic HUD ladder floating in the console gap.
        for sx in (-0.09, 0.09):
            instruments.box((sx, 1.70, -0.50), (0.008, 0.005, 0.17), _CYAN)
        for rz in (-0.555, -0.50, -0.445):
            instruments.box((0, 1.70, rz), (0.17, 0.005, 0.008), _CYAN)
        # Warning-light dots share the indicators node update() already drives.
        indicators.box((-0.06, 1.52, -0.66), (0.030, 0.008, 0.016), _AMBER)
        indicators.box((0.06, 1.52, -0.66), (0.030, 0.008, 0.016), (1.0, 0.30, 0.22))
        shell.node("cockpit-shell", self.cockpit)
        self.instruments = instruments.node("cockpit-instruments", self.cockpit, unlit=True)
        self.indicators = indicators.node("cockpit-indicators", self.cockpit, unlit=True)

        glass = Mesh()
        for side in (-1, 1):
            glass.tri((side * 1.075, 1.44, -0.45), (side * 1.19, 1.91, 0.52),
                      (side * 1.43, 1.70, 0.24), (0.21, 0.59, 0.66, 0.075))
        glass_node = glass.node("cockpit-side-glass", self.cockpit, two_sided=True, unlit=True)
        glass_node.setTransparency(TransparencyAttrib.MAlpha)
        glass_node.setDepthWrite(False)

        # A distinct slim bow is visible below the horizon through the console
        # gap. It belongs to the camera model, not to world collision geometry.
        nose = Mesh()
        left, right, tip = (-0.30, 2.05, -0.89), (0.30, 2.05, -0.89), (0, 4.35, -1.35)
        nose.tri(left, right, tip, _ALLOY)
        nose.tri(left, tip, (0, 4.35, -1.44), _ALLOY)
        nose.tri(right, (0, 4.35, -1.44), tip, _FRAME)
        nose.quad((-0.03, 2.11, -0.895), (0.03, 2.11, -0.895),
                  (0.012, 4.12, -1.297), (-0.012, 4.12, -1.297), _AMBER)
        for side in (-1, 1):
            nose.tube((side * 0.26, 2.15, -0.905), (side * 0.095, 3.60, -1.195),
                      0.009, _FRAME, 5)
            nose.tube((side * 0.247, 2.19, -0.903), (side * 0.213, 2.51, -0.967),
                      0.009, _CYAN, 4)
        nose.node("ship-bow", self.cockpit, two_sided=True)

    def update(self, dt, mode, mining=False, thrust=0, scanner=False,
               camera_motion=.35, boosting=False, braking=False,
               collision_feedback=0):
        if self._destroyed:
            return
        dt = _finite(dt, high=0.25)
        self._time = (self._time + dt) % 3600.0
        blend = 1.0 - math.exp(-10.0 * dt)
        self._motion += (_finite(thrust) - self._motion) * blend
        self._mining += (float(bool(mining)) - self._mining) * blend
        self._scan += (float(bool(scanner)) - self._scan) * blend
        self._boost += (float(bool(boosting)) - self._boost) * blend
        self._contact = max(_finite(collision_feedback), self._contact * math.exp(-9 * dt))
        motion = _finite(camera_motion, default=.35)
        if mode != self._mode:
            self.tool.hide()
            self.cockpit.hide()
            if mode == "surface":
                self.tool.show()
            elif mode in ("flight", "orbit"):
                self.cockpit.show()
            self._mode = mode
            self._emission_visible = False
            self.emission.hide()
        t = self._time
        sin = math.sin
        if mode == "surface":
            mining = self._mining
            recoil = max(0.0, sin(t * 27.0)) ** 3 * mining
            bob = sin(t * 8.5) * .008 * self._motion * motion
            # The compact tool occupies the lower-right edge. Mining raises it
            # a little to acknowledge input while keeping the target clear.
            self.tool.setPos(.350 + sin(t * 1.6) * .002 * motion + bob * .4,
                             1.02 - recoil * .013 * motion,
                             -.345 + sin(t * 2.0) * .002 * motion + bob
                             + mining * .014 - recoil * .004 * motion
                             + sin(t * 2.3) * .004 * motion)
            self.tool.setHpr(10.0 + sin(t * 1.3) * .18 * motion,
                             6.0 + recoil * 1.2 * motion, -4.0 + bob * 45.0)
            scan = self._scan
            pulse = 0.82 + scan * 0.12 + mining * (0.09 + 0.06 * sin(t * 16))
            if abs(pulse - self._last_pulse) >= 0.002:
                self.optics.setColorScale(pulse, pulse, pulse, 1)
                self._last_pulse = pulse
            self.crown.setR(t * (12.0 + 100.0 * scan))
            if abs(scan - self._last_crown) >= 0.002:
                self.crown.setColorScale(0.78 + scan * 0.22, 1, 1, 1)
                self._last_crown = scan
            self.scan_ring.setR(-t * (20.0 + 60.0 * scan))
            if abs(scan - self._last_scan_ring) >= 0.002:
                self.scan_ring.setColorScale(0.72 + scan * 0.28, 1, 1, 1)
                self._last_scan_ring = scan
            charge = 0.75 + 0.25 * sin(t * 5.0 + 1.0)
            if abs(charge - self._last_charge) >= 0.002:
                self._charge_strip.setColorScale(charge, charge, charge, 1)
                self._last_charge = charge
            if mining > 0.03:
                if not self._emission_visible:
                    self.emission.show()
                    self._emission_visible = True
                self.emission.setScale(0.7 + recoil * 0.8)
                self.emission.setColorScale(0.8 + 0.5 * mining, 1, 1, mining)
            elif self._emission_visible:
                self.emission.hide()
                self._emission_visible = False
        elif mode in ("flight", "orbit"):
            intensity = self._motion * (.45 + self._boost * .55)
            self.cockpit.setPos(sin(t * 29.0) * .0006 * intensity * motion, 0,
                                sin(t * 37.0) * .0008 * intensity * motion)
            brightness = .92 + .04 * self._motion
            if abs(brightness - self._last_brightness) >= 0.002:
                self.instruments.setColorScale(brightness, brightness, brightness, 1)
                self._last_brightness = brightness
            contact = self._contact
            indicator = _AMBER if braking or contact > .08 else _CYAN
            if indicator != self._last_indicator:
                self.indicators.setColorScale(indicator[0], indicator[1], indicator[2], 1)
                self._last_indicator = indicator
            boost = self._boost > .3 or braking
            # Thrust bars are binary per segment; the visible state is fully
            # described by the lit-segment count and the amber/cyan flag.
            # Count preserves the original `thresh > index` boundary exactly.
            _thresh = self._motion * 8
            lit = 0
            for _i in range(8):
                if _thresh > _i:
                    lit += 1
                else:
                    break
            thrust_key = (lit, bool(boost))
            if thrust_key != self._last_thrust:
                amber = _AMBER
                cyan = _CYAN
                color = amber if boost else cyan
                for index, node in self.thrust_segments:
                    strength = 1.0 if lit > index else .12
                    node.setColorScale(color[0] * strength, color[1] * strength, color[2] * strength, 1)
                self._last_thrust = thrust_key

    def destroy(self):
        if not self._destroyed:
            self.root.removeNode()
            self._destroyed = True
