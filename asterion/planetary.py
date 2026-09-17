"""Shared spherical terrain, rigid tangent frames and continuous contacts.

All distances are system metres.  The sampler is independent of a scene or
active tangent frame, so an orbital globe and a ground patch share geography.
Panda vectors are only used at the public boundary; intermediate world-space
calculations use Python doubles to retain precision on distant bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import random

from panda3d.core import Quat, Vec3

from .collision import MoveResult


ATMOSPHERE_TOP = 2200.0
CLOUD_BOTTOM = 280.0
CLOUD_TOP = 700.0
_EPS = 1e-12
_SKIN = .025
_ZERO = (0.0, 0.0, 0.0)


def _xyz(value, default=_ZERO):
    try:
        xyz = tuple(float(value[i]) for i in range(3))
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return default
    return xyz if all(math.isfinite(v) for v in xyz) else default


def _length(v):
    return math.sqrt(sum(x*x for x in v))


def _unit(value):
    xyz = _xyz(value, (0.0, 0.0, 1.0))
    length = _length(xyz)
    return tuple(v/length for v in xyz) if length > _EPS else (0.0, 0.0, 1.0)


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _add(a, b):
    return a[0]+b[0], a[1]+b[1], a[2]+b[2]


def _sub(a, b):
    return a[0]-b[0], a[1]-b[1], a[2]-b[2]


def _mul(a, scalar):
    return a[0]*scalar, a[1]*scalar, a[2]*scalar


def _smooth(low, high, value):
    t = max(0.0, min(1.0, (value-low)/(high-low)))
    return t*t*(3.0-2.0*t)


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(a[i]*(1.0-t)+b[i]*t for i in range(3))


def _shade(color, scale):
    return tuple(max(0.0, min(1.0, c*scale)) for c in color[:3])


def chart_direction(u, v, radius):
    """Canonical longitude/latitude chart in metres, with home at +Z."""
    radius = max(_EPS, abs(float(radius)))
    longitude, latitude = float(u)/radius, float(v)/radius
    cosine = math.cos(latitude)
    return Vec3(math.sin(longitude)*cosine, math.sin(latitude),
                math.cos(longitude)*cosine)


def direction_chart(normal, radius):
    """Return canonical chart coordinates; all longitudes meet at a pole."""
    x, y, z = _unit(normal)
    longitude = math.atan2(x, z) if math.hypot(x, z) > 1e-10 else 0.0
    return longitude*radius, math.asin(max(-1.0, min(1.0, y)))*radius


def _basis_rotation(right, north, normal):
    # Columns of a right-handed local-to-world rotation matrix.
    m00, m10, m20 = right
    m01, m11, m21 = north
    m02, m12, m22 = normal
    trace = m00+m11+m22
    if trace > 0.0:
        s = math.sqrt(trace+1.0)*2.0
        values = (.25*s, (m21-m12)/s, (m02-m20)/s, (m10-m01)/s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0+m00-m11-m22)*2.0
        values = ((m21-m12)/s, .25*s, (m01+m10)/s, (m02+m20)/s)
    elif m11 > m22:
        s = math.sqrt(1.0+m11-m00-m22)*2.0
        values = ((m02-m20)/s, (m01+m10)/s, .25*s, (m12+m21)/s)
    else:
        s = math.sqrt(1.0+m22-m00-m11)*2.0
        values = ((m10-m01)/s, (m02+m20)/s, (m12+m21)/s, .25*s)
    rotation = Quat(*values)
    rotation.normalize()
    return rotation


@dataclass(slots=True)
class PlanetFrame:
    """A rigid tangent basis; reframing never changes a physical world pose."""

    normal: Vec3
    right: Vec3
    north: Vec3
    origin: Vec3
    center: Vec3
    radius: float
    planet_id: str
    rotation: Quat
    _basis: tuple
    _origin: tuple

    @classmethod
    def from_planet(cls, planet, normal=(0, 0, 1)):
        up = _unit(normal)
        x, y, z = up
        horizontal = math.hypot(x, z)
        right = (z/horizontal, 0.0, -x/horizontal) if horizontal > 1e-10 else (1.0, 0.0, 0.0)
        north = (y*right[2], z*right[0]-x*right[2], -y*right[0])
        center = _xyz(planet.get("position", _ZERO))
        radius = max(1.0, float(planet["size"]))
        origin = _add(center, _mul(up, radius))
        return cls(Vec3(*up), Vec3(*right), Vec3(*north), Vec3(*origin),
                   Vec3(*center), radius, str(planet.get("id", "planet")),
                   _basis_rotation(right, north, up), (right, north, up), origin)

    def to_world(self, local):
        local = _xyz(local)
        return Vec3(*(self._origin[i] + sum(self._basis[j][i]*local[j]
                                          for j in range(3)) for i in range(3)))

    def to_local(self, world):
        relative = _sub(_xyz(world), self._origin)
        return Vec3(*(_dot(relative, axis) for axis in self._basis))

    def vector_to_world(self, local):
        local = _xyz(local)
        return Vec3(*(sum(self._basis[j][i]*local[j] for j in range(3))
                      for i in range(3)))

    def vector_to_local(self, world):
        world = _xyz(world)
        return Vec3(*(_dot(world, axis) for axis in self._basis))


def frame_to_world(frame, position):
    return frame.to_world(position) if frame is not None else Vec3(*_xyz(position))


def frame_to_local(frame, position):
    return frame.to_local(position) if frame is not None else Vec3(*_xyz(position))


def vector_to_world(frame, vector):
    return frame.vector_to_world(vector) if frame is not None else Vec3(*_xyz(vector))


def vector_to_local(frame, vector):
    return frame.vector_to_local(vector) if frame is not None else Vec3(*_xyz(vector))


class PlanetField:
    """One seam-free visible/contact surface and its atmospheric envelope.

    Oceans support the same landing and walking controls as dry ground.  Their
    surface is the spherical water datum; the terrain beneath it is retained
    separately for geography and ocean colour, never used as a hidden floor.
    """

    def __init__(self, planet):
        self.planet = planet
        self.radius = max(1.0, float(planet["size"]))
        self._center = _xyz(planet.get("position", _ZERO))
        self.center = Vec3(*self._center)
        self.planet_id = str(planet.get("id", "planet"))
        self.seed = int(planet.get("seed", 0))
        self.biome = planet.get("biome", "verdant")
        self.water_level = float(planet.get("water_level", 8.0))
        self.ground = tuple(planet.get("ground", (.30, .42, .22))[:3])
        self.water = tuple(planet.get("water", (.08, .32, .45))[:3])
        self.flora = tuple(planet.get("flora", self.ground)[:3])
        self.accent = tuple(planet.get("accent", (.7, .6, .3))[:3])
        self.threshold = {"oceanic":.16, "verdant":-.025, "fungal":-.10,
                          "crystalline":-.17, "frozen":-.14, "toxic":-.13,
                          "desert":-.55, "volcanic":-.60}.get(self.biome, -.02)
        rng = random.Random(self.seed ^ 0x51972)
        self._phase = tuple(rng.uniform(-30.0, 30.0) for _ in range(6))
        self._lattice = tuple(rng.uniform(-1.0, 1.0) for _ in range(4096))
        self._samples = lru_cache(maxsize=16384)(self._sample)
        # Bound the unclipped terrain as well as the visible surface: shoreline
        # contacts solve the land field and the spherical ocean separately.
        self._max_elevation = max(20.0, self.water_level+160.0)
        self._min_elevation = min(20.0, self.water_level-160.0)
        minimum_radius = max(1.0, self.radius+self._min_elevation)
        home_difference = max(abs(self._max_elevation-20.0), abs(self._min_elevation-20.0))
        # Bounds from cubic-noise gradients, physical detail and the home ramp.
        self._slope_bound = 7900.0/minimum_radius+.125+home_difference*1.5/158.0
        self._lipschitz = math.sqrt(1.0+self._slope_bound*self._slope_bound)
        self._curvature = .25+2_000_000.0/(minimum_radius*minimum_radius)+600.0/minimum_radius

    def _noise(self, x, y, z):
        ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
        fx, fy, fz = x-ix, y-iy, z-iz
        fx, fy, fz = fx*fx*(3-2*fx), fy*fy*(3-2*fy), fz*fz*(3-2*fz)
        x0, x1 = ix&15, (ix+1)&15
        y0, y1 = (iy&15)*16, ((iy+1)&15)*16
        z0, z1 = (iz&15)*256, ((iz+1)&15)*256
        t = self._lattice
        a = t[x0+y0+z0]*(1-fx)+t[x1+y0+z0]*fx
        b = t[x0+y1+z0]*(1-fx)+t[x1+y1+z0]*fx
        c = t[x0+y0+z1]*(1-fx)+t[x1+y0+z1]*fx
        d = t[x0+y1+z1]*(1-fx)+t[x1+y1+z1]*fx
        return (a*(1-fy)+b*fy)*(1-fz)+(c*(1-fy)+d*fy)*fz

    def _sample(self, x, y, z):
        p, q, r, s, t, u = self._phase
        warp = self._noise(x*1.8+p, y*1.8+q, z*1.8+r)
        a, b, c = x*3.2+p+warp*.6, y*3.2+q+warp*.45, z*3.2+r+warp*.2
        low = self._noise(a, b, c)
        medium = self._noise(a*2.03+s, b*2.03+t, c*2.03+u)
        fine = self._noise(a*4.9+t, b*4.9+u, c*4.9+s)
        continental = low + .27*medium + .075*fine - self.threshold
        detail = (1.6*math.sin(self.radius*(x*.036+y*.022+z*.013)+p)
                  * math.cos(self.radius*(y*.029-z*.017)+q))
        natural = self.water_level + 75.0*continental + detail
        # Angular distance avoids seams and applies only around canonical home.
        distance = self.radius*math.atan2(math.hypot(x, y), z)
        blend = _smooth(42.0, 200.0, distance)
        elevation = 20.0+(natural-20.0)*blend
        return elevation, continental, medium, fine, blend

    def seabed_elevation(self, direction):
        """Unclipped terrain relief, including terrain below the ocean datum."""
        return self._samples(*_unit(direction))[0]

    def elevation(self, direction):
        """Visible surface elevation used by rendering, altitude and contact."""
        return max(self.water_level, self.seabed_elevation(direction))

    def normal(self, direction):
        """Outward unit normal of the same surface used for sphere contacts."""
        up = _unit(direction)
        surface = _mul(up, self.radius+self.elevation(up))
        return Vec3(*self._normal(surface)[0])

    def color(self, direction):
        direction = _unit(direction)
        elevation, continental, medium, fine, blend = self._samples(*direction)
        depth = elevation-self.water_level
        sea = _mix(_shade(self.water, .36), _shade(self.water, .86),
                   _smooth(-38, -.5, depth))
        coast = _mix(self.ground, self.accent, .20)
        vegetation = _mix(_shade(self.flora, .78), self.ground, .40)
        land = _mix(vegetation, _shade(self.ground, 1.22), _smooth(9, 54, depth))
        if self.biome == "desert":
            ripple = .5+.5*math.sin(direction[0]*71+direction[2]*34+medium*7)
            land = _mix(_shade(self.ground, .70), _mix(self.ground, (.92, .62, .35), .24), ripple)
        elif self.biome == "volcanic":
            land = _mix((.055, .045, .065), _shade(self.ground, .80), .5+.3*medium)
            fissure = (1-_smooth(.012, .065, abs(medium+.23*fine)))*_smooth(.05, .4, continental)
            land = _mix(land, (1.0, .23, .025), fissure*.86)
        elif self.biome == "frozen":
            land = _mix((.25, .49, .65), (.81, .90, .94), _smooth(-5, 34, depth))
        elif self.biome == "crystalline":
            land = _mix(_shade(self.ground, .65), self.accent, _smooth(15, 66, depth)*.60)
        elif self.biome == "fungal":
            land = _mix(_shade(self.flora, .65), self.ground, _smooth(-.3, .4, medium))
        land = _mix(coast, land, _smooth(.5, 5, depth))
        color = _mix(sea, land, _smooth(-.7, .9, depth))
        if self.biome not in ("desert", "volcanic", "toxic"):
            ice = _smooth(.86, .975, abs(direction[1])+.025*medium)
            color = _mix(color, (.80, .90, .94), ice*.94)
        # The dry home shelf uses the same palette as nearby land.
        color = _mix(vegetation, color, blend)
        return _shade(color, .94+.06*fine+.045*medium)

    def point(self, u, v, extra=0):
        direction = _unit(chart_direction(u, v, self.radius))
        return Vec3(*_add(self._center, _mul(direction, self.radius+self.elevation(direction)+float(extra))))

    def altitude(self, world_position):
        relative = _sub(_xyz(world_position, self._center), self._center)
        distance = _length(relative)
        return distance-self.radius-self.elevation(relative)

    def local_height(self, frame, x, y):
        """Intersection of local vertical with the near (top) solid surface.

        This is intentionally a height function only on the top hemisphere.
        Points beyond that hemisphere return its finite tangent horizon.
        """
        x, y = float(x), float(y)
        right, north, up = frame._basis
        transverse = _add(_mul(right, x), _mul(north, y))
        rho2 = x*x+y*y
        radial = self.radius+20.0
        for _ in range(12):
            axial = math.sqrt(max(0.0, radial*radial-rho2))
            direction = _unit(_add(transverse, _mul(up, axial)))
            target = self.radius+self.elevation(direction)
            if abs(target-radial) < 1e-6:
                radial = target
                break
            radial = target
        return math.sqrt(max(0.0, radial*radial-rho2))-self.radius

    def atmosphere(self, world_position, world_velocity=(0, 0, 0)):
        relative = _sub(_xyz(world_position, self._center), self._center)
        normal = _unit(relative)
        altitude = self.altitude(world_position)
        radial_speed = _dot(_xyz(world_velocity), normal)
        top_fade = 1.0-_smooth(1550.0, ATMOSPHERE_TOP, altitude)
        density = math.exp(-max(0.0, altitude)/720.0)*top_fade
        space_blend = _smooth(220.0, ATMOSPHERE_TOP, altitude)
        cloud = _smooth(CLOUD_BOTTOM, 410.0, altitude)*(1.0-_smooth(540.0, CLOUD_TOP, altitude))
        inward = _smooth(18.0, 950.0, -radial_speed)
        outward = _smooth(12.0, 650.0, radial_speed)
        interaction = _smooth(0.0, .035, density)
        entry = inward*interaction
        exit_effect = outward*interaction
        heat = entry*min(1.0, math.sqrt(density)*1.6)
        return dict(altitude=altitude, density=density, space_blend=space_blend,
                    cloud=cloud, heat=heat, radial_speed=radial_speed,
                    entry=entry, exit=exit_effect, planet_id=self.planet_id)

    def _normal(self, relative, *, seabed=False):
        """Outward gradient of the radial terrain, using metre-scale samples."""
        distance = _length(relative)
        up = _unit(relative)
        if distance < 1.0:
            return up, 1.0
        gradient = []
        step = .35
        elevation = self.seabed_elevation if seabed else self.elevation
        for axis in range(3):
            offset = [0.0, 0.0, 0.0]
            offset[axis] = step
            derivative = (elevation(_add(relative, offset))
                          - elevation(_sub(relative, offset)))/(2.0*step)
            gradient.append(up[axis]-derivative)
        scale = _length(gradient)
        return _unit(gradient), max(1.0, scale)

    def sweep(self, start_global, displacement_global, radius=3):
        """Continuously advance a sphere against terrain and slide on impact.

        Work is bounded even for a segment through the entire planet.  A broad
        radial bound skips empty space; conservative advancement handles the
        actual terrain.  Exhausted iteration budgets stop at the last verified
        safe position instead of accepting an unchecked remainder.
        """
        return self._sweep(start_global, displacement_global, radius)

    def _contact(self, relative, radius):
        """Nearest boundary of the terrain solid and its spherical ocean."""
        return min(self._contact_surfaces(relative, radius), key=lambda item: item[0])

    def _contact_surfaces(self, relative, radius):
        """Sphere separations from both continuous components of the surface.

        Projecting onto successively updated surface tangent planes finds the
        nearest land point, including a sloped bank next to a hull over water.
        Solving the unclipped terrain and ocean separately avoids overlooking
        that bank when the hull's radial direction samples only flat water.
        """
        radial = _length(relative)
        direction = _unit(relative)
        height = self.seabed_elevation(direction)
        signed = radial-self.radius-max(height, self.water_level)
        if signed > 64.0:
            return [(signed/self._lipschitz-radius, direction, False)]
        ocean = (radial-self.radius-self.water_level-radius, direction, True)
        terrain_signed = radial-self.radius-height
        if terrain_signed > 64.0:
            return [ocean, (terrain_signed/self._lipschitz-radius, direction, False)]
        surface = _mul(direction, self.radius+height)
        normal, _ = self._normal(surface, seabed=True)
        for _ in range(5):
            separation = _sub(relative, surface)
            projected = _sub(relative, _mul(normal, _dot(separation, normal)))
            new_direction = _unit(projected)
            next_surface = _mul(new_direction, self.radius+self.seabed_elevation(new_direction))
            if _length(_sub(surface, next_surface)) < .0002:
                surface = next_surface
                break
            surface = next_surface
            normal, _ = self._normal(surface, seabed=True)
        normal, _ = self._normal(surface, seabed=True)
        separation = _dot(_sub(relative, surface), normal)
        return [ocean, (separation-radius, normal, True)]

    def _interval(self, point, delta, radius):
        """Intersection with a known enclosing sphere, in segment fractions."""
        a = _dot(delta, delta)
        c = _dot(point, point)-radius*radius
        if a < _EPS:
            return (0.0, 1.0) if c <= 0 else None
        b = _dot(point, delta)
        discriminant = b*b-a*c
        if discriminant < 0:
            return None
        root = math.sqrt(max(0.0, discriminant))
        enter, leave = max(0.0, (-b-root)/a), min(1.0, (-b+root)/a)
        return (enter, leave) if enter <= leave else None

    def _cast(self, point, delta, radius):
        distance = _length(delta)
        if distance < _EPS:
            return None
        bound = self.radius+self._max_elevation+radius*self._lipschitz+_SKIN
        interval = self._interval(point, delta, bound)
        if interval is None:
            return None
        fraction, end = interval
        direction = _mul(delta, 1.0/distance)
        last_normal = _unit(point)
        for _ in range(192):
            current = _add(point, _mul(delta, fraction))
            contacts = self._contact_surfaces(current, radius)
            gap, normal, _ = min(contacts, key=lambda item: item[0])
            last_normal = normal
            gap -= _SKIN
            if gap <= .00015:
                return fraction, normal
            advance = math.inf
            for surface_gap, surface_normal, nearby in contacts:
                surface_gap -= _SKIN
                safe_step = surface_gap/self._lipschitz
                if nearby:
                    # Bound both component surfaces independently. At a
                    # shoreline their minimum has a crease, so a step based
                    # only on the currently nearest normal could skip land.
                    derivative = _dot(surface_normal, direction)-.035
                    curvature = self._curvature
                    root = math.sqrt(derivative*derivative+2.0*curvature*surface_gap)
                    quadratic = (2.0*surface_gap/(root-derivative)
                                 if derivative < 0 else (derivative+root)/curvature)
                    safe_step = max(safe_step, .75*quadratic)
                advance = min(advance, safe_step)
            next_fraction = fraction+advance/distance
            if next_fraction > end:
                return None
            if next_fraction <= fraction+1e-13:
                return fraction, normal
            fraction = next_fraction
        # Never accept an unchecked remainder after exhausting a finite cast.
        return fraction, last_normal

    def _sweep(self, start_global, displacement_global, radius):
        try:
            radius = float(radius)
        except (TypeError, ValueError, OverflowError):
            radius = 3.0
        radius = min(1000.0, max(0.0, radius)) if math.isfinite(radius) else 3.0
        start = _xyz(start_global, _add(self._center, (0.0, 0.0, self.radius+23.0)))
        displacement = _xyz(displacement_global)
        # Finite input limits keep dot products and the enclosing quadratic
        # well-conditioned even if a caller passes a damaged saved velocity.
        point = tuple(max(-1e9, min(1e9, v)) for v in _sub(start, self._center))
        remaining = tuple(max(-1e9, min(1e9, v)) for v in displacement)
        normals = []

        def remember(normal):
            if not any(_dot(old, normal) > .9998 for old in normals):
                normals.append(normal)

        # Deep penetration has no meaningful local closest-point iteration.
        # Move directly to that radial direction's surface, then resolve its
        # sphere footprint.  This also gives the exact centre a defined escape.
        radial = _length(point)
        direction = _unit(point)
        radial_gap = radial-self.radius-self.elevation(direction)
        if radial_gap < -1.0:
            point = _mul(direction, self.radius+self.elevation(direction)+radius+_SKIN)
            remember(direction)
        for _ in range(12):
            gap, normal, _ = self._contact(point, radius)
            if gap >= _SKIN-.00015:
                break
            point = _add(point, _mul(normal, _SKIN-gap+.0002))
            remember(normal)

        for _ in range(8):
            if _length(remaining) < .0001:
                break
            hit = self._cast(point, remaining, radius)
            if hit is None:
                point = _add(point, remaining)
                remaining = _ZERO
                break
            fraction, normal = hit
            point = _add(point, _mul(remaining, fraction))
            remember(normal)
            remaining = _mul(remaining, 1.0-fraction)
            inward = _dot(remaining, normal)
            if inward < 0.0:
                remaining = _sub(remaining, _mul(normal, inward))
            # Contact skin is geometric separation, not a velocity clamp.
            point = _add(point, _mul(normal, .0004))

        # The public Vec3 quantizes large global positions.  Account for this
        # once at the boundary so rounded output remains outside the terrain.
        result = Vec3(*_add(self._center, point))
        rounded = _sub(tuple(result), self._center)
        gap, normal, _ = self._contact(rounded, radius)
        if gap < _SKIN*.5:
            result = Vec3(*_add(self._center, _add(rounded, _mul(normal, _SKIN-gap))))
            remember(normal)
        outward = _unit(_sub(tuple(result), self._center))
        return MoveResult(result, [Vec3(*normal) for normal in normals],
                          any(_dot(normal, outward) > .60 for normal in normals),
                          False, [f"planet:{self.planet_id}:terrain"] if normals else [])
