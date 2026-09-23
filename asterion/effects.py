"""Crafted camera-mounted equipment with a prelit software fallback.

The application's camera owns every node; destroy removes them together.
"""
from __future__ import annotations

import math

from panda3d.core import BitMask32, ColorAttrib, ColorBlendAttrib, TextNode, TransparencyAttrib

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
    owns camera transforms and field of view. Optional ``quantum_phase`` and
    ``quantum_progress`` drive a retained charge halo and forward-flowing tunnel.
    The tunnel's travel cue never shakes or changes the camera, including when
    camera motion is disabled. No task, texture, or shader is required for it.
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
        self.root.hide(BitMask32.bit(1))
        if getattr(getattr(app, "visuals", None), "gpu", False):
            from .visual_pipeline import shader
            self.root.setShader(shader("equipment.vert", "equipment.frag"), 2)
        self.tool = self.root.attachNewNode("survey-tool")
        self.cockpit = self.root.attachNewNode("cockpit")
        self._time = 0.0
        self._motion = 0.0
        self._mining = 0.0
        self._scan = 0.0
        self._boost = 0.0
        self._contact = 0.0
        self._quantum_time = 0.0
        self._quantum_phase = "idle"
        self._mode = None
        self._destroyed = False
        self._make_tool()
        self.tool.setScale(0.54)
        self._make_cockpit()
        self._make_quantum()
        self.tool.hide()
        self.cockpit.hide()

    def _make_quantum(self):
        """Build a sparse light corridor in camera space, behind the canopy.

        Vertex alpha softens every ribbon on both renderers. Everything lives
        under the equipment owner and is retained for the whole session; no
        scene nodes or geometry are allocated during travel.
        """
        self.quantum = self.root.attachNewNode("quantum-drive-effects")
        self.quantum.setShaderOff(20)
        self.quantum.setLightOff(20)
        self.quantum.setMaterialOff(20)
        self.quantum.setAttrib(ColorAttrib.makeVertex(), 20)
        self.quantum.setTransparency(TransparencyAttrib.MAlpha)
        self.quantum.setAttrib(ColorBlendAttrib.make(
            ColorBlendAttrib.MAdd, ColorBlendAttrib.OIncomingAlpha,
            ColorBlendAttrib.OOne))
        self.quantum.setDepthWrite(False)
        self.quantum.setTwoSided(True)
        self.quantum.setBin("transparent", 5)
        self.quantum_charge = self.quantum.attachNewNode("quantum-charge-halo")
        self.quantum_tunnel = self.quantum.attachNewNode("quantum-travel-corridor")
        self._quantum_charge_segments = []
        self._quantum_streaks = []
        self._quantum_waves = []

        def point(angle, radius, depth):
            return (math.cos(angle) * radius, depth, math.sin(angle) * radius)

        # Small broken arcs sit outside the flight sight and fill clockwise as
        # the drive calibrates. This halo has no full-screen flash or occluder.
        for index in range(16):
            arc = Mesh()
            start = math.pi / 2 - (index + 1) * math.tau / 16
            for step in range(4):
                a = start + (.035 + step * .075)
                b = a + .075
                arc.quad(point(a, .292, 3.2), point(b, .292, 3.2),
                         point(b, .305, 3.2), point(a, .305, 3.2),
                         (.20, .83, 1.0, .68))
                arc.quad(point(a, .305, 3.2), point(b, .305, 3.2),
                         point(b, .329, 3.2), point(a, .329, 3.2),
                         (.10, .48, 1.0, .15),
                         colors=((.10, .48, 1.0, .15), (.10, .48, 1.0, .15),
                                 (.10, .48, 1.0, 0), (.10, .48, 1.0, 0)))
            self._quantum_charge_segments.append(
                arc.node("quantum-calibration-arc", self.quantum_charge, unlit=True))

        # Broad translucent filaments curve along the corridor; perspective
        # contracts their distant ends into a small, open vanishing point.
        filaments = Mesh()
        for index in range(12):
            base = index * math.tau / 12
            for step in range(22):
                u, v = step / 22, (step + 1) / 22
                y0, y1 = 3 + 142 * u, 3 + 142 * v
                r0, r1 = 5.6 + u * 3.4, 5.6 + v * 3.4
                a, b = base + u * .38, base + v * .38
                width = .032 + .018 * math.sin(index * 2.4) ** 2
                color = (.055, .30 + .16 * (index % 3), 1.0)
                alpha0 = .15 * math.sin(u * math.pi) ** .65
                alpha1 = .15 * math.sin(v * math.pi) ** .65
                for side in (-1, 1):
                    filaments.quad(point(a, r0, y0), point(b, r1, y1),
                                   point(b + side * width, r1, y1),
                                   point(a + side * width, r0, y0), (*color, alpha0),
                                   colors=((*color, alpha0), (*color, alpha1),
                                           (*color, 0), (*color, 0)))
        self._quantum_filaments = filaments.node(
            "quantum-blue-filaments", self.quantum_tunnel, unlit=True)

        # Each luminous streak has a narrow pale core and a wider blue wake.
        # Deterministic lanes avoid random flashing and visual discontinuities.
        for index in range(60):
            angle = index * 2.399963229728653
            radius = 4.4 + (index * .61803398875 % 1) * 5.2
            length = 5.0 + (index * .38196601125 % 1) * 13.0
            streak = Mesh()
            for width, color, strength in ((.018, (.10, .39, 1.0), .38),
                                           (.0038, (.49, .91, 1.0), .88)):
                for start, end, aa, ab in ((0, .20, 0, strength),
                                            (.20, 1, strength, 0)):
                    streak.quad(point(angle - width, radius, length * start),
                                point(angle + width, radius, length * start),
                                point(angle + width, radius, length * end),
                                point(angle - width, radius, length * end), (*color, aa),
                                colors=((*color, aa), (*color, aa),
                                        (*color, ab), (*color, ab)))
            node = streak.node("quantum-forward-streak", self.quantum_tunnel, unlit=True)
            self._quantum_streaks.append((node, (index * .61803398875) % 1,
                                          .66 + (index % 7) * .047))

        # Widely spaced, broken wave fronts give depth without making a solid
        # cylinder around the ship. Their tapered inner and outer edges glow.
        for index in range(4):
            wave = Mesh()
            for segment in range(72):
                if (segment + index * 7) % 18 > 13:
                    continue
                a, b = segment * math.tau / 72, (segment + 1) * math.tau / 72
                for inner, outer, aa, ab in ((6.05, 6.20, 0, .22),
                                             (6.20, 6.44, .22, 0)):
                    wave.quad(point(a, inner, 0), point(b, inner, 0),
                              point(b, outer, 0), point(a, outer, 0), (.18, .64, 1.0, aa),
                              colors=((.18, .64, 1.0, aa), (.18, .64, 1.0, aa),
                                      (.18, .64, 1.0, ab), (.18, .64, 1.0, ab)))
            node = wave.node("quantum-wave-front", self.quantum_tunnel, unlit=True)
            self._quantum_waves.append((node, index / 4))
        self.quantum.hide()

    def _update_quantum(self, dt, mode, phase, progress, motion):
        phase = phase if isinstance(phase, str) else "idle"
        active = mode in ("flight", "orbit") and phase in ("spooling", "ready", "transit")
        if not active:
            self.quantum.hide()
            self._quantum_phase = "idle"
            self._quantum_time = 0.0
            return
        if phase != self._quantum_phase:
            self._quantum_time = 0.0
        self._quantum_phase = phase
        self._quantum_time = (self._quantum_time + dt) % 3600.0
        t = self._quantum_time
        progress = _finite(progress)
        self.quantum.show()
        if phase != "transit":
            self.quantum_tunnel.hide()
            self.quantum_charge.show()
            charge = 1.0 if phase == "ready" else progress
            pulse = 1.0 if not motion else .94 + .06 * math.sin(t * 2.2) * motion
            self.quantum_charge.setColorScale(1, 1, 1, pulse * (.35 + .65 * charge))
            for index, node in enumerate(self._quantum_charge_segments):
                strength = 1.0 if charge * 16 >= index + 1 else .12
                node.setColorScale(1, 1, 1, strength)
            return
        self.quantum_charge.hide()
        self.quantum_tunnel.show()
        # A gradual entrance and eased terminal glow prevent a hard flash.
        envelope = min(1.0, .20 + t * 1.8) * (.62 + .38 * math.sin(progress * math.pi))
        self.quantum_tunnel.setColorScale(1, 1, 1, envelope)
        self._quantum_filaments.setR(math.sin(t * .25) * 2.0 * motion)
        for node, offset, rate in self._quantum_streaks:
            travel = (offset + t * rate) % 1.0
            node.setY(3 + (1 - travel) * 112)
            node.setColorScale(1, 1, 1, min(1, travel * 8, (1 - travel) * 10))
        for node, offset in self._quantum_waves:
            travel = (offset + t * .39) % 1.0
            node.setY(5 + (1 - travel) * 96)
            node.setR((offset * 70 + t * 2) * motion)
            node.setColorScale(1, 1, 1, min(1, travel * 5, (1 - travel) * 7))

    def _make_tool(self):
        body = Mesh()
        # Short, twin-pronged survey head, with a recessed optical core.
        body.bevel_box((0, -0.005, 0), (0.29, 0.39, 0.22), _GRAPHITE)
        body.bevel_box((0.015, 0.015, 0.088), (0.285, 0.34, 0.085), _IVORY)
        body.bevel_box((0.01, -0.005, -0.096), (0.28, 0.38, 0.07), _ALLOY)
        body.bevel_box((0.15, -0.015, -0.005), (0.065, 0.28, 0.17), _IVORY)
        body.bevel_box((-0.155, 0.015, 0), (0.04, 0.31, 0.17), _FRAME)
        body.tube((0, 0.13, 0.022), (0, 0.40, 0.022), 0.081, _FRAME, 12)
        body.tube((0, 0.36, 0.022), (0, 0.47, 0.022), 0.107, _ALLOY, 12,
                  end_radius=0.094)
        for side in (-1, 1):
            body.bevel_box((side * 0.11, 0.27, 0.071), (0.056, 0.40, 0.058), _IVORY)
            body.bevel_box((side * 0.11, 0.475, 0.071), (0.057, 0.026, 0.060), _AMBER)
            body.bevel_box((side * 0.108, 0.185, -0.067), (0.045, 0.23, 0.04), _FRAME)
        # Amber carrying brace and ribbed, compact grip keep the silhouette
        # industrial rather than turning the device into a large weapon.
        body.tube((0.12, -0.12, -0.055), (0.14, -0.12, -0.31), 0.062,
                  _GRAPHITE, 6, end_radius=0.049)
        for i in range(5):
            body.bevel_box((0.14, -0.13, -0.12 - i * 0.037), (0.11, 0.085, 0.016), _FRAME)
        body.tube((-0.10, -0.16, 0.12), (-0.10, 0.095, 0.19), 0.018, _AMBER, 6)
        body.tube((-0.10, 0.095, 0.19), (0.10, 0.095, 0.19), 0.018, _AMBER, 6)
        body.tube((0.10, 0.095, 0.19), (0.10, -0.16, 0.12), 0.018, _AMBER, 6)
        # An exposed side lens, fasteners, cooling slots and cartridge seams.
        body.tube((-0.18, 0.007, 0), (-0.202, 0.007, 0), 0.067, _ALLOY, 12)
        for y in (-0.12, -0.06, 0.0, 0.06):
            body.bevel_box((0.014, y, 0.133), (0.13, 0.013, 0.009), _GRAPHITE)
        for z in (-0.061, 0.061):
            for y in (-0.12, 0.12):
                body.tube((-0.176, y, z), (-0.181, y, z), 0.010, _ALLOY, 6)
        body.node("survey-tool-body", self.tool)

        # Layered anodised collars and small independent ceramic panels catch
        # light around the optical head; lettering gives it manufactured scale.
        detail = Mesh()
        for y, radius in ((.34,.097),(.405,.107),(.454,.096)):
            detail.tube((0,y,.022),(0,y+.010,.022),radius,_GRAPHITE,32,smooth=True)
        for side in (-1,1):
            detail.bevel_box((side*.154,.063,.02),(.009,.13,.088),_ALLOY,bevel=.003)
            for i in range(3):
                detail.bevel_box((side*.11,.28+i*.052,.107),(.035,.029,.010),_FRAME,bevel=.003)
        detail.node("survey-anodised-details",self.tool)
        label=TextNode("survey-equipment-serial")
        label.setText("AE / 07")
        label.setTextColor(.72,.86,.86,1)
        label.setAlign(TextNode.ACenter)
        label_node=self.tool.attachNewNode(label)
        label_node.setScale(.018)
        label_node.setPos(.016,-.219,-.04)
        label_node.setLightOff(10)
        label_node.setShaderOff(10)

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
            shell.bevel_box((x, 1.274, -.559), (.53, .012, .108), _FRAME)
            shell.bevel_box((x - side * .025, 1.264, -.552), (.385, .008, .069), _SCREEN)
            shell.bevel_box((x + side * .237, 1.261, -.552), (.043, .014, .08), _GRAPHITE)
            shell.bevel_box((x - side * .284, 1.268, -.560), (.012, .012, .098), _AMBER)
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
                shell.bevel_box((x - .10 + i * .05, 1.41, -.465),
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
        shell.node("cockpit-shell", self.cockpit)
        self.instruments = instruments.node("cockpit-instruments", self.cockpit, unlit=True)
        self.indicators = indicators.node("cockpit-indicators", self.cockpit, unlit=True)

        telemetry=Mesh()
        for side in (-1,1):
            x=side*.75
            # Recessed diagnostic ticks and rotary dials live below the flight
            # sightline; they remain visually useful with the HUD disabled.
            for i in range(20):
                height=.009 if i%5 else .019
                telemetry.box((x-.21+i*.022,1.250,-.515),(.002,.002,height),(.23,.57,.64))
            telemetry.ring((x+side*.235,1.245,-.555),.019,.022,_CYAN,32,tilt=90)
            for i in range(4):
                telemetry.box((x-.15+i*.075,1.249,-.621),(.04,.004,.006),_AMBER if i==0 else _FRAME)
        telemetry.node("cockpit-engraved-telemetry",self.cockpit,unlit=True)

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
               collision_feedback=0, quantum_phase="idle", quantum_progress=0):
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
        self._update_quantum(dt, mode, quantum_phase, quantum_progress, motion)
        if mode != self._mode:
            self.tool.hide()
            self.cockpit.hide()
            if mode == "surface":
                self.tool.show()
            elif mode in ("flight", "orbit"):
                self.cockpit.show()
            self._mode = mode
        t = self._time
        if mode == "surface":
            recoil = max(0.0, math.sin(t * 27.0)) ** 3 * self._mining
            bob = math.sin(t * 8.5) * .008 * self._motion * motion
            # The compact tool occupies the lower-right edge. Mining raises it
            # a little to acknowledge input while keeping the target clear.
            self.tool.setPos(.350 + math.sin(t * 1.6) * .002 * motion + bob * .4,
                             1.02 - recoil * .013 * motion,
                             -.345 + math.sin(t * 2.0) * .002 * motion + bob
                             + self._mining * .014 - recoil * .004 * motion)
            self.tool.setHpr(10.0 + math.sin(t * 1.3) * .18 * motion,
                             6.0 + recoil * 1.2 * motion, -4.0 + bob * 45.0)
            pulse = 0.82 + self._scan * 0.12 + self._mining * (0.09 + 0.06 * math.sin(t * 16))
            self.optics.setColorScale(pulse, pulse, pulse, 1)
            self.crown.setR(t * (12.0 + 100.0 * self._scan))
            self.crown.setColorScale(0.78 + self._scan * 0.22, 1, 1, 1)
            if self._mining > 0.03:
                self.emission.show()
                self.emission.setScale(0.7 + recoil * 0.8)
                self.emission.setColorScale(1, 1, 1, self._mining)
            else:
                self.emission.hide()
        elif mode in ("flight", "orbit"):
            intensity = self._motion * (.45 + self._boost * .55)
            self.cockpit.setPos(math.sin(t * 29.0) * .0006 * intensity * motion, 0,
                                math.sin(t * 37.0) * .0008 * intensity * motion)
            brightness = .92 + .04 * self._motion
            self.instruments.setColorScale(brightness, brightness, brightness, 1)
            color = _AMBER if braking or self._contact > .08 else _CYAN
            self.indicators.setColorScale(*color, 1)
            for index, node in self.thrust_segments:
                strength = 1.0 if self._motion * 8 > index else .12
                color = _AMBER if self._boost > .3 or braking else _CYAN
                node.setColorScale(*(c * strength for c in color), 1)

    def destroy(self):
        if not self._destroyed:
            self.root.removeNode()
            self._destroyed = True
