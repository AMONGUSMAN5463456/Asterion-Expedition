"""Game orchestration: exploration, travel, interactions and accessible menus.

The engine-facing systems are deliberately separated from the pure save/economy
code. No network connection is used by the running game.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import time

from direct.showbase.ShowBase import ShowBase
from panda3d.core import CardMaker, ClockObject, LineSegs, NodePath, TransparencyAttrib, Vec3

from . import __version__
from .audio import AudioManager
from .content import BUILDINGS, ITEMS, RECIPES, STORY, RUIN_LORE
from .controller import PlayerController
from .effects import PlayerEffects
from .geometry import make_ship
from .navigation import plan_route
from .occlusion import AmbientOcclusion
from .state import GameState
from .ui import GameUI
from .universe import galaxy_catalog, generate_system, get_planet, terrain_height
from .world import WorldRenderer


def default_save_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AsterionExpedition"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AsterionExpedition"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "asterion-expedition"


def distance(a, b) -> float:
    return (Vec3(*a) - Vec3(*b)).length()


def quantity_text(items) -> str:
    return "  /  ".join(f"{ITEMS.get(k, {}).get('name', k.replace('_', ' ').title())} {v}" for k, v in items.items()) or "No materials required"


class ExpeditionApp(ShowBase):
    """One independent local expedition and all its rendered views."""

    def __init__(self, save_dir=None, offscreen=False, no_audio=False):
        super().__init__(windowType="offscreen" if offscreen else None)
        self.disableMouse()
        self.offscreen = offscreen
        self.no_audio = no_audio
        self.save_dir = Path(save_dir) if save_dir else default_save_dir()
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.save_path = self.save_dir / "expedition.json"
        self.camLens.setFov(78)
        self.camLens.setNearFar(0.12, 80000)
        self.game = GameState()
        self._ao_filters = None
        self._ao_available = self.win.getGsg().getSupportsBasicShaders()
        self.system = generate_system(0)
        self.planet = self.system["planets"][0]
        self.world = WorldRenderer(self)
        self.controller = PlayerController(self)
        self.effects = PlayerEffects(self)
        self.ui = GameUI(self, self.action)
        self.audio = AudioManager(self, volume=0 if no_audio else self.game.settings.get("volume", .45))
        self.started = False
        self.cleaned = False
        self.current_panel = ""
        self.panel_context = None
        self.notice = ""
        self.notice_time = 0.0
        self.autosave_time = 0.0
        self.hud_time = 0.0
        self.scanner_time = 0.0
        self.mine_time = 0.0
        self.mine_id = ""
        self.target = None
        self.target_distance = 0.0
        self.nearby = None
        self.ship = None
        self.beam = None
        self.autopilot = None
        self.transition = None
        self.transition_elapsed = 0.0
        self.transition_done = False
        self.storm = 0.0
        self.low_warning = 0.0
        self.base_time = 0.0
        self.navigation = None
        self._quitting = False
        fade = CardMaker("scene-transition")
        fade.setFrame(-1, 1, -1, 1)
        self.fade = self.render2d.attachNewNode(fade.generate())
        self.fade.setTransparency(TransparencyAttrib.MAlpha)
        self.fade.setColor(.015, .025, .045, 0)
        self.fade.setBin("fixed", 1000)
        self.fade.setDepthTest(False)
        self.fade.setDepthWrite(False)
        self.fade.hide()
        self._load_surface(fresh=True)
        self.controller.set_enabled(False)
        self.ui.show_title(self.save_path.exists())
        self.current_panel = "title"
        self._bind_controls()
        if not offscreen:
            self.taskMgr.add(self._frame, "expedition-update")

    def _apply_ambient_occlusion(self):
        enabled = (self._ao_available and self.game.mode == "surface"
                   and self.game.settings.get("ambient_occlusion", True))
        if enabled == (self._ao_filters is not None):
            return
        if not enabled:
            self._ao_filters.cleanup()
            self._ao_filters = None
            return
        try:
            self._ao_filters = AmbientOcclusion(self.win, self.cam)
        except RuntimeError as exc:
            print(f"Ambient occlusion disabled: {exc}", file=sys.stderr)
            self._ao_available = False

    def _bind_controls(self):
        mapping = {
            "escape": self.pause, "e": self.interact, "f": self.flight_action,
            "c": self.scan, "r": self.recharge, "x": self.recall_ship,
            "i": lambda: self.toggle_panel("inventory"), "tab": lambda: self.toggle_panel("inventory"),
            "k": lambda: self.toggle_panel("craft"), "m": lambda: self.toggle_panel("map"),
            "j": lambda: self.toggle_panel("journal"), "b": lambda: self.toggle_panel("build"),
            "h": lambda: self.toggle_panel("help"), "f5": self.save_game,
            "f9": lambda: self.open_panel("reload"),
        }
        for key, method in mapping.items():
            self.accept(key, method)
        self.accept("window-event", self._window_event)

    def _window_event(self, window):
        if self.offscreen or not hasattr(window, "getProperties"):
            return
        props = window.getProperties()
        if not props.getOpen():
            self.quit_game()
        else:
            # Preserve Panda3D's aspect/lens/pixel-space resize handling.
            ShowBase.windowEvent(self, window)
            if not props.getForeground() and self.started and not self.ui.panel_open:
                self.pause()

    @property
    def playing(self):
        return self.started and not self.ui.panel_open and self.transition is None

    def toast(self, message, sound=None, seconds=4.5):
        self.notice = str(message)
        self.notice_time = seconds
        if sound:
            self.audio.play(sound)

    def _remove_ship(self):
        self.world.collisions.remove_group("player-ship")
        if self.ship is not None:
            self.ship.removeNode()
            self.ship = None

    def _site_clear(self, x, y, radius=4.8, height=7.0, ignore=()):
        """Check a landing/build footprint against terrain and solid scenery."""
        samples = [self.world.height(x + dx * radius, y + dy * radius)
                   for dx, dy in ((0, 0), (-1, -1), (1, -1), (-1, 1), (1, 1))]
        if max(samples) - min(samples) > 2.4:
            return False
        # Extend the rounded foot below terrain so the full footprint is checked
        # at ground level too, including low rocks under the wings.
        top = max(samples) + height + radius + .05
        return not self.world.collisions.overlaps_capsule(
            (x, y, top), radius=radius, height=height + radius * 2, ignore=ignore)

    @staticmethod
    def _site_candidates(x, y, radii=(0, 6, 12, 18, 26, 38, 54)):
        for radius in radii:
            for index in range(1 if radius == 0 else 16):
                angle = math.tau * index / 16
                yield x + radius * math.cos(angle), y + radius * math.sin(angle)

    def _safe_surface_position(self, position):
        """Resolve old saves/spawns gently, preserving a valid elevated roof position."""
        p = Vec3(*position)
        p.z = max(p.z, self.world.height(p.x, p.y) + 1.8)
        if not self.world.collisions.overlaps_capsule(p):
            return p
        for x, y in self._site_candidates(p.x, p.y, (1, 2, 4, 7, 12, 18, 26)):
            eye = Vec3(x, y, self.world.height(x, y) + 1.8)
            if not self.world.collisions.overlaps_capsule(eye):
                return eye
        # A newly solid structure can surround an old save. Generic overlap
        # recovery may push down through a floor, so accept only clear points
        # above terrain; otherwise settle on a reachable roof above this site.
        recovered = self.world.collisions.move_capsule(p, (0, 0, 0)).position
        ground = self.world.height(recovered.x, recovered.y)
        if recovered.z >= ground + 1.8 and not self.world.collisions.overlaps_capsule(recovered):
            return recovered
        ground = self.world.height(p.x, p.y)
        for lift in (4, 8, 16, 32, 64, 128, 256, 512, 1024, 4096):
            eye = Vec3(p.x, p.y, max(p.z, ground + 1.8) + lift)
            if self.world.collisions.overlaps_capsule(eye):
                continue
            hit = self.world.collisions.raycast(eye, (0, 0, -1), eye.z - ground)
            floor = max(ground, hit.position.z if hit and hit.normal.z > .6 else ground)
            settled = Vec3(p.x, p.y, floor + 1.804)
            if not self.world.collisions.overlaps_capsule(settled):
                return settled
            return eye
        # Renderer-owned scenery is much shorter than this final clearance.
        return Vec3(p.x, p.y, max(p.z, ground + 1.8) + 8192)

    def _park_ship(self, x, y, heading=0, avoid_player=False):
        site = self._find_landing_site(x, y, avoid_player)
        if site is None:
            self.toast("No clear landing site nearby. Move into open ground and recall again.", "alert")
            return False
        self._remove_ship()
        x, y = site
        ground = max(self.world.height(x + dx, y + dy)
                     for dx, dy in ((0, 0), (-2, -2), (2, -2), (0, 3)))
        self.game.ship_position = [float(x), float(y), float(ground)]
        self.ship = make_ship(self.render)
        self.ship.setPos(x, y, ground)
        self.ship.setH(heading)
        self._register_ship_collision(x, y, ground, heading)
        return True

    def _find_landing_site(self, x, y, avoid_player=False):
        return next(((sx, sy) for sx, sy in self._site_candidates(x, y)
                     if self._site_clear(sx, sy, ignore=("player-ship",))
                     and (not avoid_player or
                          math.hypot(sx - self.controller.position.x, sy - self.controller.position.y) > 6)), None)

    def _register_ship_collision(self, x, y, ground, heading):
        angle = math.radians(heading)
        shapes = []
        for name, center, half in (
                ("hull", (0, -.15, 1.65), (1.02, 2.7, .8)),
                ("nose", (0, 2.9, 1.35), (.45, 1.05, .35)),
                ("port-wing", (-2.15, -1.95, 1.28), (1.25, 1.05, .18)),
                ("starboard-wing", (2.15, -1.95, 1.28), (1.25, 1.05, .18)),
                ("port-engine", (-2.28, -2.12, 1.3), (.48, 1.58, .48)),
                ("starboard-engine", (2.28, -2.12, 1.3), (.48, 1.58, .48))):
            cx, cy, cz = center
            shapes.append({"id": "player-ship:" + name, "type": "box",
                           "center": (x + cx * math.cos(angle) - cy * math.sin(angle),
                                      y + cx * math.sin(angle) + cy * math.cos(angle), ground + cz),
                           "half": half, "heading": heading})
        self.world.collisions.set_group("player-ship", shapes)

    def _load_surface(self, fresh=False, landing=None):
        self.system = generate_system(self.game.system_id)
        self.planet = self.system["planets"][self.game.planet_index]
        # Stream around the destination, never the previous orbit coordinates.
        if landing is not None:
            lx, ly = float(landing[0]) + 10, float(landing[1]) - 2
            self.game.position = [lx, ly, terrain_height(self.planet["seed"], lx, ly) + 1.8]
            self.game.heading = self.controller.heading
            self.game.pitch = 0
        elif fresh:
            self.game.position = [0, -16, terrain_height(self.planet["seed"], 0, -16) + 1.8]
        self._remove_ship()
        self.world.load_surface(self.planet, self.game)
        self.controller.set_collision_world(self.world.collisions)
        self.game.mode = "surface"
        if landing is not None:
            x, y = float(landing[0]), float(landing[1])
            pos = [x + 10, y - 2, self.world.height(x + 10, y - 2) + 1.8]
            if self._park_ship(x, y, self.controller.heading):
                sx, sy, _ = self.game.ship_position
                pos = [sx + 10, sy - 2, self.world.height(sx + 10, sy - 2) + 1.8]
            else:
                self.game.ship_position = [x, y, self.world.height(x, y)]
        elif fresh:
            pos = [0, -16, self.world.height(0, -16) + 1.8]
            self._park_ship(11, 2, -25)
        else:
            pos = list(self.game.position)
            if len(pos) != 3:
                pos = [0, -16, self.world.height(0, -16) + 1.8]
            pos[2] = max(pos[2], self.world.height(pos[0], pos[1]) + 1.8)
            parked = self.game.ship_position
            self._park_ship(parked[0], parked[1])
            if distance(pos, parked) < 24:
                # If scenery added in an update forces the parked craft aside,
                # a save beside it should still resume within boarding reach.
                pos[0] += self.game.ship_position[0] - parked[0]
                pos[1] += self.game.ship_position[1] - parked[1]
        pos = self._safe_surface_position(pos)
        self.controller.set_mode("surface", pos, self.game.heading if not fresh else 8,
                                 self.game.pitch if not fresh else 0)
        self.mine_time = 0
        self.target = None
        self.navigation = None
        self.world.update(0, self.controller.position, self.game.elapsed, 0)
        self._apply_ambient_occlusion()

    def new_game(self):
        preferences = dict(self.game.settings)
        self.game = GameState()
        self.game.settings.update(preferences)
        self.audio.set_volume(0 if self.no_audio else self.game.settings.get("volume", .45))
        self.autopilot = None
        self.started = True
        self._load_surface(fresh=True)
        self.game.visit(self.planet)
        self.close_panel()
        self.toast("Expedition online. C scans. Hold left mouse to mine. H opens your field guide.", "discover", 9)

    def continue_game(self):
        if not self.save_path.exists():
            return self.new_game()
        self.game = GameState.load(self.save_path)
        self.started = True
        self.autopilot = None
        if self.game.mode == "orbit":
            position = list(self.game.position)
            self.enter_orbit(position=position, restoring=True)
        else:
            self._load_surface(fresh=False)
        self.audio.set_volume(0 if self.no_audio else self.game.settings.get("volume", .45))
        self.close_panel()
        self.toast(getattr(self.game, "load_warning", "") or "Expedition restored. Welcome back, pathfinder.", "ui")

    def _sync_state(self):
        self.game.position = [float(v) for v in self.controller.position]
        self.game.heading = float(self.controller.heading)
        self.game.pitch = float(self.controller.pitch)
        # Surface flight resumes safely beside the ship after loading.
        self.game.mode = "orbit" if self.controller.mode == "orbit" else "surface"
        if self.controller.mode == "flight":
            x, y = self.controller.position.x, self.controller.position.y
            self.game.ship_position = [x, y, self.world.height(x, y)]
            self.game.position = [x + 10, y, self.world.height(x + 10, y) + 1.8]

    def save_game(self, announce=True):
        if not self.started:
            return False
        self._sync_state()
        ok, message = self.game.save(self.save_path)
        if announce or not ok:
            self.toast(message, "ui" if ok else "alert")
        self.autosave_time = 0
        return ok

    def quit_game(self):
        if self._quitting:
            return
        self._quitting = True
        self.save_game(announce=False)
        self.taskMgr.stop()
        self.userExit()

    def close_panel(self):
        self.ui.hide_panel()
        self.current_panel = ""
        self.panel_context = None
        self.controller.set_enabled(self.started and self.transition is None and not self.offscreen)

    def toggle_panel(self, kind):
        if not self.started:
            if kind == "help":
                self.open_panel(kind)
            return
        if self.current_panel == kind:
            self.close_panel()
        else:
            self.open_panel(kind)

    def pause(self):
        if self.transition:
            return
        if not self.started:
            self.ui.show_title(self.save_path.exists())
            self.current_panel = "title"
        elif self.ui.panel_open:
            self.close_panel()
        else:
            self.open_panel("pause")

    def transition_to(self, callback, message):
        if self.transition:
            return
        self.close_panel()
        self.transition = callback
        self.transition_elapsed = 0.0
        self.transition_done = False
        self.controller.set_enabled(False)
        self.fade.show()
        self.toast(message, "launch", 5)

    def launch(self):
        if self.controller.mode != "surface":
            return
        if self.ship is None:
            self.toast("Your ship needs a clear landing site. Press X on open ground to recall it.", "alert")
            return
        if distance(self.controller.position, self.game.ship_position) > 22:
            self.toast("Move closer to your ship, or press X to recall it.")
            return
        if self.game.vitals["fuel"] < 3:
            self.toast("Launch fuel low. Press R to refuel, or use the rescue option in Esc.", "alert")
            return
        pos = Vec3(*self.game.ship_position) + Vec3(0, 0, 7)
        self._remove_ship()
        self.controller.set_mode("flight", pos, self.controller.heading, 8)
        self.game.vitals["fuel"] = max(0, self.game.vitals["fuel"] - 2 / (1 + .25 * self.game.upgrades.get("engine", 0)))
        self.game.record("launched")
        self.toast("Launch complete. W thrust | Mouse steer | Space climb | Reach 420 m for orbit.", "launch", 8)

    def enter_orbit(self, position=None, restoring=False):
        self._remove_ship()
        self.system = generate_system(self.game.system_id)
        self.planet = self.system["planets"][self.game.planet_index]
        self.world.load_orbit(self.system, self.game)
        self.world.collisions.set_group("atmospheres", [
            {"id": "atmosphere:" + planet["id"], "type": "sphere",
             "center": planet["position"], "radius": planet["size"] + 80}
            for planet in self.system["planets"]])
        self.controller.set_collision_world(self.world.collisions)
        self.game.mode = "orbit"
        self._apply_ambient_occlusion()
        if position is None:
            center = Vec3(*self.planet["position"])
            position = center + Vec3(0, -self.planet["size"] - 850, self.planet["size"] * .18)
        self.controller.set_mode("orbit", position, self.game.heading if restoring else 0,
                                 self.game.pitch if restoring else 0)
        self._constrain_orbit()
        self.autopilot = None
        self.target = None
        self.navigation = None
        self.world.update(0, self.controller.position, self.game.elapsed, 0)
        if not restoring:
            self.toast(f"{self.system['name']} // orbital flight. M opens navigation. E docks near a station.", "discover", 8)

    def land_on_planet(self, planet_index):
        self.game.planet_index = int(planet_index)
        self._load_surface(fresh=True)
        self.game.visit(self.planet)
        self.autopilot = None
        self.toast(f"Touchdown on {self.planet['name']}. {self.planet['description']}", "land", 8)
        self.save_game(announce=False)

    def flight_action(self):
        if not self.playing:
            return
        mode = self.controller.mode
        if mode == "surface":
            self.launch()
        elif mode == "flight":
            p = self.controller.position
            altitude = p.z - self.world.height(p.x, p.y)
            if altitude > 65 or abs(self.controller.speed) > 48:
                self.toast("Landing requires altitude below 65 m and speed below 48 m/s. S brakes; Ctrl descends.")
            else:
                landing = self._find_landing_site(p.x, p.y)
                if landing is None:
                    self.toast("No clear landing site. Fly toward open, level ground and try F again.", "alert")
                    return
                self.transition_to(lambda: self._load_surface(landing=landing), "Landing approach confirmed")
        else:
            nearest = min(self.system["planets"], key=lambda p: distance(p["position"], self.controller.position) - p["size"])
            gap = distance(nearest["position"], self.controller.position) - nearest["size"]
            if gap < 1600:
                self.transition_to(lambda: self.land_on_planet(nearest["index"]), f"Entering {nearest['name']} atmosphere")
            else:
                self.toast("Approach a planet, then press F. M can set an automatic approach.")

    def recall_ship(self):
        if not self.playing or self.controller.mode != "surface":
            return
        p = self.controller.position
        if self._park_ship(p.x + 13, p.y + 5, self.controller.heading, avoid_player=True):
            self.toast("Ship recalled to a clear landing site.", "land")

    def rescue(self):
        if not self.started:
            return
        if hasattr(self.game, "rescue"):
            result = self.game.rescue()
            message = result[1] if isinstance(result, tuple) and len(result) > 1 else "Rescue complete. Equipment and cargo recovered."
        else:
            self.game.credits = max(0, self.game.credits - min(500, self.game.credits // 10))
            self.game.vitals.update({k: 100 for k in self.game.vitals})
            message = "Rescue complete. Equipment and cargo recovered."
        self.autopilot = None
        self._load_surface(fresh=True)
        self.close_panel()
        self.toast(message, "land", 7)

    def _visible_target(self, entity, center):
        p = self.controller.position
        offset = center - p
        length = offset.length()
        if length < .01:
            return True
        hit = self.world.collisions.raycast(p, offset / length, length)
        if hit and hit.distance < length - float(entity.get("radius", 2)) - .35:
            if hit.id != entity["id"] and not hit.id.startswith(entity["id"] + ":"):
                return False
        if self.controller.mode != "orbit":
            for i in range(1, 12):
                point = p + offset * (i / 12)
                if point.z < self.world.height(point.x, point.y) - .1:
                    return False
        return True

    def _find_target(self):
        p = Vec3(self.controller.position)
        forward = self.controller.forward()
        best = None
        best_score = 1e9
        nearby = None
        nearby_dist = 1e9
        scan_range = 100 * (1 + self.game.upgrades.get("scanner", 0) * .4)
        max_range = 1100 if self.controller.mode == "orbit" else (scan_range if self.scanner_time else 38)
        for entity in self.world.interactables():
            center = Vec3(*entity["pos"])
            radius = float(entity.get("radius", 2))
            if self.controller.mode != "orbit":
                center.z += min(radius * .6, 4)
            offset = center - p
            length = offset.length()
            kind = entity["kind"]
            interaction_range = 650 if kind == "station" else 18
            if kind in ("outpost", "ruin", "beacon", "station", "habitat", "extractor", "solar") and length < interaction_range and length < nearby_dist:
                nearby, nearby_dist = entity, length
            if length > max_range or length < .01:
                continue
            along = offset.dot(forward)
            if along <= 0:
                continue
            side = (offset - forward * along).length()
            tolerance = max(radius, length * .035)
            if side <= tolerance:
                score = along + side * 3
                if score < best_score and self._visible_target(entity, center):
                    best, best_score = entity, score
        self.target = best
        self.target_distance = distance(p, best["pos"]) if best else 0
        self.nearby = nearby

    def scan(self):
        if not self.playing:
            return
        self.scanner_time = 7.0
        self.audio.play("scan")
        self._find_target()
        entity = self.target
        if entity is None:
            candidates = [e for e in self.world.interactables()
                          if distance(e["pos"], self.controller.position) < 100 * (1 + self.game.upgrades.get("scanner", 0) * .4)
                          and e["id"] not in self.game.discoveries]
            entity = min(candidates, key=lambda e: distance(e["pos"], self.controller.position)) if candidates else None
        if entity:
            record = {"name": entity["name"], "kind": entity["kind"], "planet": self.planet["name"],
                      "planet_id": self.planet["id"], "resource": entity.get("resource", ""),
                      "description": entity.get("description", "Survey archived by the expedition scanner.")}
            ok, message = self.game.discover(entity["id"], record)
            self.toast(message, "discover" if ok else None)
        else:
            self.toast("Survey pulse complete. No unrecorded targets in range. Explore further.")

    def interact(self):
        if not self.playing:
            return
        if self.autopilot:
            self.autopilot = None
            self.controller.velocity = Vec3(0)
            self.controller.speed = 0
            self.toast("Automatic approach cancelled.")
            return
        self._find_target()
        if self.controller.mode == "surface" and self.ship is not None and distance(self.controller.position, self.game.ship_position) < 19:
            self.launch()
            return
        entity = self.nearby
        if entity is None:
            self.toast("Move closer to a ship, outpost, signal ruin, or station.")
            return
        kind = entity["kind"]
        if kind in ("outpost", "station"):
            self.game.vitals["oxygen"] = 100
            self.game.vitals["hazard"] = 100
            self.game.vitals["shield"] = 100
            self.game.vitals["fuel"] = min(100, self.game.vitals["fuel"] + 30)
            self.open_panel("trade", entity)
        elif kind == "ruin":
            key = "signal:" + entity["id"]
            if key not in self.game.discoveries and self.game.cargo_used() >= self.game.capacity():
                self.toast("Make room for one Archive Fragment before opening this signal archive.", "alert")
                return
            first, _ = self.game.discover(key, {"name": "Signal // " + entity["name"], "kind": "signal", "planet": self.planet["name"]})
            if first:
                self.game.record("ruins")
                self.game.add_item("relic", 1)
            self.open_panel("signal", {"entity": entity, "first": first})
            self.audio.play("discover")
        elif kind in ("beacon", "habitat", "solar", "extractor"):
            if hasattr(self.game, "collect_base_yield"):
                result = self.game.collect_base_yield(self.planet["id"])
                message = result[1] if isinstance(result, tuple) else str(result)
                self.toast(message, "collect")
            self.game.vitals["oxygen"] = min(100, self.game.vitals["oxygen"] + 20)
            self.game.vitals["hazard"] = min(100, self.game.vitals["hazard"] + 30)
            self.save_game(announce=False)

    def recharge(self):
        if not self.playing:
            return
        v = self.game.vitals
        choices = []
        if self.controller.mode != "surface" or v["fuel"] < 30:
            choices += [(100 - v["fuel"], "fuel_cell")]
        choices += [(100 - v["oxygen"], "life_gel"), (100 - v["oxygen"], "oxygen"),
                    (100 - min(v["hazard"], v["energy"]), "ion_cell"), (100 - v["hazard"], "sodium"),
                    (100 - v["shield"], "repair_kit")]
        for deficit, item in sorted(choices, reverse=True):
            if deficit >= 5 and self.game.inventory.get(item, 0) > 0:
                ok, message = self.game.consume(item)
                if ok:
                    self.toast(message, "collect")
                    return
        self.toast("No recharge needed or no compatible supplies. K crafts gel, ion cells and fuel; I shows cargo.")

    def _mine(self, dt):
        self._clear_beam()
        entity = self.target
        if not self.controller.mouse_down or not entity or entity["kind"] not in ("mineral", "flora", "asteroid"):
            self.mine_time = 0
            self.mine_id = ""
            return
        if self.controller.mode == "flight":
            return
        max_range = 1000 if entity["kind"] == "asteroid" else 38
        if self.target_distance > max_range:
            self.mine_time = 0
            return
        if self.mine_id != entity["id"]:
            self.mine_time = 0
            self.mine_id = entity["id"]
        start = Vec3(self.controller.position) + self.controller.forward() * .8 + Vec3(.18, 0, -.2)
        end = Vec3(*entity["pos"]) + Vec3(0, 0, min(entity.get("radius", 2) * .5, 3))
        line = LineSegs("mining-beam")
        line.setThickness(3)
        line.setColor(.15, .95, 1, 1)
        line.moveTo(start)
        line.drawTo(end)
        self.beam = self.render.attachNewNode(line.create())
        self.beam.setLightOff()
        self.beam.setFogOff()
        duration = max(.25, float(entity.get("hardness", 1)) * .8 / (1 + self.game.upgrades.get("mining", 0) * .35))
        previous = self.mine_time
        self.mine_time += dt
        if previous == 0:
            self.audio.play("mine")
        if self.mine_time >= duration:
            resource = entity.get("resource", "ferrite")
            amount = max(1, int(entity.get("amount", 12)))
            if self.game.capacity() - self.game.cargo_used() < amount:
                self.toast(f"Cargo needs {amount} free units. Sell supplies or install a cargo upgrade.", "alert")
                self.mine_time = 0
                return
            added = self.game.add_item(resource, amount)
            if added:
                self.game.record("mined", added)
                depleted = self.game.depleted.setdefault(self.planet["id"] if self.controller.mode != "orbit" else f"orbit:{self.system['id']}", [])
                if entity["id"] not in depleted:
                    depleted.append(entity["id"])
                self.world.set_depleted(entity["id"])
                self.toast(f"+{added} {ITEMS.get(resource, {}).get('name', resource)}", "collect", 2)
            self.mine_time = 0
            self.target = None

    def _clear_beam(self):
        if self.beam is not None:
            self.beam.removeNode()
            self.beam = None

    def _survival(self, dt):
        v = self.game.vitals
        if self.controller.mode != "surface":
            v["oxygen"] = min(100, v["oxygen"] + dt * 3)
            v["hazard"] = min(100, v["hazard"] + dt * 5)
            v["shield"] = min(100, v["shield"] + dt * .5)
            return
        sheltered = self.ship is not None and distance(self.controller.position, self.game.ship_position) < 13
        if self.nearby and self.nearby["kind"] in ("outpost", "habitat", "station"):
            sheltered = True
        for building in self.game.bases.get(self.planet["id"], []):
            if distance(self.controller.position, building["pos"]) < 22:
                if building["kind"] == "habitat":
                    sheltered = True
                elif building["kind"] == "solar":
                    v["energy"] = min(100, v["energy"] + dt * 8)
        protection = 1 + self.game.upgrades.get("hazard", 0) * .65
        hazard_rate = (.12 + self.planet.get("hazard", 0) * .6 + self.storm * .7) / protection
        v["oxygen"] = max(0, min(100, v["oxygen"] + (2.0 if sheltered else -.10) * dt))
        v["hazard"] = max(0, min(100, v["hazard"] + (4.0 if sheltered else -hazard_rate) * dt))
        if v["oxygen"] <= 0 or v["hazard"] <= 0:
            v["shield"] = max(0, v["shield"] - dt * 4)
        elif sheltered:
            v["shield"] = min(100, v["shield"] + dt)
        if v["shield"] <= 0:
            self.open_panel("rescue")
        if min(v["oxygen"], v["hazard"]) < 20 and self.low_warning <= 0:
            self.toast("Suit reserves low. R recharges. Ships and outposts provide shelter.", "alert", 7)
            self.low_warning = 25

    def _constrain_orbit(self):
        """Recover saved positions and keep camera/velocity in sync at boundaries."""
        p = self.controller.position
        for planet in self.system["planets"]:
            center = Vec3(*planet["position"])
            delta = p - center
            if delta.length() < planet["size"] + 83:
                if delta.length() < .01:
                    delta = Vec3(0, -1, 0)
                delta.normalize()
                p = center + delta * (planet["size"] + 110)
        result = self.world.collisions.move_sphere(p, Vec3(0), radius=3)
        changed = (result.position - self.controller.position).length() > .001
        self.controller.position = result.position
        if changed:
            self.controller.velocity = Vec3(0)
            self.controller.speed = 0
            self.camera.setPos(result.position)

    def _plan_approach(self, destination):
        obstacles = [{"id": p["id"], "center": p["position"], "radius": p["size"] + 90}
                     for p in self.system["planets"]]
        obstacles += [{"id": e["id"], "center": e["pos"], "radius": e.get("radius", 20) + 12}
                      for e in self.world.interactables() if e["kind"] == "asteroid"]
        obstacles.append({"id": "exchange", "center": self.system["station"], "radius": 145})
        start = Vec3(self.controller.position)
        # A pilot resting against a body can be just inside the planner's extra
        # safety margin. Move out through real swept collision before plotting.
        for _ in range(4):
            for obstacle in obstacles:
                center = Vec3(*obstacle["center"])
                delta = start - center
                if delta.length() < obstacle["radius"] + .1:
                    if delta.length() < .001:
                        delta = Vec3(0, 0, 1)
                    delta.normalize()
                    desired = center + delta * (obstacle["radius"] + 1)
                    start = self.world.collisions.move_sphere(start, desired - start, radius=3).position
        self.controller.position = start
        self.controller.velocity = Vec3(0)
        self.controller.speed = 0
        self.camera.setPos(start)
        center = Vec3(*destination["position"])
        stop = destination.get("size", 0) + (700 if destination["kind"] == "planet" else 300)
        delta = start - center
        if delta.length() < .01:
            delta = Vec3(0, -1, 0)
        delta.normalize()
        directions = [delta, Vec3(0, 0, 1), Vec3(0, 0, -1), Vec3(1, 0, 0),
                      Vec3(-1, 0, 0), Vec3(0, 1, 0), Vec3(0, -1, 0)]
        for direction in directions:
            goal = center + direction * stop
            route = plan_route(start, goal, obstacles)
            if route:
                destination["route"] = route
                destination["route_index"] = 0
                self.autopilot = destination
                return True
        self.autopilot = None
        self.toast("No clear automatic route. Move into open space and plot the approach again.", "alert")
        return False

    def _update_autopilot(self, dt):
        if not self.autopilot:
            return False
        dest = self.autopilot
        route = dest.get("route", [])
        index = dest.get("route_index", 0)
        if index >= len(route):
            self.autopilot = None
            self.controller.speed = 0
            self.controller.velocity = Vec3(0)
            if dest["kind"] == "planet":
                self.transition_to(lambda: self.land_on_planet(dest["index"]), f"Atmospheric insertion // {dest['name']}")
            else:
                self.open_panel("trade", {"name": "Orbital Exchange", "kind": "station"})
                self.game.vitals.update({"oxygen": 100, "hazard": 100, "shield": 100})
            return True
        delta = route[index] - self.controller.position
        remaining = delta.length()
        if remaining < .1:
            dest["route_index"] = index + 1
            return True
        direction = delta / remaining
        cruise = min(5000, max(80, remaining * 1.2))
        velocity = self.controller.speed + (cruise - self.controller.speed) * (1 - math.exp(-3 * dt))
        displacement = direction * min(remaining, velocity * dt)
        result = self.world.collisions.move_sphere(self.controller.position, displacement, radius=3)
        expected = self.controller.position + displacement
        self.controller.position = result.position
        if (expected - result.position).length() > .1:
            self.autopilot = None
            self.controller.velocity = Vec3(0)
            self.controller.speed = 0
            self.controller.collision_feedback = 1.0
            self.camera.setPos(result.position)
            self.toast("Approach paused by an obstruction. M plots a fresh route.", "alert")
            return True
        if (route[index] - result.position).length() < .1:
            dest["route_index"] = index + 1
        self.controller.heading = math.degrees(math.atan2(-direction.x, direction.y))
        self.controller.pitch = math.degrees(math.asin(max(-1, min(1, direction.z))))
        self.controller.speed = velocity
        self.controller.velocity = direction * velocity
        self.camera.setPos(self.controller.position)
        self.camera.setHpr(self.controller.heading, self.controller.pitch, 0)
        self.game.vitals["fuel"] = max(0, self.game.vitals["fuel"] - dt * .1)
        return True

    def step(self, dt):
        if self.cleaned:
            return
        dt = max(0, min(float(dt), .1))
        self.notice_time = max(0, self.notice_time - dt)
        self.low_warning = max(0, self.low_warning - dt)
        if self.transition:
            self.transition_elapsed += dt
            t = self.transition_elapsed
            self.fade.setColor(.015, .025, .045, min(1, t / .3) if t < .35 else max(0, 1 - (t - .35) / .5))
            if t >= .32 and not self.transition_done:
                self.transition_done = True
                self.transition()
            if t >= .88:
                self.transition = None
                self.fade.hide()
                self.controller.set_enabled(self.started and not self.ui.panel_open and not self.offscreen)
        if self.playing:
            self.game.elapsed += dt
            self.autosave_time += dt
            self.base_time += dt
            self.scanner_time = max(0, self.scanner_time - dt)
            phase = (self.game.elapsed + (self.planet["seed"] % 40)) % 330
            self.storm = min(1, max(0, (phase - 245) / 20), max(0, (325 - phase) / 20)) if self.controller.mode != "orbit" else 0
            if not self._update_autopilot(dt):
                self.controller.update(dt, self.world.height, self.game.vitals,
                                       self.game.upgrades, self.game.settings, enabled=True,
                                       gravity=self.planet.get("gravity", 12))
            self.world.update(dt, self.controller.position, self.game.elapsed, self.storm)
            self._find_target()
            self._mine(dt)
            self._survival(dt)
            if self.controller.mode == "flight":
                p = self.controller.position
                if p.z - self.world.height(p.x, p.y) >= 420 and not self.transition:
                    self.transition_to(self.enter_orbit, "Leaving atmosphere // orbital insertion")
            elif self.controller.mode == "orbit" and not self.autopilot:
                self._constrain_orbit()
            if self.autosave_time >= 60:
                self.save_game(announce=False)
        else:
            self._clear_beam()
            self.mine_time = 0
            if not self.started:
                self.world.update(dt, self.controller.position, self.game.elapsed + time.monotonic() % 60, 0)
        self.audio.update(dt, self.controller.mode, abs(self.controller.speed) / 100)
        self.effects.update(dt if self.playing else 0, self.controller.mode if self.started else "hidden",
                            mining=self.mine_time > 0, thrust=min(1, abs(self.controller.speed) / (16 if self.controller.mode == "surface" else 150)),
                            scanner=self.scanner_time > 0,
                            camera_motion=self.game.settings.get("camera_motion", .35),
                            boosting=self.controller.boosting, braking=self.controller.braking,
                            collision_feedback=self.controller.collision_feedback)
        if self._ao_filters is not None:
            self._ao_filters.update()
        self.hud_time += dt
        if self.hud_time >= 1 / 20:
            self.hud_time = 0
            self.ui.update(self._view())

    def _frame(self, task):
        self.step(ClockObject.getGlobalClock().getDt())
        return task.cont

    def _view(self):
        mode = self.controller.mode
        p = self.controller.position
        prompt = "C scan   Hold LMB mine   E interact   H field guide"
        name = ""
        if self.target:
            entity = self.target
            name = f"{entity['name']}  /  {self.target_distance:.0f} m"
            if entity.get("resource"):
                name += f"  /  {ITEMS.get(entity['resource'], {}).get('name', entity['resource'])}"
            if entity["kind"] in ("mineral", "flora", "asteroid"):
                reach = 1000 if entity["kind"] == "asteroid" else 38
                prompt = "Hold LMB extract   C catalogue" if self.target_distance <= reach else f"C catalogue   Approach within {reach} m to extract"
            else:
                prompt = "C catalogue specimen" if entity["kind"] == "fauna" else "Approach the site and press E to interact"
        if mode == "surface" and self.ship is not None and distance(p, self.game.ship_position) < 19:
            prompt = "E / F board and launch your ship"
        elif self.nearby:
            prompt = f"E interact with {self.nearby['name']}"
        if mode == "flight":
            prompt = "W thrust  S brake  Space ascend  Ctrl descend  Shift boost  F land"
        if mode == "orbit":
            prompt = "M navigation  F enter nearby planet  E dock  Shift boost  LMB mine asteroids"
        if self.autopilot:
            name = f"APPROACH // {self.autopilot['name']}"
            prompt = "E cancels automatic approach"
        if self.navigation and not self.target:
            name = f"{self.navigation['name']}  /  {distance(p, self.navigation['pos']):.0f} m"
        objective = self.game.objective()
        if self.navigation:
            d = Vec3(*self.navigation["pos"]) - p
            objective = {"title": "WAYPOINT // " + self.navigation["name"],
                         "description": f"{d.length():.0f} m away. Bearing {(math.degrees(math.atan2(-d.x, d.y)) % 360):.0f} deg. M selects another location.",
                         "progress": "Surface navigation"}
        duration = max(.25, float(self.target.get("hardness", 1)) * .8 / (1 + self.game.upgrades.get("mining", 0) * .35)) if self.target else 1
        return {"mode": mode, "location": self.system["name"] if mode == "orbit" else self.planet["name"],
                "biome": "INTERPLANETARY SPACE" if mode == "orbit" else self.planet["biome"].upper(),
                "coordinates": f"{p.x:+.0f} / {p.y:+.0f}", "speed": self.controller.speed,
                "altitude": p.z - self.world.height(p.x, p.y) if mode != "orbit" else p.z,
                "vitals": self.game.vitals, "credits": self.game.credits, "cargo": self.game.cargo_used(),
                "capacity": self.game.capacity(), "target": name, "prompt": prompt,
                "objective": objective, "notice": self.notice if self.notice_time else "", "scanner": bool(self.scanner_time),
                "storm": self.storm > .2, "mining_progress": min(1, self.mine_time / duration),
                "heading": self.controller.heading, "pitch": self.controller.pitch, "version": __version__,
                "grounded": self.controller.grounded, "jetpacking": self.controller.jetpacking,
                "boosting": self.controller.boosting, "braking": self.controller.braking,
                "drifting": self.controller.drifting, "throttle": self.controller.throttle,
                "vertical_speed": self.controller.velocity.z,
                "flight_assist": self.game.settings.get("flight_assist", True),
                "collision_feedback": self.controller.collision_feedback}

    @staticmethod
    def button(label, action, payload=None, enabled=True):
        return {"label": label, "action": action, "payload": payload, "enabled": enabled}

    def _tabs(self, active):
        return [{"label": title, "action": "open", "payload": key, "active": active == key}
                for key, title in (("inventory", "CARGO"), ("craft", "FABRICATOR"), ("map", "NAVIGATION"),
                                   ("journal", "EXPEDITION"), ("build", "CONSTRUCTION"))]

    def open_panel(self, kind, context=None):
        if self.transition:
            return
        if not self.started and kind not in ("help", "settings", "new_confirm"):
            return
        self.controller.set_enabled(False)
        self.controller.reset_keys()
        self._clear_beam()
        self.current_panel = kind
        self.panel_context = context
        g = self.game
        rows = []
        buttons = []
        title = kind.upper()
        subtitle = "ASTERION EXPEDITION"
        footer = "Expedition paused while menus are open. Esc returns to the world."
        tabs = self._tabs(kind) if kind in ("inventory", "craft", "map", "galaxy", "journal", "discoveries", "contracts", "build", "trade") else []
        if kind == "inventory":
            title, subtitle = "Expedition cargo", f"{g.cargo_used()} / {g.capacity()} cargo units   |   {g.credits:,} credits"
            for item, amount in g.inventory.items():
                if amount <= 0 or item not in ITEMS:
                    continue
                info = ITEMS[item]
                usable = item in ("oxygen", "sodium", "life_gel", "ion_cell", "fuel_cell", "warp_cell", "repair_kit")
                actions = [self.button("USE", "consume", item)] if usable else []
                actions.append(self.button("DISCARD 1", "discard", item))
                rows.append({"title": info["name"], "body": info["description"], "meta": f"{amount} units  /  {info['category']}", "buttons": actions})
            buttons = [self.button("VIEW UPGRADES", "open", "upgrades")]
        elif kind in ("craft", "upgrades"):
            title = "Fabricator" if kind == "craft" else "Equipment upgrades"
            subtitle = "Refine materials, make supplies, and fit permanent improvements."
            for key, recipe in RECIPES.items():
                upgrade = recipe.get("upgrade")
                if kind == "upgrades" and not upgrade:
                    continue
                if upgrade and recipe.get("tier", 1) <= g.upgrades.get(upgrade, 0):
                    continue
                if upgrade and recipe.get("tier", 1) > g.upgrades.get(upgrade, 0) + 1:
                    continue
                enough = all(g.inventory.get(i, 0) >= n for i, n in recipe["inputs"].items())
                installed = f"Installed tier: {g.upgrades.get(upgrade, 0)} / 3" if upgrade else "Makes: " + quantity_text(recipe.get("outputs", {}))
                rows.append({"title": recipe["name"], "body": recipe["description"] + "\n" + quantity_text(recipe["inputs"]),
                             "meta": installed, "buttons": [self.button("INSTALL" if upgrade else "CRAFT", "craft", key, enough)]})
            footer = "Materials are consumed only when crafting succeeds. Upgrades are permanent and install in order."
        elif kind in ("map", "galaxy"):
            title = "Navigation chart" if kind == "map" else "The Asterion Reach"
            subtitle = f"{self.system['name']}   |   24 star systems   |   96 worlds"
            buttons = [self.button("LOCAL SYSTEM", "open", "map"), self.button("GALAXY", "open", "galaxy")]
            orbit = self.controller.mode == "orbit"
            if kind == "galaxy":
                for system in galaxy_catalog():
                    current = system["id"] == g.system_id
                    rows.append({"title": system["name"], "body": system["description"],
                                 "meta": "CURRENT SYSTEM" if current else "Requires 1 Fold Cell; initiate from orbit",
                                 "buttons": [self.button("CURRENT" if current else "FOLD TO SYSTEM", "warp", system["id"],
                                                         orbit and not current and g.inventory.get("warp_cell", 0) >= 1)]})
            else:
                if not orbit:
                    landmarks = [e for e in self.world.interactables() if e["kind"] in ("outpost", "ruin", "beacon", "habitat", "extractor", "solar")]
                    landmarks.sort(key=lambda e: distance(e["pos"], self.controller.position))
                    rows.append({"title": "Your courier ship", "body": "E or F boards and launches. Climb to 420 m to reach orbit.",
                                 "meta": f"{distance(self.controller.position, g.ship_position):.0f} m away",
                                 "buttons": [self.button("SET WAYPOINT", "waypoint", {"name": "Your ship", "pos": g.ship_position})]})
                    for e in landmarks[:12]:
                        rows.append({"title": e["name"], "body": e.get("description", e["kind"].replace("_", " ").title()),
                                     "meta": f"{distance(e['pos'], self.controller.position):.0f} m away",
                                     "buttons": [self.button("SET WAYPOINT", "waypoint", {"name": e["name"], "pos": list(e["pos"])})]})
                rows.append({"title": "Orbital Exchange", "body": "Trade materials, restore your suit, and accept survey contracts.",
                             "meta": "Automatic approach available in orbit",
                             "buttons": [self.button("APPROACH STATION", "approach_station", enabled=orbit)]})
                for planet in self.system["planets"]:
                    visited = planet["id"] in g.visited
                    rows.append({"title": planet["name"], "body": planet["description"] + "\nResources: " + ", ".join(ITEMS[i]["name"] for i in planet["resources"]),
                                 "meta": f"{planet['temperature']:+.0f} C  |  {planet['biome'].upper()}  |  {'SURVEYED' if visited else 'UNCHARTED'}",
                                 "buttons": [self.button("APPROACH & LAND", "travel", planet["index"], orbit)]})
                footer = "Surface: follow a waypoint. Orbit: automatic approach flies to the selected destination; E cancels."
        elif kind == "journal":
            title, subtitle = "The listening network", f"{min(g.story_stage, len(STORY))} / {len(STORY)} expedition chapters completed"
            buttons = [self.button("DISCOVERIES", "open", "discoveries"), self.button("CONTRACTS", "open", "contracts")]
            for i, chapter in enumerate(STORY):
                complete = i < g.story_stage
                active = i == g.story_stage
                status = "COMPLETE" if complete else "ACTIVE" if active else "UPCOMING"
                count = min(chapter["target"], g.stats.get(chapter["event"], 0))
                body = chapter["description"]
                if complete:
                    body += "\n" + chapter["lore"]
                rows.append({"title": chapter["title"], "body": body,
                             "meta": f"{status}  |  {count} / {chapter['target']}  |  Reward {chapter['reward']:,} credits",
                             "accent": "amber" if active else "cyan", "buttons": []})
        elif kind == "discoveries":
            title, subtitle = "Field atlas", f"{len(g.discoveries)} records   |   {len(g.visited)} planets visited"
            buttons = [self.button("STORY JOURNAL", "open", "journal")]
            for record in reversed(list(g.discoveries.values())):
                rows.append({"title": record.get("name", "Unidentified record"),
                             "body": record.get("description", "Archived by your expedition scanner."),
                             "meta": str(record.get("planet", "")) + "  /  " + str(record.get("kind", "discovery")).upper(), "buttons": []})
            if not rows:
                rows = [{"title": "Your first discovery is ahead", "body": "Press C near plants, rocks, or wildlife. New surveys earn credits.", "buttons": []}]
        elif kind == "contracts":
            title, subtitle = "Survey contracts", "Optional commissions reward exploration, fabrication, and field work."
            buttons = [self.button("STORY JOURNAL", "open", "journal")]
            for contract in g.contracts:
                progress = g.contract_progress(contract)
                rows.append({"title": contract.get("name", "Survey commission"), "body": contract.get("description", ""),
                             "meta": f"{progress['current']} / {progress['target']}  |  {contract.get('reward', 0)} credits",
                             "buttons": [self.button("CLAIM REWARD", "claim_contract", contract["id"], progress["complete"] and not contract.get("claimed", False))]})
            accepted = {c["id"] for c in g.contracts}
            for contract in g.available_contracts():
                if contract["id"] in accepted:
                    continue
                rows.append({"title": contract["name"], "body": contract["description"],
                             "meta": f"Target: {contract['target']}  |  Reward: {contract['reward']} credits",
                             "buttons": [self.button("ACCEPT", "accept_contract", contract["id"])]})
        elif kind == "build":
            title, subtitle = "Outpost construction", f"{self.planet['name']}   |   {len(g.bases.get(self.planet['id'], []))} structures placed"
            buttons = [self.button("COLLECT OUTPUT", "collect_base", enabled=self.controller.mode == "surface")]
            for key, building in BUILDINGS.items():
                enough = all(g.inventory.get(i, 0) >= n for i, n in building["cost"].items())
                rows.append({"title": building["name"], "body": building["description"] + "\n" + quantity_text(building["cost"]),
                             "meta": "Places 12 m ahead on the terrain. Move between builds to arrange your outpost.",
                             "buttons": [self.button("CONSTRUCT", "build", key, enough and self.controller.mode == "surface")]})
            footer = "Built structures remain on their planet. Extractors and gardens accrue output during expedition time."
        elif kind == "trade":
            title = (context or {}).get("name", "Orbital Exchange")
            subtitle = f"{self.system['name']} market   |   {g.credits:,} credits   |   {g.cargo_used()} / {g.capacity()} cargo"
            buttons = [self.button("SURVEY CONTRACTS", "open", "contracts")]
            for item, info in ITEMS.items():
                buy, sell = g.price(item, True), g.price(item, False)
                owned = g.inventory.get(item, 0)
                rows.append({"title": info["name"], "body": info["description"],
                             "meta": f"In cargo {owned}  |  Buy {buy} / Sell {sell} credits each",
                             "buttons": [self.button("BUY 5", "buy", {"item": item, "amount": 5}, g.credits >= buy * 5 and g.capacity() - g.cargo_used() >= 5),
                                         self.button("BUY 1", "buy", {"item": item, "amount": 1}, g.credits >= buy and g.capacity() > g.cargo_used()),
                                         self.button("SELL 5", "sell", {"item": item, "amount": min(5, owned)}, owned > 0)]})
            footer = "Prices vary by star system. Buying always costs more than selling in the same market."
        elif kind == "signal":
            entity = (context or {}).get("entity", {})
            title, subtitle = "A voice in the stone", entity.get("name", "Meridian signal site")
            index = sum(entity.get("id", "signal").encode("utf-8")) % len(RUIN_LORE)
            rows = [{"title": "ARCHIVE RECORD", "body": RUIN_LORE[index], "meta": "Meridian listening network", "buttons": []},
                    {"title": "A new path opens" if (context or {}).get("first") else "The signal remembers you",
                     "body": "This site has been added to your atlas. Different sites carry different records. Continue the expedition to reconstruct the Meridian Key.",
                     "buttons": [self.button("VIEW EXPEDITION", "open", "journal")]}]
        elif kind == "settings":
            title, subtitle = "Expedition settings", "Mouse, sound, and rendering preferences are included in your save."
            s = g.settings
            rows = [
                {"title": "Mouse sensitivity", "body": "Degrees of view rotation per mouse pixel.", "meta": f"{s.get('sensitivity', .16):.2f}",
                 "buttons": [self.button("LOWER", "setting", {"key": "sensitivity", "value": max(.04, s.get("sensitivity", .16) - .02)}),
                             self.button("HIGHER", "setting", {"key": "sensitivity", "value": min(.5, s.get("sensitivity", .16) + .02)})]},
                {"title": "Vertical mouse look", "body": "Choose standard or inverted vertical steering.", "meta": "Inverted" if s.get("invert_y") else "Standard",
                 "buttons": [self.button("TOGGLE", "setting", {"key": "invert_y", "value": not s.get("invert_y", False)})]},
                {"title": "Mouse capture", "body": "Captured look follows the mouse continuously. Drag mode turns while you hold the right mouse button.",
                 "meta": "Captured" if s.get("mouse_capture", True) else "Right-button drag",
                 "buttons": [self.button("TOGGLE", "setting", {"key": "mouse_capture", "value": not s.get("mouse_capture", True)})]},
                {"title": "Field of view", "body": "Adjust the view angle. Higher values show more of your surroundings.", "meta": f"{s.get('fov', 78):.0f} degrees",
                 "buttons": [self.button("NARROWER", "setting", {"key": "fov", "value": s.get("fov", 78) - 5}),
                             self.button("WIDER", "setting", {"key": "fov", "value": s.get("fov", 78) + 5})]},
                {"title": "Camera motion", "body": "Strength of walking bob, landing response, ship banking, and speed effects. OFF keeps the camera steady.",
                 "meta": f"{s.get('camera_motion', .35):.0%}",
                 "buttons": [self.button(label, "setting", {"key": "camera_motion", "value": value})
                             for label, value in (("OFF", 0), ("SUBTLE", .35), ("FULL", 1))]},
                {"title": "Mouse smoothing", "body": "Direct input gives the quickest response. Gentle smoothing softens small hand movements.",
                 "meta": "Direct" if s.get("mouse_smoothing", 0) == 0 else f"{s.get('mouse_smoothing', 0):.0%}",
                 "buttons": [self.button(label, "setting", {"key": "mouse_smoothing", "value": value})
                             for label, value in (("DIRECT", 0), ("GENTLE", .3), ("SMOOTH", .65))]},
                {"title": "Flight assist", "body": "Assist stabilises sideways drift. Disable it to retain momentum while steering. S actively brakes in either mode.",
                 "meta": "Enabled" if s.get("flight_assist", True) else "Inertial flight",
                 "buttons": [self.button("TOGGLE", "setting", {"key": "flight_assist", "value": not s.get("flight_assist", True)})]},
                {"title": "Audio volume", "body": "Original ambient soundscape, engine sounds, and interface tones.", "meta": f"{s.get('volume', .45):.0%}",
                 "buttons": [self.button("QUIETER", "setting", {"key": "volume", "value": max(0, s.get("volume", .45) - .1)}),
                             self.button("LOUDER", "setting", {"key": "volume", "value": min(1, s.get("volume", .45) + .1)})]},
                {"title": "World detail", "body": "Changes terrain and scenery draw distance. Applies when the next planet loads.", "meta": str(s.get("quality", "medium")).upper(),
                 "buttons": [self.button(q.upper(), "setting", {"key": "quality", "value": q}) for q in ("low", "medium", "high")]},
                {"title": "Ambient occlusion", "body": "Soft contact shading on the surface. Applies immediately; disabled in orbit and on unsupported renderers.",
                 "meta": ("On" if s.get("ambient_occlusion", True) else "Off") if self._ao_available else "Unavailable on this renderer",
                 "buttons": [self.button("TOGGLE", "setting", {"key": "ambient_occlusion", "value": not s.get("ambient_occlusion", True)})] if self._ao_available else []},
            ]
        elif kind == "help":
            title, subtitle = "Pathfinder's field guide", "A practical guide to life beyond the chart."
            rows = [
                {"title": "01 / Start with a survey", "body": "Walk with WASD; look with the mouse or arrow keys. Shift sprints. Tap Space to jump; hold to engage the jetpack. Ctrl brakes in the air, and Space + Ctrl hovers. C surveys; hold left mouse to extract a visible deposit.", "meta": "C scan  |  LMB mine  |  Shift sprint", "buttons": []},
                {"title": "02 / Supplies and equipment", "body": "I or Tab opens cargo. K opens the fabricator. Craft Launch Cells from carbon and ferrite; Fold Cells from copper, crystal, and carbon. R uses suitable supplies to recharge a depleted reserve.", "meta": "I cargo  |  K crafting  |  R recharge", "buttons": []},
                {"title": "03 / Take to the sky", "body": "Approach the ship and press E or F. W adds thrust; S brakes. Mouse or arrow keys steer. Space climbs, Ctrl descends, Shift boosts. Climb to 420 m above the terrain to enter orbit. Press X on foot to recall your ship.", "meta": "E / F launch  |  X recall  |  Space climb", "buttons": []},
                {"title": "04 / Find another world", "body": "In orbit, M opens the chart. Select APPROACH & LAND to fly automatically to a planet, or approach manually and press F near the atmosphere. Choose GALAXY to spend a Fold Cell traveling to another system. E cancels automatic approach.", "meta": "M navigation  |  F atmospheric entry", "buttons": []},
                {"title": "05 / Land, trade, investigate", "body": "In atmospheric flight, slow below 48 m/s and descend below 65 m, then F lands. E interacts with nearby outposts, signal ruins, or stations. Markets buy and sell supplies, and the journal tracks your expedition and contracts.", "meta": "E interact  |  J expedition log", "buttons": []},
                {"title": "06 / Make a place to return to", "body": "B opens construction. Buildings are placed 12 m ahead. Move between builds to arrange your outpost. Habitats provide shelter; extractors and gardens accumulate materials. Collect their output from the construction panel.", "meta": "B construction  |  M surface waypoints", "buttons": []},
                {"title": "07 / A resilient expedition", "body": "Shelter near your ship or an outpost restores oxygen and climate protection. Esc offers emergency rescue if supplies run out. Menus pause time. Automatic saves occur every minute; F5 saves and F9 opens reload confirmation.", "meta": "Esc pause / rescue  |  F5 save  |  F9 reload", "buttons": []},
            ]
        elif kind == "pause":
            title, subtitle = "Expedition paused", f"{self.planet['name']}   /   {self.system['name']}"
            rows = [
                {"title": "Return to the horizon", "body": "Your expedition is waiting exactly where you left it.", "buttons": [self.button("RESUME", "close"), self.button("SAVE", "save")]},
                {"title": "Your equipment and journey", "body": "Review the field guide, change settings, or restore the last saved expedition.", "buttons": [self.button("SETTINGS", "settings"), self.button("FIELD GUIDE", "help"), self.button("RELOAD", "open", "reload")]},
                {"title": "Expedition assistance", "body": "Free emergency rescue returns you and your cargo to a safe landing site. Your progress stays intact.", "buttons": [self.button("RESCUE", "open", "rescue")]},
                {"title": "Until the next horizon", "body": "Save the expedition and close the game.", "buttons": [self.button("SAVE & QUIT", "quit")]},
            ]
        elif kind == "rescue":
            title, subtitle = "Expedition assistance", "A remote recovery shuttle is available."
            rows = [{"title": "Return to a safe landing site", "body": "Free rescue restores your suit and ship reserves while keeping your discoveries, credits, and cargo. It remains available without supplies.",
                     "buttons": [self.button("REQUEST RESCUE", "rescue"), self.button("RETURN", "close")]}]
        elif kind == "reload":
            title, subtitle = "Restore your expedition?", "Changes since the last save will be replaced."
            rows = [{"title": "Load the latest saved expedition", "body": "The save includes cargo, upgrades, discoveries, worlds visited, mined deposits and constructed outposts.",
                     "buttons": [self.button("LOAD SAVE", "load", enabled=self.save_path.exists()), self.button("CANCEL", "close")]}]
        elif kind == "new_confirm":
            title, subtitle = "Start a new expedition?", "The next save will replace your current expedition."
            rows = [{"title": "A fresh journey through the Reach", "body": "Your existing expedition will be replaced by a new one when the game next saves. Cancel to keep exploring it.",
                     "buttons": [self.button("START NEW", "confirm_new"), self.button("CANCEL", "close")]}]
        self.ui.show_panel({"id": kind, "title": title, "subtitle": subtitle, "tabs": tabs,
                            "rows": rows, "buttons": buttons, "footer": footer})

    def _refresh_panel(self):
        if self.current_panel and self.current_panel != "title":
            self.open_panel(self.current_panel, self.panel_context)

    def action(self, action, payload=None):
        """Single UI action boundary, used by real menus and integration tests."""
        self.audio.play("ui")
        if action == "close":
            if self.started:
                self.close_panel()
            else:
                self.ui.show_title(self.save_path.exists())
                self.current_panel = "title"
        elif action == "continue":
            self.continue_game()
        elif action == "new_game":
            self.open_panel("new_confirm") if self.save_path.exists() else self.new_game()
        elif action == "confirm_new":
            self.new_game()
        elif action in ("help", "settings"):
            self.open_panel(action)
        elif action == "open":
            self.open_panel(str(payload))
        elif action == "quit":
            self.quit_game()
        elif action == "save":
            self.save_game()
        elif action == "load":
            self.continue_game()
        elif action == "rescue":
            self.rescue()
        elif action in ("craft", "consume") and self.started:
            result = self.game.craft(payload) if action == "craft" else self.game.consume(payload)
            self.toast(result[1], "craft" if result[0] else "alert")
            self._refresh_panel()
        elif action == "discard" and self.started:
            if self.game.remove_item(payload, 1):
                self.toast(f"Discarded 1 {ITEMS.get(payload, {}).get('name', payload)}")
            self._refresh_panel()
        elif action in ("buy", "sell") and self.started and self.current_panel == "trade":
            ok, message = self.game.trade(payload["item"], payload["amount"], action == "buy")
            self.toast(message, "collect" if ok else "alert")
            self._refresh_panel()
        elif action == "waypoint" and self.started:
            self.navigation = {"name": str(payload["name"]), "pos": list(payload["pos"])}
            self.close_panel()
            self.toast(f"Waypoint set: {self.navigation['name']}", "scan")
        elif action in ("travel", "approach_station") and self.started:
            if self.controller.mode != "orbit":
                self.toast("Launch and reach orbit before setting an interplanetary approach.")
                return
            if action == "travel":
                if not isinstance(payload, int) or isinstance(payload, bool) or not 0 <= payload < len(self.system["planets"]):
                    return
                planet = self.system["planets"][payload]
                self.autopilot = {"kind": "planet", "position": planet["position"], "size": planet["size"], "index": payload, "name": planet["name"]}
            else:
                self.autopilot = {"kind": "station", "position": self.system["station"], "size": 0, "name": "Orbital Exchange"}
            self.close_panel()
            if self._plan_approach(self.autopilot):
                self.toast(f"Automatic approach engaged: {self.autopilot['name']}. E cancels.", "launch", 7)
        elif action == "warp" and self.started:
            if self.controller.mode != "orbit" or not isinstance(payload, int) or isinstance(payload, bool) or not 0 <= payload < 24 or payload == self.game.system_id:
                self.toast("Select a different star system while in orbit.")
                return
            if self.game.inventory.get("warp_cell", 0) < 1:
                self.toast("An interstellar fold requires one Fold Cell. Craft one with K.", "alert")
                return
            def fold():
                if self.game.remove_item("warp_cell", 1):
                    self.game.system_id = payload
                    self.game.planet_index = 0
                    self.game.record("warped")
                    self.enter_orbit()
                    self.save_game(announce=False)
                    self.audio.play("warp")
            self.transition_to(fold, f"Folding to {generate_system(payload)['name']}")
        elif action == "build" and self.started and self.controller.mode == "surface":
            if payload not in BUILDINGS:
                return
            ahead = self.controller.forward()
            ahead.z = 0
            if ahead.length() < .01:
                ahead = Vec3(0, 1, 0)
            ahead.normalize()
            pos = self.controller.position + ahead * 12
            pos.z = self.world.height(pos.x, pos.y)
            records = self.game.bases.get(self.planet["id"], [])
            if any(distance(record["pos"], pos) < 9 for record in records):
                self.toast("Another building is too close. Walk to a new site and build again.", "alert")
                return
            clearance = {"habitat": 4.0, "solar": 3.6, "extractor": 2.3}.get(payload, 1.5)
            if not self._site_clear(pos.x, pos.y, radius=clearance, height=5):
                self.toast("Construction site obstructed or too steep. Aim toward clear, level ground.", "alert")
                return
            ok, message, record = self.game.build(payload, self.planet["id"], list(pos), self.controller.heading)
            if ok:
                self.world.add_building(record)
                self.close_panel()
            self.toast(message, "craft" if ok else "alert")
        elif action == "collect_base" and self.started and self.controller.mode == "surface":
            ok, message = self.game.collect_base_yield(self.planet["id"])
            self.toast(message, "collect" if ok else None)
            self._refresh_panel()
        elif action in ("accept_contract", "claim_contract") and self.started:
            method = self.game.accept_contract if action == "accept_contract" else self.game.claim_contract
            ok, message = method(payload)
            self.toast(message, "discover" if ok else "alert")
            self._refresh_panel()
        elif action == "setting":
            if not isinstance(payload, dict):
                return
            key, value = payload.get("key"), payload.get("value")
            ranges = {"sensitivity": (.04, .5), "volume": (0, 1), "fov": (60, 100),
                      "camera_motion": (0, 1), "mouse_smoothing": (0, 1)}
            if key in ranges and isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                low, high = ranges[key]
                self.game.settings[key] = max(low, min(high, float(value)))
                if key == "volume":
                    self.audio.set_volume(0 if self.no_audio else self.game.settings[key])
                elif key == "fov":
                    self.camLens.setFov(self.game.settings[key])
            elif key in ("invert_y", "mouse_capture", "flight_assist", "ambient_occlusion") and isinstance(value, bool):
                self.game.settings[key] = value
                if key == "ambient_occlusion":
                    self._apply_ambient_occlusion()
            elif key == "quality" and value in ("low", "medium", "high"):
                self.game.settings[key] = value
            self._refresh_panel()

    def run_smoke_checks(self):
        """Exercise real world/state/UI integration without touching a normal save."""
        checks = []
        def check(name, condition):
            if not condition:
                raise AssertionError(name)
            checks.append(name)
        self.new_game()
        check("new expedition renders a populated surface", len(self.world.interactables()) > 10)
        check("spawn is above terrain", self.controller.position.z >= self.world.height(self.controller.position.x, self.controller.position.y) + 1.79)
        for name in ("inventory", "craft", "upgrades", "map", "galaxy", "journal", "discoveries", "contracts", "build", "settings", "help", "pause"):
            self.open_panel(name)
            check("panel opens: " + name, self.ui.panel_open)
        elapsed = self.game.elapsed
        self.step(.1)
        check("menus pause expedition time", self.game.elapsed == elapsed)
        self.close_panel()
        self.scan()
        check("scanner records a nearby specimen", len(self.game.discoveries) >= 2)
        for _ in range(12):
            self.step(1 / 60)
        resource = next(e for e in self.world.interactables() if e["kind"] in ("mineral", "flora"))
        self.target = resource
        self.target_distance = 2
        self.controller.mouse_down = True
        self._mine(10)
        self.controller.mouse_down = False
        check("mining awards units", self.game.stats.get("mined", 0) > 0)
        ok, message = self.game.craft("fuel_cell")
        check("crafting works with starter supplies", ok)
        self.recall_ship()
        self.launch()
        check("boarding enables atmospheric flight", self.controller.mode == "flight")
        self.enter_orbit()
        check("orbital scene contains station and asteroids", any(e["kind"] == "station" for e in self.world.interactables()))
        self.open_panel("map")
        self.action("travel", 1)
        check("planet route starts automatic approach", bool(self.autopilot))
        self.autopilot = None
        self.land_on_planet(1)
        check("planet landing records a visit", len(self.game.visited) == 2)
        check("save written atomically", self.save_game(announce=False) and self.save_path.exists())
        before = dict(self.game.inventory)
        self.continue_game()
        check("save restores cargo and location", self.game.inventory == before and self.game.planet_index == 1)
        self.open_panel("trade", {"name": "QA exchange"})
        self.action("sell", {"item": "carbon", "amount": 1})
        check("trade deducts sold cargo", self.game.inventory.get("carbon", 0) == before.get("carbon", 0) - 1)
        self.close_panel()
        self.land_on_planet(0)
        self.notice = "Expedition systems verified. The next horizon is yours."
        self.notice_time = 8
        self.ui.update(self._view())
        return {"game": "Asterion Expedition", "version": __version__, "status": "passed", "checks": checks,
                "tested": "Linux, Python " + sys.version.split()[0] + ", offscreen rendering", "save_directory": str(self.save_dir)}

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        self.ignoreAll()
        self.taskMgr.remove("expedition-update")
        if self._ao_filters is not None:
            self._ao_filters.cleanup()
            self._ao_filters = None
        self._clear_beam()
        self._remove_ship()
        self.controller.destroy()
        self.effects.destroy()
        self.ui.destroy()
        self.audio.destroy()
        self.world.destroy()
        self.fade.removeNode()
        self.destroy()
