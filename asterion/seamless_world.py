"""One persistent solar scene, with geographic terrain detail streamed into it.

Every visible body and surface object lives in system coordinates.  A change of
reference frame transforms the scene root; it never substitutes a landing map.
Cubed-sphere tile addresses avoid a singular, infinitely dense pole grid, while
saved positions use the public longitude/latitude metre chart.
"""
from __future__ import annotations

import math
import random
from collections import OrderedDict

from panda3d.core import (AmbientLight, BitMask32, DirectionalLight, Fog, Material, Quat,
                          TransparencyAttrib, Vec3)

from .collision import CollisionWorld, MoveResult, _number, _project, _vector
from .celestial_sky import create_sky, create_starfield, create_sun, update_sky
from .content import ITEMS
from .geometry import (Mesh, building_mesh, crystal_mesh, fauna_mesh, flora_mesh,
                       grass_mesh, mix, outpost_mesh, rock_mesh, ruin_mesh,
                       station_mesh)
from .planet_weather import PlanetWeather, fog_profile
from .surface_materials import (configure_surface, terrain_albedo,
                                update_surface_materials)
from .planetary import (PlanetField, PlanetFrame, chart_direction, direction_chart,
                        _SKIN as _TERRAIN_SKIN,
                        frame_to_local, frame_to_world, vector_to_local,
                        vector_to_world)
from .universe import SPACE_SCALE, generate_system
from .world import (CHUNK_SIZE, RESOURCE_NAMES, STYLES, WorldRenderer, _cylinder,
                    _flora_shapes, _placed_shapes, _seed, _station_shapes,
                    _structure_shapes, _xyz)


# (outward normal, chart east, chart north), consistently right handed.
_FACES = ((Vec3(0,0,1), Vec3(1,0,0), Vec3(0,1,0)),
          (Vec3(1,0,0), Vec3(0,0,-1), Vec3(0,1,0)),
          (Vec3(0,0,-1), Vec3(-1,0,0), Vec3(0,1,0)),
          (Vec3(-1,0,0), Vec3(0,0,1), Vec3(0,1,0)),
          (Vec3(0,1,0), Vec3(1,0,0), Vec3(0,0,-1)),
          (Vec3(0,-1,0), Vec3(1,0,0), Vec3(0,0,1)))
_SUN = Vec3(-.68, -.62, .39)
_SUN.normalize()


def _direction(face, x, y, radius):
    return Vec3(*_direction_components(face, x, y, radius))


def _direction_components(face, x, y, radius):
    """Keep metre-scale vertices precise on a tens-of-kilometres body."""
    normal, east, north = _FACES[face]
    value = tuple(normal[k] + east[k] * (x / radius) + north[k] * (y / radius)
                  for k in range(3))
    length = math.sqrt(sum(c * c for c in value))
    return tuple(c / length for c in value)


def _address(direction, radius):
    value = Vec3(direction)
    if value.lengthSquared() < 1e-12:
        value = Vec3(0, 0, 1)
    face = max(range(6), key=lambda i: value.dot(_FACES[i][0]))
    normal, east, north = _FACES[face]
    scale = radius / max(1e-12, value.dot(normal))
    x, y = value.dot(east) * scale, value.dot(north) * scale
    # Positive face edges belong to the final (possibly partial) cell.
    x, y = min(radius - 1e-7, x), min(radius - 1e-7, y)
    return face, math.floor(x / CHUNK_SIZE), math.floor(y / CHUNK_SIZE)


def _tile_id(planet_id, key):
    face, cx, cy = key
    return f"{planet_id}:c{cx},{cy}" if face == 0 else f"{planet_id}:f{face}:c{cx},{cy}"


def _lit(color, normal):
    incidence = max(0., Vec3(normal).dot(_SUN))
    sunlight = incidence ** .82
    # Warm key light and blue skylight keep the software renderer dimensional
    # too. This matches the desktop material's broad illumination response.
    ambient, direct = (.43, .51, .59), (1.12, 1.02, .84)
    lit = tuple(min(1., max(0., color[i] * (ambient[i] + direct[i] * sunlight)))
                for i in range(3))
    return lit + ((color[3],) if len(color) > 3 else ())


def _ground_cover_mesh(flora, ground, accent, seed):
    """Small curved sedges, batched as sparse islands of understory."""
    rng=random.Random(seed)
    mesh=Mesh()
    root_color=mix(flora,ground,.63)
    tip_color=mix(mix(flora,(.48,.51,.28),.35),accent,.09)
    for index in range(7):
        angle=index*2.399963+rng.uniform(-.24,.24)
        ca,sa=math.cos(angle),math.sin(angle)
        h=rng.uniform(.17,.39)
        length=rng.uniform(.23,.53)
        width=rng.uniform(.033,.065)
        base=(ca*.055,sa*.055,.008)
        middle=(ca*length*.52,sa*length*.52,h*.83)
        tip=(ca*length,sa*length,h)
        a=(middle[0]-sa*width,middle[1]+ca*width,middle[2])
        b=(middle[0]+sa*width,middle[1]-ca*width,middle[2])
        ridge=(middle[0],middle[1],middle[2]+width*.18)
        mesh.tri(base,a,ridge,root_color,colors=(root_color,tip_color,tip_color),
                 texcoords=((0,0),(.7,0),(.7,0)))
        mesh.tri(base,ridge,b,root_color,colors=(root_color,tip_color,tip_color),
                 texcoords=((0,0),(.7,0),(.7,0)))
        mesh.tri(a,tip,ridge,tip_color,texcoords=((.7,0),(1,0),(.7,0)))
        mesh.tri(ridge,tip,b,tip_color,texcoords=((.7,0),(1,0),(.7,0)))
    return mesh


