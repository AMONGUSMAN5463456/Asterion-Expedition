"""Persistent, shader-free cloud volumes and a physical-scale atmospheric limb.

Cloud banks contain overlapping soft puffs at fixed three-dimensional positions.
Each puff has horizontal extent and finite vertical depth; soft, textured
billows retain that volume on the software renderer. The same ellipsoids sample
local mist, so entering and leaving a bank changes visibility continuously.
Nothing follows the camera, replaces terrain, moves a collider, or owns a task.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import random

from panda3d.core import Geom, GeomVertexWriter, SamplerState, Texture, TransparencyAttrib, Vec3

from .geometry import Mesh
from .planetary import ATMOSPHERE_TOP, CLOUD_BOTTOM, CLOUD_TOP, PlanetFrame


_SUN = Vec3(-.68, -.62, .39)
_SUN.normalize()
_BANK_COUNT = 96
_PUFFS_PER_BANK = 7


def _smooth(low, high, value):
    t = max(0., min(1., (value - low) / (high - low)))
    return t * t * (3 - 2 * t)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _xyz(value):
    try:
        result = tuple(float(value[i]) for i in range(3))
    except (ValueError, TypeError, IndexError, KeyError, OverflowError):
        return (0., 0., 0.)
    return result if all(math.isfinite(v) for v in result) else (0., 0., 0.)


@dataclass(frozen=True, slots=True)
class CloudPuff:
    """One cloud's body-space ellipsoid, also used for continuous local mist."""

    center: tuple
    axes: tuple
    radii: tuple
    opacity: float

    def density(self, body_position):
        relative = tuple(body_position[i] - self.center[i] for i in range(3))
        # A cheap enclosing box rejects almost every distant puff.
        extent = max(self.radii)
        if any(abs(v) > extent for v in relative):
            return 0.
        distance = sum((_dot(relative, axis) / radius) ** 2
                       for axis, radius in zip(self.axes, self.radii))
        if distance >= 1:
            return 0.
        return self.opacity * (1 - distance) ** 2


@lru_cache(maxsize=1)
def _cloud_texture():
    """Small original soft billow; shared RAM texture needs no shader or asset."""
    width = 64
    pixels = bytearray(width * width * 4)
    for y in range(width):
        for x in range(width):
            u, v = 2 * (x + .5) / width - 1, 2 * (y + .5) / width - 1
            angle = math.atan2(v, u)
            rim = .88 + .055 * math.sin(angle * 5 + .7) + .045 * math.sin(angle * 9)
            radius = math.hypot(u, v) / rim
            billow = .80 + .12 * math.sin(u * 12 + v * 5) * math.sin(v * 11 - u * 3)
            alpha = max(0., 1 - radius * radius) ** 1.3 * billow
            brightness = .91 + .06 * math.sin(v * 3 + .8) + .025 * math.cos(u * 9 + v * 4)
            offset = (y * width + x) * 4
            pixels[offset:offset + 4] = bytes((int(brightness * 244),
                int(brightness * 249), int(brightness * 255), int(alpha * 255)))
    texture = Texture("soft-world-cloud-billow")
    texture.setup2dTexture(width, width, Texture.TUnsignedByte, Texture.FRgba8)
    texture.setRamImageAs(bytes(pixels), "RGBA")
    texture.setMinfilter(SamplerState.FTLinearMipmapLinear)
    texture.setMagfilter(SamplerState.FTLinear)
    texture.setWrapU(SamplerState.WMClamp)
    texture.setWrapV(SamplerState.WMClamp)
    texture.generateRamMipmapImages()
    return texture