class SeamlessWorld:
    """Facade used by walking, flight, mining and construction alike."""

    def __init__(self, app):
        self.app = app
        self._gpu = bool(getattr(getattr(app, "visuals", None), "gpu", False))
        self.root = None
        self.sky_root = None
        self.system = self.state = self.planet = self.frame = None
        self.mode = "orbit"
        self.fields = {}
        self.body_roots = {}
        self.chunks = {}
        self.collisions = CollisionWorld()
        self._space_collisions = CollisionWorld(128)
        self._entities = {}
        self._depleted = set()
        self._buildings = {}
        self._fauna = {}
        self._spinners = []
        self._lights = []
        self._surface_groups = {}
        self._chunk_queue = []
        self._chunk_center = None
        self._near_planet_id = None
        self._model_cache = {}
        self._sample_cache = OrderedDict()
        self._environment = None
        self._stars = None
        self._sky_dome = None
        self._clouds = {}
        self._limbs = {}
        self._weather = {}
        self._lod = {}
        self._terrain_planet_id = None
        self._detail_textures = {}
        self._radius = 2
        self._last_position = Vec3(0, 0, 22)
        self._landmark_root = None
        self._fog = Fog("continuous-atmosphere")
        self._fog.setMode(Fog.MExponential)

    def ensure_system(self, system, state):
        """System changes alone rebuild the solar root; frame changes do not."""
        if self.root is not None and not self.root.isEmpty() and self.system["id"] == system["id"]:
            self.state = state
            self._merge_depletion(state)
            return
        self.destroy()
        self.system, self.state = system, state
        self._merge_depletion(state)
        self.root = self.app.render.attachNewNode("persistent-solar-system")
        self.root.setShaderInput("ae_fog_color", .32, .56, .72)
        self.root.setShaderInput("ae_fog_density", 0.)
        material = Material("survey-matte")
        material.setShininess(0)
        material.setSpecular((0, 0, 0, 1))
        self.root.setMaterial(material)
        ambient = AmbientLight("solar-ambient")
        ambient.setColor((.32, .36, .43, 1))
        sun = DirectionalLight("system-sun")
        sun.setColor((.70, .67, .60, 1))
        a, b = self.root.attachNewNode(ambient), self.root.attachNewNode(sun)
        b.lookAt(-_SUN)
        self._lights = [a, b]
        for light in self._lights:
            self.root.setLight(light)
        self.sky_root = self.root.attachNewNode("fixed-celestial-directions")
        self.sky_root.setFogOff(10)
        self.sky_root.hide(BitMask32.bit(1))
        self._stars = create_starfield(self.sky_root, int(system["id"]) * 773 + 47, 1800)
        self._make_sky()
        # These are distant directions, translated with the observer each frame.
        create_sun(self.sky_root, _SUN, system["star_color"])
        for planet in system["planets"]:
            field = self.fields[planet["id"]] = PlanetField(planet)
            body = self.root.attachNewNode("body-" + planet["id"])
            body.setPos(*planet["position"])
            self.body_roots[planet["id"]] = body
            if self._gpu:
                update_surface_materials(body, field, (0.,0.,0.), 0.)
            self._make_globe(field, body)
            self._make_atmosphere(field, body)
        self._make_space_content(system)
        self.set_frame(None)
        visuals = getattr(self.app, "visuals", None)
        if visuals is not None:
            visuals.bind_world(self)

    def _merge_depletion(self, state):
        for identifiers in getattr(state, "depleted", {}).values():
            self._depleted.update(identifiers)
        for identifier in tuple(self._entities):
            if identifier in self._depleted:
                self.set_depleted(identifier)

    def set_frame(self, frame):
        self.frame = frame
        if self.root is None or self.root.isEmpty():
            return
        if frame is None:
            self.root.setPos(0, 0, 0)
            self.root.setQuat(Quat.identQuat())
        else:
            inverse = Quat(frame.rotation)
            inverse.conjugateInPlace()
            self.root.setQuat(inverse)
            self.root.setPos(inverse.xform(-Vec3(frame.origin)))
        self._refresh_surface_collisions()
        self._refresh_entities()

    def prepare_surface(self, planet, frame, state, position=None):
        if self.system is None or self.system["id"] != planet["system_id"]:
            self.ensure_system(generate_system(planet["system_id"]), state)
        self.state, self.planet = state, planet
        self._merge_depletion(state)
        self._radius = {"low": 2, "medium": 3, "high": 3, "ultra": 4}.get(
            getattr(state, "settings", {}).get("quality", "medium"), 3)
        entering = self._near_planet_id != planet["id"]
        if entering:
            self._clear_surface()
            self._near_planet_id = planet["id"]
            self._models(planet)
        self.set_frame(frame)
        position = _xyz(getattr(state, "position", (0, 0, 22)) if position is None else position)
        self._last_position = Vec3(*position)
        if entering:
            field = self.fields[planet["id"]]
            relative = tuple(self._world_tuple(position)[k] - field._center[k] for k in range(3))
            radial = math.sqrt(sum(c * c for c in relative))
            direction = tuple(c / radial for c in relative) if radial > 1 else (0., 0., 1.)
            altitude = radial - field.radius - field.elevation(direction)
            # Restoring a ground save has no approach frames to build detail.
            # Prepare the footprint during scene setup before showing the player.
            self._stream_lod(direction, field=field, altitude=altitude,
                             budget=512 if altitude < 180 else 16)
            self._terrain_planet_id = field.planet_id
        self._stream(position, budget=1)
        self._ensure_landmarks()
        for record in getattr(state, "bases", {}).get(planet["id"], ()):
            self.add_building(record)

    def set_flight_environment(self, data):
        self._environment = dict(data) if data else None

    def height(self, x, y):
        if self.planet is None or self.frame is None:
            return 20.
        try:
            return self.fields[self.planet["id"]].local_height(self.frame, float(x), float(y))
        except (ValueError, TypeError, OverflowError):
            return 20.

    def interactables(self):
        self._refresh_entities()
        return list(self._entities.values())

    def load_surface(self, planet, state):
        self.ensure_system(generate_system(planet["system_id"]), state)
        self.prepare_surface(planet, PlanetFrame.from_planet(planet), state)
        self.mode = "surface"

    def load_orbit(self, system, state):
        self.ensure_system(system, state)
        self.set_frame(None)
        self.mode = "orbit"

    def destroy(self):
        self.collisions.clear()
        self._space_collisions.clear()
        if self.root is not None and not self.root.isEmpty():
            self.root.removeNode()
        self.root = self.sky_root = None
        self.frame = self.planet = self.system = self.state = None
        self.fields.clear()
        self.body_roots.clear()
        self.chunks.clear()
        self._entities.clear()
        self._surface_groups.clear()
        self._fauna.clear()
        self._buildings.clear()
        self._spinners.clear()
        self._lights.clear()
        self._chunk_queue.clear()
        self._sample_cache.clear()
        self._model_cache.clear()
        self._detail_textures.clear()
        self._lod.clear()
        self._terrain_planet_id = None
        self._clouds.clear()
        self._limbs.clear()
        self._weather.clear()
        self._stars = self._sky_dome = self._environment = None
        self._depleted.clear()
        self._near_planet_id = self._chunk_center = self._landmark_root = None

    def _sample(self, field, face, x, y):
        key = (field.planet["id"], face, round(x, 7), round(y, 7))
        sample = self._sample_cache.get(key)
        if sample is None:
            direction = _direction_components(face, x, y, field.radius)
            height = field.elevation(direction)
            sample = (direction, height, terrain_albedo(field, direction))
            self._sample_cache[key] = sample
            if len(self._sample_cache) > 65000:
                for _ in range(8000):
                    self._sample_cache.popitem(last=False)
        return sample

    def _terrain_mesh(self, field, face, x0, y0, x1, y1, resolution=16,
                      origin=(0., 0., 0.)):
        """One continuous solid envelope, with slope lighting and radial skirts.

        Samples include one neighbour outside each edge so normals are shared
        by equal-level tiles. Coordinates are calculated as doubles and stored
        relative to a tile origin; small surface relief survives a large radius.
        """
        mesh, rows, samples = Mesh(), [], {}
        dx, dy = (x1 - x0) / resolution, (y1 - y0) / resolution
        for j in range(-1, resolution + 2):
            for i in range(-1, resolution + 2):
                x, y = x0 + dx * i, y0 + dy * j
                radial, elevation, color = self._sample(field, face, x, y)
                position = tuple(radial[k] * (field.radius + elevation) - origin[k]
                                 for k in range(3))
                samples[i, j] = (position, radial, color)
        for j in range(resolution + 1):
            row = []
            for i in range(resolution + 1):
                position, radial, color = samples[i, j]
                east = tuple(samples[i + 1, j][0][k] - samples[i - 1, j][0][k]
                             for k in range(3))
                north = tuple(samples[i, j + 1][0][k] - samples[i, j - 1][0][k]
                              for k in range(3))
                normal = Vec3(*east).cross(Vec3(*north))
                if normal.lengthSquared() < 1e-12:
                    normal = Vec3(*radial)
                normal.normalize()
                row.append((position, tuple(normal), color if self._gpu else _lit(color, normal),
                            ((x0 + dx * i) / 12, (y0 + dy * j) / 12), radial))
            rows.append(row)
        for j in range(resolution):
            for i in range(resolution):
                a, b, c, d = rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i]
                for triangle in ((a, b, c), (a, c, d)):
                    mesh.tri(*(p[0] for p in triangle), (1, 1, 1),
                             normals=tuple(p[1] for p in triangle),
                             colors=tuple(p[2] for p in triangle),
                             texcoords=tuple(p[3] for p in triangle))
        # The coarse edge is a chord below its sampled radial surface. Skirts
        # cover that curvature sag as well as differing relief samples; a fixed
        # 12-m skirt could expose open wedges on a much larger coarse tile.
        perimeter = (rows[0] + [row[-1] for row in rows[1:]] +
                     list(reversed(rows[-1][:-1])) + [row[0] for row in reversed(rows[1:-1])])
        step = max(dx, dy)
        relief = field._max_elevation - field._min_elevation
        depth = max(2., min(relief * 2., step * .9) + step * step / field.radius)
        for index, a in enumerate(perimeter):
            b = perimeter[(index + 1) % len(perimeter)]
            da = tuple(a[0][k] - a[4][k] * depth for k in range(3))
            db = tuple(b[0][k] - b[4][k] * depth for k in range(3))
            for triangle in ((a[0], da, db), (a[0], db, b[0])):
                mesh.tri(*triangle, a[2], normals=(a[1], a[1], b[1]),
                         texcoords=(a[3], a[3], b[3]))
        return mesh

    def _make_lod_patch(self, field, key):
        face, level, ix, iy = key
        size = 2 * field.radius / (2 ** level)
        x, y = -field.radius + ix * size, -field.radius + iy * size
        normal = _direction_components(face, x + size * .5, y + size * .5, field.radius)
        origin = tuple(c * (field.radius + field.elevation(normal)) for c in normal)
        # The whole planet remains recognisable before any near detail streams.
        # Close patches need fewer divisions because their physical span shrinks.
        mesh = self._terrain_mesh(field, face, x, y, x + size, y + size,
                                  resolution=24 if level == 0 else 12, origin=origin)
        node = mesh.node(f"spherical-terrain-{face}-{level}-{ix}-{iy}",
                         self.body_roots[field.planet["id"]], unlit=True)
        node.setPos(*origin)
        configure_surface(node, field, origin, gpu=self._gpu)
        cone = max(math.acos(max(-1., min(1., sum(a * b for a, b in zip(normal,
            _direction_components(face, xx, yy, field.radius))))))
            for xx, yy in ((x, y), (x + size, y), (x + size, y + size), (x, y + size)))
        return dict(node=node, bounds=(x, y, x + size, y + size), children=(), pending=(),
                    normal=normal, cone=cone, origin=origin, balance_for=None, wants_refine=False)

    def _make_globe(self, field, parent):
        self._lod[field.planet["id"]] = {
            (face, 0, 0, 0): self._make_lod_patch(field, (face, 0, 0, 0)) for face in range(6)}

    @staticmethod
    def _lod_leaf(records, field, direction):
        """Locate an active leaf, including across a cube-face boundary."""
        face = max(range(6), key=lambda i: sum(direction[k] * _FACES[i][0][k]
                                              for k in range(3)))
        normal, east, north = _FACES[face]
        denominator = max(1e-12, sum(direction[k] * normal[k] for k in range(3)))
        x = field.radius * sum(direction[k] * east[k] for k in range(3)) / denominator
        y = field.radius * sum(direction[k] * north[k] for k in range(3)) / denominator
        key = (face, 0, 0, 0)
        while records[key]["children"]:
            x0, y0, x1, y1 = records[key]["bounds"]
            dx, dy = int(x >= (x0 + x1) * .5), int(y >= (y0 + y1) * .5)
            key = (face, key[1] + 1, key[2] * 2 + dx, key[3] * 2 + dy)
        return key

    def _lod_neighbours(self, records, field, key):
        x0, y0, x1, y1 = records[key]["bounds"]
        epsilon = max(.001, (x1 - x0) * 1e-7)
        neighbours = set()
        for t in (.125, .375, .625, .875):
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            for px, py in ((x0 - epsilon, y), (x1 + epsilon, y),
                           (x, y0 - epsilon), (x, y1 + epsilon)):
                direction = _direction_components(key[0], px, py, field.radius)
                neighbour = self._lod_leaf(records, field, direction)
                if neighbour != key:
                    neighbours.add(neighbour)
        return neighbours

    def _stream_lod(self, direction, budget=1, *, field=None, altitude=0.):
        """Refine by observer distance, including in orbit before any reframe.

        The six root meshes always cover the entire sphere. A complete set of
        four children replaces its parent atomically; no landing disc appears
        and there is never a missing patch while more detail is being built.
        """
        field = field or self.fields[self.planet["id"]]
        records = self._lod[field.planet_id]
        altitude = max(0., altitude)
        horizon = math.acos(min(1., field.radius / (field.radius + altitude + 180.)))
        # At most 1,022 records (including hidden parents) for the active body.
        # Splits are a finite CPU budget and merges release their geometry.
        for _ in range(max(0, int(budget))):
            requests = []
            def visit(key):
                record = records[key]
                x0, y0, x1, y1 = record["bounds"]
                size = x1 - x0
                angle = math.acos(max(-1., min(1., sum(a * b for a, b in
                                                      zip(record["normal"], direction)))))
                visible = angle <= horizon + record["cone"] + .025
                axis, east, north = _FACES[key[0]]
                facing = sum(direction[k] * axis[k] for k in range(3))
                if facing > 1e-8:
                    px = field.radius * sum(direction[k] * east[k] for k in range(3)) / facing
                    py = field.radius * sum(direction[k] * north[k] for k in range(3)) / facing
                    closest = _direction_components(key[0], max(x0, min(x1, px)),
                                                    max(y0, min(y1, py)), field.radius)
                    distance = field.radius * math.sqrt(sum((closest[k] - direction[k]) ** 2
                                                           for k in range(3)))
                else:
                    distance = field.radius * max(0., angle - record["cone"])
                viewing_distance = math.hypot(altitude, distance)
                target_step = max(1.6, viewing_distance * .030)
                resolution = 24 if key[1] == 0 else 12
                hysteresis = .76 if record["children"] else 1.
                refine = (visible and size > 24. and
                          size / resolution > target_step * hysteresis)
                balanced_target = records.get(record["balance_for"], {})
                if (not refine and altitude < field.radius and
                        (record["pending"] or balanced_target.get("wants_refine", False))):
                    refine = True
                if (not refine and record["children"] and altitude < field.radius and
                        any(neighbour[1] > key[1] + 1 for neighbour in
                            self._lod_neighbours(records, field, key))):
                    refine = True
                record["wants_refine"] = refine
                if refine:
                    if record["children"]:
                        for child in record["children"]:
                            visit(child)
                    else:
                        # Cover the horizon and nearby patches before pursuing
                        # centimetric improvements in just one nearest branch.
                        error = size / resolution / max(20., viewing_distance)
                        requests.append((not bool(record["pending"]), -error, distance, angle, key))
                elif record["children"] or record["pending"]:
                    self._merge_lod(records, key)
            for face in range(6):
                visit((face, 0, 0, 0))
            if not requests:
                break
            key = min(requests)[-1]
            # A refined child may only border its own level or one coarser
            # level. Otherwise the coarse chord and tall closing skirt can
            # form a rectangular wall in the ground-level horizon.
            while not records[key]["pending"]:
                coarser = [neighbour for neighbour in self._lod_neighbours(records, field, key)
                           if neighbour[1] < key[1]]
                if not coarser:
                    break
                target = key
                records[target]["wants_refine"] = True
                key = min(coarser, key=lambda neighbour: (neighbour[1], neighbour))
                records[key]["balance_for"] = target
            if not records[key]["pending"] and len(records) + 4 > 1022:
                break
            face, level, ix, iy = key
            children = tuple((face, level + 1, ix * 2 + dx, iy * 2 + dy)
                             for dy in range(2) for dx in range(2))
            # Only one child mesh is built per work item. It remains hidden
            # until all siblings are ready, keeping CPU frame work predictable.
            child = children[len(records[key]["pending"])]
            records[child] = self._make_lod_patch(field, child)
            records[child]["node"].hide()
            records[key]["pending"] += (child,)
            if len(records[key]["pending"]) == 4:
                records[key]["children"] = children
                records[key]["pending"] = ()
                records[key]["node"].hide()
                for child in children:
                    records[child]["node"].show()

    def _update_terrain(self, observer, field):
        """Stream the approach planet without requiring a surface frame."""
        if self._terrain_planet_id != field.planet_id:
            previous = self._lod.get(self._terrain_planet_id, {})
            for face in range(6):
                if (face, 0, 0, 0) in previous:
                    self._merge_lod(previous, (face, 0, 0, 0))
            self._terrain_planet_id = field.planet_id
        relative = tuple(float(observer[k]) - field._center[k] for k in range(3))
        radial = math.sqrt(sum(c * c for c in relative))
        direction = tuple(c / radial for c in relative) if radial > 1 else (0., 0., 1.)
        altitude = radial - field.radius - field.elevation(direction)
        self._stream_lod(direction, field=field, altitude=altitude, budget=1)

    def _merge_lod(self, records, key):
        for child in records[key]["children"] + records[key]["pending"]:
            self._merge_lod(records, child)
            records.pop(child)["node"].removeNode()
        records[key]["children"] = ()
        records[key]["pending"] = ()
        records[key]["node"].show()

    def _make_space_content(self, system):
        solid, glow = station_mesh()
        station = self.root.attachNewNode("orbital-survey-exchange")
        solid.node("station-hull", station, two_sided=True)
        glow.node("station-lights", station, two_sided=True, unlit=True)
        station.setPos(*system["station"])
        station.setH(-12)
        identifier = f"s{system['id']}:station"
        self._entities[identifier] = dict(id=identifier, kind="station",
            name=system["name"] + " orbital exchange", pos=tuple(system["station"]),
            world_pos=tuple(system["station"]), radius=145, node=station)
        self._space_collisions.set_group(identifier, _placed_shapes(_station_shapes(), identifier,
                                                                  system["station"], -12))
        rng = random.Random(8011 + int(system["id"]) * 1009)
        for i in range(95):
            identifier = f"s{system['id']}:asteroid:{i}"
            angle, distance = rng.uniform(0, math.tau), rng.uniform(400, 1950)
            x, y = math.sin(angle) * distance, math.cos(angle) * distance
            z = rng.uniform(-320, 440)
            if i < 7:
                x, y, z = rng.uniform(-120, 180), rng.uniform(210, 520), rng.uniform(-90, 110)
            position = Vec3(x, y, z) * SPACE_SCALE
            if (position - Vec3(*system["station"])).length() < 210:
                continue
            radius = rng.uniform(6, 22)
            resource = ("ferrite", "gold", "cobalt", "crystal")[i % 4]
            mesh = rock_mesh(mix((.29, .32, .39), ITEMS[resource]["color"], .16),
                             i + int(system["id"]) * 999, radius)
            hpr = tuple(rng.uniform(0, 360) for _ in range(3))
            amount, speed = 25 + rng.randrange(30), rng.uniform(-1.1, 1.1)
            if identifier in self._depleted:
                continue
            node = mesh.node("asteroid", self.root)
            node.setPos(position)
            node.setHpr(*hpr)
            self._entities[identifier] = dict(id=identifier, kind="asteroid",
                name="Drifting " + resource + " asteroid", pos=tuple(position),
                world_pos=tuple(position), radius=radius * 1.4, node=node,
                resource=resource, amount=amount, hardness=1.)
            self._space_collisions.set_group(identifier, [dict(id=identifier, type="sphere",
                center=tuple(position), radius=radius * 1.42)])
            self._spinners.append((node, speed))

    def _models(self, planet):
        seed = int(planet["seed"])
        self._model_cache.clear()
        self._style = STYLES.get(planet["biome"], "fan")
        self._tree_models = [flora_mesh(self._style, planet["flora"], planet["accent"], seed + i)
                             for i in range(3)]
        self._grass_models = [grass_mesh(planet["flora"], planet["accent"], seed + i * 29)
                              for i in range(3)]
        self._rock_models = [rock_mesh(mix(planet["ground"], (.32, .35, .38), .40), seed + i * 77)
                            for i in range(3)]
        self._cover_models = [_ground_cover_mesh(planet["flora"],planet["ground"],
                               planet["accent"],seed+i*83) for i in range(3)]
        self._pebble_models=[]
        for i in range(3):
            pebble=Mesh()
            pebble.sphere((0.,0.,.05),(.19,.12,.10),
                mix(planet["ground"],(.49,.44,.36),.57),7,4,.32,seed+i*57,
                smooth=True)
            self._pebble_models.append(pebble)
        # Build vertex buffers only once. Each streamed chunk is combined by
        # Panda's native scene reducer instead of transforming millions of
        # individual vertices through Python at every boundary crossing.
        for category in ("tree","grass","rock","cover","pebble"):
            models=getattr(self,"_"+category+"_models")
            self._model_cache[category]=[
                mesh.node("scenery-template-"+category,two_sided=True)
                for mesh in models]
        # Pay the small, bounded art upload cost while preparing the planet,
        # rather than stopping a walking frame for a new resource silhouette.
        for resource in planet["resources"]:
            for variant in range(5):
                self._resource_template(resource,variant)
        for variant in range(3):
            self._fauna_template(variant)

    def _clear_surface(self):
        for key in tuple(self.chunks):
            self._remove_chunk(key)
        for identifier in tuple(self._surface_groups):
            self.collisions.remove_group(identifier)
        self._surface_groups.clear()
        for identifier, entity in tuple(self._entities.items()):
            if "planet_id" in entity:
                if not entity["node"].isEmpty():
                    entity["node"].removeNode()
                self._entities.pop(identifier, None)
        if self._landmark_root is not None and not self._landmark_root.isEmpty():
            self._landmark_root.removeNode()
        self._landmark_root = None
        self._fauna.clear()
        self._buildings.clear()
        self._chunk_queue.clear()
        self._chunk_center = None
        if self._near_planet_id and hasattr(self, "_lod"):
            records = self._lod.get(self._near_planet_id, {})
            for face in range(6):
                if (face, 0, 0, 0) in records:
                    self._merge_lod(records, (face, 0, 0, 0))

    def _wanted_chunks(self, direction):
        field = self.fields[self.planet["id"]]
        face, cx, cy = _address(direction, field.radius)
        # At a cube corner a face metre is shorter on the sphere.  Expand the
        # sampling window there, then retain the closest bounded set of cells.
        radius = self._radius + 1
        wanted = set()
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                normal = _direction(face, (cx + dx + .5) * CHUNK_SIZE,
                                    (cy + dy + .5) * CHUNK_SIZE, field.radius)
                wanted.add(_address(normal, field.radius))
        return wanted

    def _stream(self, position, budget=1, initial=False):
        if self.planet is None or self.frame is None:
            return
        field = self.fields[self.planet["id"]]
        direction = frame_to_world(self.frame, position) - field.center
        if direction.lengthSquared() < 1:
            direction = Vec3(self.frame.normal)
        direction.normalize()
        center = _address(direction, field.radius)
        if center != self._chunk_center:
            self._chunk_center = center
            wanted = self._wanted_chunks(direction)
            for key in tuple(self.chunks):
                if key not in wanted:
                    self._remove_chunk(key)
            def distance(key):
                normal = _direction(key[0], (key[1] + .5) * CHUNK_SIZE,
                                    (key[2] + .5) * CHUNK_SIZE, field.radius)
                return (normal - direction).lengthSquared()
            self._chunk_queue = sorted(wanted - self.chunks.keys(), key=distance)
        count = len(self._chunk_queue) if initial else budget
        for _ in range(min(count, len(self._chunk_queue))):
            self._make_chunk(self._chunk_queue.pop(0))

    def _remove_chunk(self, key):
        chunk = self.chunks.pop(key)
        for identifier in chunk["ids"]:
            self._entities.pop(identifier, None)
            self._fauna.pop(identifier, None)
            self._surface_groups.pop(identifier, None)
            self.collisions.remove_group(identifier)
        self._surface_groups.pop(chunk["collision_group"], None)
        self.collisions.remove_group(chunk["collision_group"])
        chunk["root"].removeNode()

    def _geo(self, direction, extra=0):
        field = self.fields[self.planet["id"]]
        u, v = direction_chart(direction, field.radius)
        # Surface props originate on dry terrain or the seabed, rather than
        # sprouting on the newly collidable ocean envelope.
        return (u, v, field.seabed_elevation(direction) + extra)

    def _pose(self, node, geo, heading=0):
        field = self.fields[self.planet["id"]]
        direction = chart_direction(geo[0], geo[1], field.radius)
        tangent = PlanetFrame.from_planet(self.planet, direction)
        node.setPos(direction * (field.radius + geo[2]))
        rotation = Quat()
        rotation.setHpr(Vec3(heading, 0, 0))
        node.setQuat(rotation * tangent.rotation)
        return tangent

    def _register(self, identifier, node, geo, kind, name, radius, **data):
        self._entities[identifier] = dict(id=identifier, node=node, geo=tuple(geo),
            planet_id=self.planet["id"], kind=kind, name=name, radius=radius,
            pos=(0, 0, 0), **data)
        self._refresh_entity(self._entities[identifier])

    def _refresh_entity(self, entity):
        if "planet_id" in entity:
            field = self.fields[entity["planet_id"]]
            u, v, height = entity["geo"]
            direction = chart_direction(u, v, field.radius)
            world = field.center + direction * (field.radius + height + entity.get("_offset", .7))
            entity["world_pos"] = tuple(world)
        entity["pos"] = tuple(frame_to_local(self.frame, entity["world_pos"]))

    def _refresh_entities(self):
        for entity in self._entities.values():
            self._refresh_entity(entity)

    def _shape_records(self, identifier, shapes, geo, heading=0, scale=1):
        field = self.fields[self.planet["id"]]
        direction = chart_direction(geo[0], geo[1], field.radius)
        tangent = PlanetFrame.from_planet(self.planet, direction)
        origin = field.center + direction * (field.radius + geo[2])
        output = []
        for shape in _placed_shapes(shapes, identifier, (0, 0, 0), heading, scale):
            center = origin + tangent.vector_to_world(shape["center"])
            record = dict(shape, world_center=tuple(center), planet_id=self.planet["id"])
            angle = math.radians(shape.get("heading", heading))
            record["world_right"] = tuple(tangent.vector_to_world((math.cos(angle), math.sin(angle), 0)))
            record["world_up"] = tuple(direction)
            output.append(record)
        return output

    def _set_surface_group(self, identifier, records):
        self._surface_groups[identifier] = records
        self._refresh_surface_group(identifier, records)

    def _refresh_surface_group(self, identifier, records):
        if self.frame is None:
            self.collisions.remove_group(identifier)
            return
        shapes = []
        for record in records:
            if record["planet_id"] != self.frame.planet_id:
                continue
            center = frame_to_local(self.frame, record["world_center"])
            # Public capsule colliders describe the close tangent neighborhood.
            # Other hemispheres are handled by their actual spherical terrain.
            if abs(center.x) > 1800 or abs(center.y) > 1800 or abs(center.z) > 600:
                continue
            shape = {key: value for key, value in record.items()
                     if key not in ("world_center", "world_right", "world_up", "planet_id")}
            shape["center"] = tuple(center)
            right = vector_to_local(self.frame, record["world_right"])
            shape["heading"] = math.degrees(math.atan2(right.y, right.x))
            shapes.append(shape)
        self.collisions.set_group(identifier, shapes)

    def _refresh_surface_collisions(self):
        for identifier, records in self._surface_groups.items():
            self._refresh_surface_group(identifier, records)

    def _append_decor(self, target, source, geo, scale=1, heading=0,
                      origin_offset=(0.,0.,0.)):
        field = self.fields[self.planet["id"]]
        direction = chart_direction(geo[0], geo[1], field.radius)
        tangent = PlanetFrame.from_planet(self.planet, direction)
        ca, sa = math.cos(math.radians(heading)), math.sin(math.radians(heading))
        right = tangent.right * ca + tangent.north * sa
        north = tangent.north * ca - tangent.right * sa
        up = tangent.normal
        origin = tuple(float(direction[k])*(field.radius+geo[2])-origin_offset[k]
                       for k in range(3))
        axes = (tuple(right), tuple(north), tuple(up))
        # Texcoord.x is a botanical flexibility weight for the scene shader.
        # Preserve it through batching; inert stones explicitly carry zero.
        if source.texcoords and not target.texcoords:
            target.texcoords.extend(((0.,0.),)*len(target.vertices))
        if target.texcoords or source.texcoords:
            target.texcoords.extend(source.texcoords or ((0.,0.),)*len(source.vertices))
        rx,ry,rz=axes[0]
        nx,ny,nz=axes[1]
        ux,uy,uz=axes[2]
        ox,oy,oz=origin
        for (x,y,z),(a,b,c) in zip(source.vertices,source.normals):
            target.vertices.append((ox+scale*(x*rx+y*nx+z*ux),
                                    oy+scale*(x*ry+y*ny+z*uy),
                                    oz+scale*(x*rz+y*nz+z*uz)))
            target.normals.append((a*rx+b*nx+c*ux,a*ry+b*ny+c*uy,a*rz+b*nz+c*uz))
        target.colors.extend(source.colors)

    def _instance_decor(self, parent, category, variant, geo, scale, heading,
                         origin_offset):
        """Place a shared vertex buffer in the chunk's precise local frame."""
        source=self._model_cache[category][variant%3]
        node=source.copyTo(parent)
        field=self.fields[self.planet["id"]]
        direction=chart_direction(geo[0],geo[1],field.radius)
        tangent=PlanetFrame.from_planet(self.planet,direction)
        node.setPos(*(float(direction[k])*(field.radius+geo[2])-origin_offset[k]
                      for k in range(3)))
        rotation=Quat()
        rotation.setHpr(Vec3(heading,0,0))
        node.setQuat(rotation*tangent.rotation)
        node.setScale(scale)
        return node

    def _in_clearance(self, geo, padding=0):
        u, v = geo[:2]
        return (math.hypot(u, v) < 9 + padding or
                math.hypot(u - 32, v - 62) < 13 + padding or
                math.hypot(u + 86, v - 108) < 14 + padding)

    def _make_chunk(self, key):
        face, cx, cy = key
        field = self.fields[self.planet["id"]]
        prefix = _tile_id(self.planet["id"], key)
        root = self.body_roots[self.planet["id"]].attachNewNode("geographic-chunk-" + prefix)
        group, ids = "chunk:" + prefix, []
        self.chunks[key] = dict(root=root, ids=ids, collision_group=group)
        # Face zero retains the original home-region generator seed and IDs.
        seed = _seed(int(self.planet["seed"]), cx, cy, face * 101)
        rng = random.Random(seed)
        x0, y0 = max(-field.radius, cx * CHUNK_SIZE), max(-field.radius, cy * CHUNK_SIZE)
        x1, y1 = min(field.radius, (cx + 1) * CHUNK_SIZE), min(field.radius, (cy + 1) * CHUNK_SIZE)
        def location(border=0):
            x, y = x0 + (border + rng.random() * (1 - border * 2)) * (x1 - x0), y0 + (border + rng.random() * (1 - border * 2)) * (y1 - y0)
            return self._geo(_direction(face, x, y, field.radius))
        center = self._geo(_direction(face, (x0 + x1) * .5, (y0 + y1) * .5, field.radius))
        remote = (cx % 7 == 3 and cy % 7 == 4 and (face != 0 or abs(cx) + abs(cy) > 4)
                  and center[2] > field.planet["water_level"] + 1)
        def clearance(geo, padding=0):
            return self._in_clearance(geo, padding) or (remote and math.hypot(geo[0] - center[0], geo[1] - center[1]) < 14 + padding)
        decor=root.attachNewNode("batched-local-flora-and-stones")
        shapes = []
        center_direction=chart_direction(center[0],center[1],field.radius)
        decor_origin=tuple(float(c)*(field.radius+center[2]) for c in center_direction)
        decor.setPos(*decor_origin)
        abundance = .6 if self.planet["biome"] in ("desert", "volcanic", "frozen") else 1.
        for i in range(int(12 * abundance)):
            geo = location()
            if clearance(geo, 1) or geo[2] < field.planet["water_level"] + .5:
                continue
            scale, heading = rng.uniform(.75, 1.8), rng.uniform(0, 360)
            if self._style == "mushroom":
                scale *= 1.25
            self._instance_decor(decor,"tree",i,geo,scale,heading,decor_origin)
            shapes.extend(self._shape_records(prefix + f":tree{i}",
                _flora_shapes(self._style, int(self.planet["seed"]) + i % 3), geo, heading, scale))
        for i in range(int(34 * abundance)):
            geo = location()
            if clearance(geo) or geo[2] < field.planet["water_level"]:
                continue
            self._instance_decor(decor,"grass",i,geo,rng.uniform(.65,1.7),rng.uniform(0,360),decor_origin)
        for i in range(9):
            geo = location()
            if clearance(geo):
                continue
            scale, heading = rng.uniform(.4, 1.7), rng.uniform(0, 360)
            self._instance_decor(decor,"rock",i,geo,scale,heading,decor_origin)
            if scale > .62:
                shapes.extend(self._shape_records(prefix + f":rock{i}",
                    [_cylinder("stone", (.12, 0, .53), .84, 1.35)], geo, heading, scale))
        decor.flattenStrong()
        self._set_surface_group(group, shapes)
        for i in range(7):
            geo = location(.13)
            if clearance(geo, 2) or geo[2] < field.planet["water_level"] + .3:
                continue
            resources = self.planet["resources"]
            resource = resources[rng.randrange(len(resources))]
            identifier = prefix + f":r{i}"
            if self._resource(identifier, resource, geo, _seed(seed, cx, cy, i + 1), root):
                ids.append(identifier)
        if rng.random() < .34 * float(self.planet.get("fauna_density", 1)):
            if not clearance(center, 5) and center[2] > field.planet["water_level"] + 1:
                identifier = prefix + ":fauna"
                self._make_fauna(identifier, center, _seed(seed, cx, cy, 49), root)
                ids.append(identifier)
        if remote:
            kind = "outpost" if seed % 3 == 0 else "ruin"
            identifier = prefix + ":" + kind
            self._structure(identifier, kind, center, (seed % 4) * 90, root,
                            "Remote survey exchange" if kind == "outpost" else "Meridian listening halo")
            ids.append(identifier)
        # An isolated random stream adds visual ground cover without shifting
        # any pre-existing resource, animal, tree or saved-world identity.
        micro_rng=random.Random(seed^0xC0FE146)
        cover=root.attachNewNode("batched-low-ground-cover")
        cover.setPos(*decor_origin)
        quality=getattr(self.state,"settings",{}).get("quality","medium")
        cover_count={"low":64,"medium":144,"high":192,"ultra":256}.get(quality,144)
        vegetation=field.biome not in ("volcanic","frozen","desert")
        cluster=None
        for i in range(cover_count if vegetation else cover_count//5):
            if i%5==0:
                cluster=(micro_rng.uniform(x0,x1),micro_rng.uniform(y0,y1))
            x=max(x0,min(x1,cluster[0]+micro_rng.uniform(-2.2,2.2)))
            y=max(y0,min(y1,cluster[1]+micro_rng.uniform(-2.2,2.2)))
            geo=self._geo(_direction(face,x,y,field.radius))
            if clearance(geo,.4) or geo[2]<field.water_level+.5:
                continue
            self._instance_decor(cover,"cover",i,geo,
                micro_rng.uniform(.64,1.45),micro_rng.uniform(0,360),decor_origin)
        for i in range(18):
            geo=self._geo(_direction(face,micro_rng.uniform(x0,x1),
                                     micro_rng.uniform(y0,y1),field.radius))
            if clearance(geo,.2) or geo[2]<field.water_level+.15:
                continue
            self._instance_decor(cover,"pebble",i,geo,
                micro_rng.uniform(.45,1.1),micro_rng.uniform(0,360),decor_origin)
        cover.flattenStrong()

    def _resource_template(self, resource, seed):
        """One immutable art buffer; variation is independent of visit order."""
        key=("resource",resource,seed%5)
        if key not in self._model_cache:
            color=ITEMS.get(resource,{}).get("color",self.planet["accent"])
            visual_seed=int(self.planet["seed"])+sum(ord(c) for c in resource)*131+key[2]*977
            if resource == "carbon":
                mesh=flora_mesh(self._style,self.planet["flora"],self.planet["accent"],visual_seed)
            elif resource in ("oxygen","sodium"):
                mesh=flora_mesh("succulent",mix(color,self.planet["flora"],.3),color,visual_seed)
            elif resource in ("crystal","cobalt","silicon","copper"):
                mesh=crystal_mesh(color,visual_seed)
            else:
                mesh=rock_mesh(mix(color,self.planet["ground"],.4),visual_seed)
            self._model_cache[key]=mesh.node("resource-"+resource,two_sided=True)
        return self._model_cache[key]

    def _resource(self, identifier, resource, geo, seed, parent):
        if identifier in self._depleted:
            return False
        rng = random.Random(seed)
        plant = resource in ("carbon", "oxygen", "sodium")
        if resource == "carbon":
            scale, radius, amount, hardness = .6, 1.7, 18 + rng.randrange(15), 1.1
            shapes = _flora_shapes(self._style, seed)
        elif resource in ("oxygen", "sodium"):
            scale, radius, amount, hardness = .4, 1., 10 + rng.randrange(9), .75
            shapes = _flora_shapes("succulent", seed)
        elif resource in ("crystal", "cobalt", "silicon", "copper"):
            scale = .6 + rng.random() * .35
            shapes = [_cylinder("deposit", (0, 0, 1.15), .73, 2.3)]
            radius, amount, hardness = 1.7, 12 + rng.randrange(17), 1.6
        else:
            scale = 1 + rng.random() * .3
            shapes = [_cylinder("deposit", (.12, 0, .56), .91, 1.4)]
            radius, amount, hardness = 1.65, 20 + rng.randrange(16), 1.25
        # Preserve all gameplay RNG draws above. A bounded, deterministic set
        # of art variants shares the expensive mesh buffers during streaming.
        node=self._resource_template(resource,seed).copyTo(parent)
        node.setScale(scale)
        heading = rng.uniform(0, 360)
        self._pose(node, geo, heading)
        self._register(identifier, node, geo, "flora" if plant else "mineral",
                       RESOURCE_NAMES.get(resource, resource.title() + " deposit"), radius,
                       resource=resource, amount=amount, hardness=hardness, _offset=.6)
        records = self._shape_records(identifier, shapes, geo, heading, scale)
        if len(records) == 1:
            records[0]["id"] = identifier
        self._set_surface_group(identifier, records)
        return True

    def _structure(self, identifier, kind, geo, heading, parent, name):
        node = parent.attachNewNode(kind)
        key=("structure",kind)
        if key not in self._model_cache:
            meshes = outpost_mesh(self.planet["accent"]) if kind == "outpost" else ruin_mesh(self.planet["accent"])
            self._model_cache[key]=(meshes[0].node(kind+"-structure",two_sided=True),
                                    meshes[1].node(kind+"-signals",two_sided=True,unlit=True))
        for template in self._model_cache[key]:
            template.copyTo(node)
        self._pose(node, geo, heading)
        self._register(identifier, node, geo, kind, name, 11, _offset=1.4)
        self._set_surface_group(identifier, self._shape_records(identifier, _structure_shapes(kind), geo, heading))

    def _fauna_template(self, seed):
        key=("fauna",seed%3)
        if key not in self._model_cache:
            self._model_cache[key]=fauna_mesh(mix(self.planet["flora"],(.7,.66,.47),.45),
                    self.planet["accent"],seed%3).node("lantern-grazer",two_sided=True)
        return self._model_cache[key]

    def _make_fauna(self, identifier, geo, seed, parent):
        rng = random.Random(seed)
        size, heading = rng.uniform(.75, 1.3), rng.uniform(0, 360)
        node=self._fauna_template(seed).copyTo(parent)
        node.setScale(size)
        self._pose(node, geo, heading)
        names = ("Lantern grazer", "Ribbon drifter", "Prism shellback")
        descriptions = ("A peaceful six-legged browser carrying bioluminescent antennae.",
                        "A gentle filter feeder riding warm currents on layered ribbon fins.",
                        "A quiet forager whose shell plates collect trace minerals.")
        self._register(identifier, node, geo, "fauna", names[seed % 3], 1.8 * size,
                       description=descriptions[seed % 3], _offset=1.1 * size)
        self._fauna[identifier] = dict(origin=tuple(geo), phase=rng.random() * math.tau,
                                      size=size, variant=seed % 3)

    def _ensure_landmarks(self):
        if self._landmark_root is not None and not self._landmark_root.isEmpty():
            return
        parent = self._landmark_root = self.body_roots[self.planet["id"]].attachNewNode("canonical-home-landmarks")
        field = self.fields[self.planet["id"]]
        for kind, name, u, v in (("outpost", "Wayfarer survey exchange", 32, 62),
                                  ("ruin", "The Unfinished Halo", -86, 108)):
            geo = self._geo(chart_direction(u, v, field.radius))
            self._structure(f"{self.planet['id']}:{kind}:landing", kind, geo, 0, parent, name)
        for i, (resource, u, v) in enumerate((("ferrite", -7, 14), ("carbon", 10, 20),
                 ("oxygen", -13, 27), ("sodium", 16, 31), ("copper", -22, 39), ("crystal", 23, 46))):
            self._resource(f"{self.planet['id']}:landing:r{i}", resource,
                           self._geo(chart_direction(u, v, field.radius)),
                           int(self.planet["seed"]) + i * 97, parent)
        self._make_fauna(f"{self.planet['id']}:landing:fauna",
                         self._geo(chart_direction(-17, 51, field.radius)), int(self.planet["seed"]) + 622, parent)
        marker = Mesh()
        marker.ring((0, 0, .025), 5.3, 5.4, mix(self.planet["ground"], (.84, .9, .86), .45), 40)
        node = marker.node("landing-site-marker", parent)
        self._pose(node, (0, 0, field.elevation((0, 0, 1))))

    def set_depleted(self, identifier):
        self._depleted.add(identifier)
        entity = self._entities.pop(identifier, None)
        self._fauna.pop(identifier, None)
        self._buildings.pop(identifier, None)
        self._surface_groups.pop(identifier, None)
        self.collisions.remove_group(identifier)
        self._space_collisions.remove_group(identifier)
        if entity and not entity["node"].isEmpty():
            entity["node"].removeNode()

    def add_building(self, record):
        if self.planet is None or not isinstance(record, dict):
            return
        identifier = record.get("id")
        if not identifier or identifier in self._buildings or identifier in self._depleted:
            return
        field = self.fields[self.planet["id"]]
        u, v, _ = _xyz(record.get("pos", (0, 0, 20)))
        # Construction places foundations on the current solid surface. Legacy
        # seabed elevations must not hide a saved base below the ocean envelope.
        # Only the runtime pose changes; the saved record and progress stay intact.
        elevation = field.elevation(chart_direction(u, v, field.radius))
        geo = (u, v, elevation)
        kind, heading = record.get("kind", "beacon"), float(record.get("heading", 0))
        solid, glow = building_mesh(kind, self.planet["accent"])
        node = self.body_roots[self.planet["id"]].attachNewNode("constructed-" + kind)
        solid.node(kind + "-structure", node, two_sided=True)
        glow.node(kind + "-indicators", node, two_sided=True, unlit=True)
        self._pose(node, geo, heading)
        self._buildings[identifier] = node
        self._register(identifier, node, geo, "beacon", {"beacon": "Expedition beacon",
            "habitat": "Field habitat", "solar": "Solar array", "extractor": "Mineral extractor"}.get(kind, kind),
            4 if kind == "habitat" else 3, building_kind=kind, _offset=1.3)
        self._set_surface_group(identifier, self._shape_records(identifier, _structure_shapes(kind), geo, heading))

    def _make_sky(self):
        """A radial sky backdrop; distant star directions remain system-fixed."""
        self._sky_dome = create_sky(self.sky_root, self._gpu)

    def _make_atmosphere(self, field, body):
        """Depth-bearing cloud weather and atmospheric limb share this body."""
        weather = self._weather[field.planet_id] = PlanetWeather(field, body, gpu=self._gpu)
        self._clouds[field.planet_id] = weather.clouds
        self._limbs[field.planet_id] = weather.limb

    def update(self, dt, local_camera_position, elapsed, storm=0):
        """Stream a finite amount of detail without changing any flight state."""
        if self.root is None or self.root.isEmpty():
            return
        dt = _number(dt, 0., 0., .2)
        elapsed = _number(elapsed, 0., 0., 1e9)
        storm = _number(storm, 0., 0., 1.)
        position = Vec3(*_xyz(local_camera_position))
        self._last_position = position
        observer = frame_to_world(self.frame, position)
        # Only the sky's origin follows the observer. Its axes stay galactic,
        # so a reframe cannot rotate stars or move a moon/planet with the ship.
        self.sky_root.setPos(observer)
        if self.planet is not None and self.frame is not None:
            self._stream(position, budget=1)

        nearest = min(self.fields.values(), key=lambda field: field.altitude(observer))
        self._update_terrain(observer, nearest)
        data = nearest.atmosphere(observer)
        if self._environment and self._environment.get("planet_id") == nearest.planet_id:
            # Altitude is always derived from this camera pose; externally
            # supplied dynamics are used only for the matching body's weather.
            data.update({key: value for key, value in self._environment.items()
                         if key in ("heat", "entry", "exit", "radial_speed")})
        self.environment = data
        density = max(0., min(1., data["density"]))
        sky_alpha = density ** .38 if density else 0.
        normal = observer - nearest.center
        if normal.lengthSquared() < 1:
            normal = Vec3(0, 0, 1)
        normal.normalize()
        daylight = .22 + .78 * max(0., normal.dot(_SUN)) ** .55
        tone = tuple(c * (1 - storm * .35) * daylight for c in nearest.planet["sky"][:3])
        self._sky_dome.setQuat(PlanetFrame.from_planet(nearest.planet, normal).rotation)
        sun_local = self._sky_dome.getQuat().conjugate().xform(_SUN)
        update_sky(self._sky_dome, tone, sky_alpha, sun_local, daylight, elapsed)
        self._stars.setColorScale(1, 1, 1, 1 - sky_alpha * (.35 + .65 * daylight))
        # Fog fades with the same continuous density as the backdrop. Distant
        # bodies remain in this scene and naturally reappear during ascent.
        local_cloud = self._weather[nearest.planet_id].local_density(observer)
        data["local_cloud"] = local_cloud
        fog_color, fog_density = fog_profile(data, tone, local_cloud, storm)
        self._fog.setColor(*fog_color[:3])
        self._fog.setExpDensity(fog_density)
        self.root.setShaderInput("ae_fog_color", *fog_color[:3])
        self.root.setShaderInput("ae_fog_density", float(fog_density))
        if self._gpu:
            for field in self.fields.values():
                update_surface_materials(self.body_roots[field.planet_id], field, observer, elapsed)
        if fog_density > 0:
            self.root.setFog(self._fog)
        else:
            self.root.clearFog()
        self.app.setBackgroundColor(.004, .008, .019, 1)
        for weather in self._weather.values():
            weather.update(observer, storm)

        for identifier, animation in tuple(self._fauna.items()):
            entity = self._entities.get(identifier)
            if entity is None or entity["planet_id"] != self._near_planet_id:
                continue
            if (Vec3(*entity["world_pos"]) - observer).lengthSquared() > 220 ** 2:
                continue
            field = self.fields[entity["planet_id"]]
            u0, v0, _ = animation["origin"]
            # Walk in a tangent basis around the saved origin, avoiding the
            # longitude singularity even at a true geographic pole.
            origin_direction = chart_direction(u0, v0, field.radius)
            tangent = PlanetFrame.from_planet(field.planet, origin_direction)
            phase = elapsed * .105 + animation["phase"]
            direction = origin_direction * field.radius + tangent.right * (math.cos(phase) * 4.2) + tangent.north * (math.sin(phase) * 3.5)
            direction.normalize()
            hover = (.9 + math.sin(elapsed * 1.4 + animation["phase"]) * .28
                     if animation["variant"] == 1 else 0.)
            geo = self._geo(direction, hover + math.sin(elapsed * 3 + animation["phase"]) * .045)
            entity["geo"] = geo
            self._pose(entity["node"], geo, -math.degrees(phase))
            self._refresh_entity(entity)
        self._spinners[:] = [(node, speed) for node, speed in self._spinners if not node.isEmpty()]
        for node, speed in self._spinners:
            node.setH(node.getH() + dt * speed)

    def _world_tuple(self, point, vector=False):
        """Keep collision arithmetic in doubles across a floating origin."""
        if self.frame is None:
            return tuple(point)
        return tuple((0. if vector else self.frame._origin[i]) +
                     sum(self.frame._basis[j][i] * point[j] for j in range(3))
                     for i in range(3))

    def _local_tuple(self, point, vector=False):
        if self.frame is None:
            return tuple(point)
        relative = tuple(point[i] - (0. if vector else self.frame._origin[i]) for i in range(3))
        return tuple(sum(relative[i] * axis[i] for i in range(3)) for axis in self.frame._basis)

    def move_sphere(self, position, displacement, radius=3):
        """One earliest-hit sweep against terrain, nearby props and orbital solids.

        All casts inspect the same requested segment before choosing a hit.
        Sweeping these worlds successively would miss a thin object on a
        terrain slide, or project an object's slide through the terrain.
        The shared narrow-phase casts and plane projection preserve the
        collision module's finite budgets and simultaneous-corner behavior.
        """
        point, delta = _vector(position), _vector(displacement)
        radius = _number(radius, 3., .001, 1000.)
        normals, ids = [], []
        def remember(normal, identifiers):
            normal = tuple(normal)
            if not any(sum(a * b for a, b in zip(old, normal)) > .9998 for old in normals):
                normals.append(normal)
            for identifier in identifiers:
                if identifier not in ids:
                    ids.append(identifier)

        # Recover a spawn or newly streamed solid before starting the cast.
        # Repeating the three domains is bounded and only does work in contact.
        for _ in range(3):
            before = point
            point, contacts, identifiers = self.collisions._recover(point, radius, 0., ())
            for normal in contacts:
                remember(normal, identifiers)
            global_point = self._world_tuple(point)
            recovered, contacts, identifiers = self._space_collisions._recover(global_point, radius, 0., ())
            if recovered != global_point:
                point = self._local_tuple(recovered)
            for normal in contacts:
                remember(self._local_tuple(normal, True), identifiers)
            for field in self.fields.values():
                global_point = self._world_tuple(point)
                relative = tuple(global_point[i] - field._center[i] for i in range(3))
                bound = field.radius + field._max_elevation + radius * field._lipschitz + 1
                if sum(c * c for c in relative) > bound * bound:
                    continue
                gap, normal, _ = field._contact(relative, radius)
                if gap < _TERRAIN_SKIN:
                    if gap < -radius - 1:
                        recovered = field.sweep(global_point, (0, 0, 0), radius)
                        point = self._local_tuple(recovered.position)
                        for contact in recovered.normals:
                            remember(self._local_tuple(contact, True), recovered.hit_ids)
                    else:
                        contact = self._local_tuple(normal, True)
                        point = tuple(point[i] + contact[i] * (_TERRAIN_SKIN + .0005 - gap) for i in range(3))
                        remember(contact, [f"planet:{field.planet_id}:terrain"])
            if sum((point[i] - before[i]) ** 2 for i in range(3)) < 1e-12:
                break

        remaining, time_left = delta, 1.
        for _ in range(10):
            if sum(c * c for c in remaining) < 1e-12:
                break
            best = None
            hit = self.collisions._first_hit(point, remaining, radius, 0., ())
            if hit is not None:
                best = (hit.fraction, hit.normal, hit.id)
            global_point = self._world_tuple(point)
            global_delta = self._world_tuple(remaining, True)
            hit = self._space_collisions._first_hit(global_point, global_delta, radius, 0., ())
            if hit is not None and (best is None or hit.fraction < best[0]):
                best = (hit.fraction, self._local_tuple(hit.normal, True), hit.id)
            for field in self.fields.values():
                relative = tuple(global_point[i] - field._center[i] for i in range(3))
                hit = field._cast(relative, global_delta, radius)
                if hit is not None and (best is None or hit[0] < best[0]):
                    best = (hit[0], self._local_tuple(hit[1], True), f"planet:{field.planet_id}:terrain")
            if best is None:
                point = tuple(point[i] + remaining[i] for i in range(3))
                break
            fraction, normal, identifier = best
            point = tuple(point[i] + remaining[i] * fraction + normal[i] * .0005 for i in range(3))
            remember(normal, [identifier])
            time_left *= 1 - fraction
            remaining = _project(tuple(c * time_left for c in delta), normals)
        return MoveResult(Vec3(*point), [Vec3(*normal) for normal in normals],
                          any(normal[2] >= .60 for normal in normals),
                          any(normal[2] <= -.60 for normal in normals), ids)