def _billow_points(puff, observer, offset):
    """Project a fixed ellipsoid into a soft billow, preserving physical scale.

    Its center never follows the observer. Two separated centers in every puff,
    plus seven puffs per bank, supply depth and parallax during a crossing.
    """
    center = Vec3(*puff.center) + Vec3(*puff.axes[2]) * puff.radii[2] * offset
    view = Vec3(*observer) - center
    distance = view.length()
    if view.lengthSquared() < 1e-8:
        view = Vec3(*puff.axes[1])
    view.normalize()
    up = Vec3(*puff.axes[2])
    up -= view * up.dot(view)
    if up.lengthSquared() < 1e-6:
        up = Vec3(*puff.axes[1])
        up -= view * up.dot(view)
    up.normalize()
    right = up.cross(view)
    right.normalize()
    width = .94 * math.sqrt(sum((right.dot(Vec3(*axis)) * radius) ** 2
                                for axis, radius in zip(puff.axes, puff.radii)))
    height = .85 * math.sqrt(sum((up.dot(Vec3(*axis)) * radius) ** 2
                                 for axis, radius in zip(puff.axes, puff.radii)))
    # Draw the impostor on the near side of its volume. A center-plane card
    # can cut into the curved planet even when its cloud is wholly above land.
    # The perspective correction retains the ellipsoid's projected extent.
    support = math.sqrt(sum((view.dot(Vec3(*axis)) * radius) ** 2
                            for axis, radius in zip(puff.axes, puff.radii)))
    depth = min(support * .82, distance * .35)
    correction = max(.1, 1 - depth / max(1., distance))
    center += view * depth
    width *= correction
    height *= correction
    return [tuple(center + right * a * width + up * b * height)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _billow(mesh, puff, observer, offset, color, alpha):
    points = _billow_points(puff, observer, offset)
    # Lower sides are cooler and shaded, while the upper billows catch the sun.
    colors = []
    for point in points:
        height = _dot(tuple(point[k] - puff.center[k] for k in range(3)), puff.axes[2])
        brightness = .93 + .07 * max(-1., min(1., height / puff.radii[2]))
        colors.append(tuple(c * brightness for c in color) + (alpha,))
    uv = ((0, 0), (1, 0), (1, 1), (0, 1))
    for indices in ((0, 1, 2), (0, 2, 3)):
        mesh.tri(*(points[i] for i in indices), (*color, alpha),
                 colors=tuple(colors[i] for i in indices),
                 texcoords=tuple(uv[i] for i in indices))


def _visibility(radius, observer, puff):
    """Analytic planetary occlusion, robust to a software depth buffer's range."""
    origin = Vec3(*observer)
    ray = Vec3(*puff.center) - origin
    denominator = ray.lengthSquared()
    if denominator < 1.:
        return 1.
    along = -origin.dot(ray) / denominator
    if along <= 0. or along >= 1.:
        return 1.
    clearance = (origin + ray * along).length() - radius
    edge = puff.radii[2] * .55
    return _smooth(-edge, edge, clearance)


def _limb_mesh(radius, color):
    """Haze falloff measured in metres rather than a percentage of planet size."""
    mesh = Mesh()
    rings = ((-.8, 110., .12, .22), (110., 330., .22, .15),
             (330., 850., .15, .052), (850., ATMOSPHERE_TOP, .052, 0.))
    for inner, outer, alpha0, alpha1 in rings:
        for index in range(96):
            a, b = math.tau * index / 96, math.tau * (index + 1) / 96
            def point(height, angle):
                return ((radius + height) * math.cos(angle), 0,
                        (radius + height) * math.sin(angle))
            points = (point(inner, a), point(outer, a), point(outer, b), point(inner, b))
            colors = ((*color, alpha0), (*color, alpha1), (*color, alpha1), (*color, alpha0))
            for indices in ((0, 1, 2), (0, 2, 3)):
                mesh.tri(*(points[i] for i in indices), colors[0],
                         colors=tuple(colors[i] for i in indices))
    return mesh


def fog_profile(data, tone, cloud_density=0., storm=0.):
    """Continuous distance haze, with extra optical depth only inside a cloud."""
    density = max(0., min(1., float(data.get("density", 0.))))
    cloud = max(0., min(1., float(cloud_density)))
    storm = max(0., min(1., float(storm)))
    cloud *= _smooth(0., .015, density)
    color = tuple(tone[i] * (1 - cloud * .52) + (.72, .79, .85)[i] * cloud * .52
                  for i in range(3))
    return color, density * (.00015 + storm * .0005) + cloud * .0009


class PlanetWeather:
    """A bounded, static collection of world-space cloud banks and limb haze."""

    def __init__(self, field, body):
        self.field = field
        self.root = body.attachNewNode("physical-planet-weather")
        self.clouds = self.root.attachNewNode("finite-depth-cloud-banks")
        self.clouds.setLightOff(10)
        self.clouds.setMaterialOff(10)
        self.clouds.setShaderOff(10)
        self.clouds.setTransparency(TransparencyAttrib.MAlpha)
        self.clouds.setDepthWrite(False)
        # One depth quantum separates the clouds from land on software depth
        # buffers spanning sub-metre cockpit detail to interplanetary ranges.
        # Analytic occlusion below also excludes the far hemisphere.
        self.clouds.setDepthOffset(1)
        self.clouds.setTwoSided(True)
        self.clouds.setTexture(_cloud_texture(), 1)
        self.limb = _limb_mesh(field.radius, field.planet["sky"][:3]).node(
            "physical-atmospheric-limb", self.root, two_sided=True, unlit=True)
        self.limb.setTransparency(TransparencyAttrib.MAlpha)
        self.limb.setDepthWrite(False)
        self.limb.setFogOff(10)
        self.puffs = []
        self.banks = []
        self._billows = []
        self._destroyed = False
        self._build_clouds()

    def _build_clouds(self):
        field, radius = self.field, self.field.radius
        rng = random.Random(field.seed ^ 0xC10DDE)
        directions = []
        # A Fibonacci lattice has no longitude seam or polar concentration.
        # Irregular positions and clustered puffs break up its regular spacing.
        for index in range(_BANK_COUNT):
            y = 1 - 2 * (index + .5) / _BANK_COUNT
            angle = index * 2.399963229728653 + rng.uniform(-.15, .15)
            ring = math.sqrt(1 - y * y)
            directions.append(Vec3(math.sin(angle) * ring, y, math.cos(angle) * ring))
        # One nearby bank supplies a readable reference for the first ascent.
        # It is real geography: its center, thickness and extent never follow us.
        directions.append(Vec3(0, 0, 1))
        for index, direction in enumerate(directions):
            frame = PlanetFrame.from_planet(field.planet, direction)
            width = radius * rng.uniform(.047, .078)
            length = radius * rng.uniform(.028, .048)
            if index == _BANK_COUNT:
                width, length = 620., 450.
            heading = rng.uniform(0., math.tau)
            right = frame.right * math.cos(heading) + frame.north * math.sin(heading)
            north = frame.north * math.cos(heading) - frame.right * math.sin(heading)
            mesh, bank_puffs = Mesh(), []
            for puff_index in range(_PUFFS_PER_BANK):
                phase = puff_index * 2.399963229728653
                spread = math.sqrt(puff_index / (_PUFFS_PER_BANK - 1))
                dx, dy = math.cos(phase) * width * spread, math.sin(phase) * length * spread
                normal = direction * radius + right * dx + north * dy
                normal.normalize()
                tangent = PlanetFrame.from_planet(field.planet, normal)
                axis0 = tangent.right * math.cos(heading) + tangent.north * math.sin(heading)
                axis1 = tangent.north * math.cos(heading) - tangent.right * math.sin(heading)
                # Distinct tops and bases make approach, immersion and exit
                # occupy hundreds of metres rather than one spherical boundary.
                altitude = 475. + rng.uniform(-80., 80.)
                vertical = rng.uniform(80., 125.)
                if index == _BANK_COUNT and puff_index == 0:
                    altitude, vertical = 490., 145.
                horizontal = (.50 + rng.random() * .22)
                radii = (max(120., width * horizontal), max(105., length * horizontal), vertical)
                center = normal * (radius + field.elevation(normal) + altitude)
                axes = (tuple(axis0), tuple(axis1), tuple(normal))
                puff = CloudPuff(tuple(center), axes, radii, rng.uniform(.46, .70))
                self.puffs.append(puff)
                bank_puffs.append(puff)
                light = .44 + .56 * max(0., normal.dot(_SUN)) ** .55
                color = tuple(c * light for c in (.90, .95, 1.))
                # Separated billows make finite cloud depth without the sharp
                # intersection lines produced by crossed transparent planes.
                initial_observer = tuple(normal * (radius * 4))
                for offset, alpha in ((-.30, .58), (.30, .66)):
                    _billow(mesh, puff, initial_observer, offset, color, alpha)
            node = mesh.node("world-cloud-bank-%02d" % index, self.clouds,
                             two_sided=True, unlit=True)
            # Sorting is per bank, preserving depth relative to other banks
            # without hundreds of independent scene nodes per planet.
            self.banks.append((node, tuple(bank_puffs)))
            data = node.node().modifyGeom(0).modifyVertexData()
            data.setUsageHint(Geom.UHDynamic)
            center = tuple(sum(puff.center[i] for puff in bank_puffs) / len(bank_puffs)
                           for i in range(3))
            self._billows.append(dict(data=data, puffs=tuple(bank_puffs),
                                      colors=tuple(mesh.colors), node=node,
                                      center=Vec3(*center), observer=None))
        self.puffs = tuple(self.puffs)
        self.banks = tuple(self.banks)

    def local_density(self, world_position):
        if self._destroyed:
            return 0.
        relative = tuple(a - b for a, b in zip(_xyz(world_position), self.field._center))
        distance = math.sqrt(_dot(relative, relative))
        # No cloud can reach the surface or upper atmosphere. The radial bound
        # avoids testing the fixed puff set during ordinary orbital travel.
        if distance < self.field.radius + CLOUD_BOTTOM - 165 or distance > self.field.radius + CLOUD_TOP + 165:
            return 0.
        clear = 1.
        for puff in self.puffs:
            clear *= 1. - puff.density(relative)
        return 1. - clear

    def update(self, world_position, storm=0.):
        """Only limb presentation changes; cloud geometry stays world-fixed."""
        if self._destroyed:
            return
        relative = Vec3(*_xyz(world_position)) - self.field.center
        distance = relative.length()
        if distance < 1.:
            relative = Vec3(0, 0, 1)
            distance = 1.
        normal = relative / distance
        altitude = self.field.altitude(world_position)
        # Place the ring at the sphere's actual tangent plane. A center-plane
        # disc otherwise disappears behind a large planet during close orbit.
        tangent = min(.995, self.field.radius / max(distance, self.field.radius))
        self.limb.setPos(normal * (self.field.radius * tangent))
        self.limb.lookAt(relative)
        self.limb.setScale(math.sqrt(max(.001, 1 - tangent * tangent)))
        alpha = _smooth(450., 1800., altitude)
        self.limb.setColorScale(1, 1, 1, alpha)
        self.limb.show() if alpha > .001 else self.limb.hide()
        shade = 1 - max(0., min(1., float(storm))) * .28
        self.clouds.setColorScale(shade, shade, shade, 1)
        body_observer = Vec3(*_xyz(world_position)) - self.field.center
        for bank in self._billows:
            distance = (body_observer - bank["center"]).length()
            # Far banks need orientation changes only after a visible change
            # of angle. Nearby billows retain smooth translation parallax.
            threshold = max(.8, min(400., distance * .016))
            previous = bank["observer"]
            if previous is not None and (body_observer - previous).lengthSquared() < threshold ** 2:
                continue
            bank["observer"] = Vec3(body_observer)
            writer = GeomVertexWriter(bank["data"], "vertex")
            colors = GeomVertexWriter(bank["data"], "color")
            vertex_index = 0
            visible = 0.
            for puff in bank["puffs"]:
                opacity = _visibility(self.field.radius + self.field.water_level,
                                      body_observer, puff)
                visible = max(visible, opacity)
                for offset in (-.30, .30):
                    points = _billow_points(puff, body_observer, offset)
                    for index in (0, 1, 2, 0, 2, 3):
                        writer.setData3f(*points[index])
                        color = bank["colors"][vertex_index]
                        colors.setData4f(*color[:3], color[3] * opacity)
                        vertex_index += 1
            bank["node"].show() if visible > .001 else bank["node"].hide()

    def destroy(self):
        if not self._destroyed:
            self.root.removeNode()
            self._billows.clear()
            self._destroyed = True
