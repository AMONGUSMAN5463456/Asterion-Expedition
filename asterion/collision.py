"""Small, renderer-independent continuous collision world.

Coordinates are Panda's +Y-forward, Z-up convention.  Capsule positions are
the eye/top, with the feet exactly ``height`` below; the body is a vertical
line segment with spherical ends, not three independent sample spheres.

An upright capsule's configuration obstacle is the obstacle extruded by its
internal vertical segment and rounded by its radius.  All three supported
shapes have an exact signed distance for that convex volume.  Conservative
distance/normal advancement finds time of impact without discrete substeps,
even for a fast sphere crossing a very thin wall.  Iteration limits stop at a
safe location rather than advancing unchecked through geometry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
import math

from panda3d.core import Vec3


_LIMIT = 10_000_000.0
_SIZE_LIMIT = 1_000_000.0
_SKIN = 0.001
_CONTACT = 0.003
_EPS = 1e-8
_GROUND_Z = 0.60
_MAX_GROUP_SHAPES = 8192
_MAX_SHAPE_CELLS = 512
_MAX_QUERY_CELLS = 4096
_MAX_SWEEPS = 10
_MAX_RECOVERY = 24
_MAX_ADVANCE = 64
_ZERO = (0.0, 0.0, 0.0)


@dataclass(slots=True)
class MoveResult:
    position: Vec3
    normals: list[Vec3]
    grounded: bool
    ceiling: bool
    hit_ids: list[str]
    stepped: bool = False


@dataclass(slots=True)
class RayHit:
    distance: float
    normal: Vec3
    id: str
    position: Vec3


@dataclass(frozen=True, slots=True)
class _Shape:
    id: str
    group: str
    kind: str
    center: tuple[float, float, float]
    half: tuple[float, float, float]
    radius: float
    cosine: float
    sine: float
    low: tuple[float, float, float]
    high: tuple[float, float, float]


@dataclass(slots=True)
class _Hit:
    fraction: float
    normal: tuple[float, float, float]
    id: str


def _number(value, default=0.0, low=-_LIMIT, high=_LIMIT):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(high, max(low, value)) if math.isfinite(value) else default


def _vector(value, default=_ZERO):
    try:
        return tuple(_number(value[i], default[i]) for i in range(3))
    except (TypeError, IndexError, KeyError):
        return default


def _shape_vector(value, *, positive=False):
    try:
        result = tuple(float(value[i]) for i in range(3))
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return None
    limit = _SIZE_LIMIT if positive else _LIMIT
    if any(not math.isfinite(x) or abs(x) > limit for x in result):
        return None
    if positive and any(x <= 0.0 for x in result):
        return None
    return result


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sign(value):
    return -1.0 if value < 0.0 else 1.0


def _ignored(ignore):
    if isinstance(ignore, str):
        return frozenset((ignore,))
    try:
        return frozenset(x for x in islice(ignore, _MAX_GROUP_SHAPES)
                         if isinstance(x, str))
    except TypeError:
        return frozenset()


def _bounds_overlap(low, high, other_low, other_high):
    return all(low[i] <= other_high[i] and high[i] >= other_low[i]
               for i in range(3))


def _interval(origin, delta, low, high):
    """Closed ray segment/slab interval, including parallel boundary rays."""
    start, end = 0.0, 1.0
    for axis in range(3):
        if abs(delta[axis]) <= _EPS:
            if origin[axis] < low[axis] or origin[axis] > high[axis]:
                return None
            continue
        near = (low[axis] - origin[axis]) / delta[axis]
        far = (high[axis] - origin[axis]) / delta[axis]
        if near > far:
            near, far = far, near
        start, end = max(start, near), min(end, far)
        if start > end:
            return None
    return start, end


def _distance(shape, point, radius, segment):
    """Signed gap and unit outward normal for the configuration obstacle."""
    dx, dy, dz = _sub(point, shape.center)
    if shape.kind == "sphere":
        z = dz - min(segment, max(-segment, dz))
        length = math.sqrt(dx * dx + dy * dy + z * z)
        normal = (dx / length, dy / length, z / length) if length > _EPS else (1., 0., 0.)
        return length - shape.radius - radius, normal

    if shape.kind == "cylinder":
        radial = math.hypot(dx, dy)
        side = radial - shape.radius
        top = abs(dz) - shape.half[2] - segment
        nx, ny = (dx / radial, dy / radial) if radial > _EPS else (1., 0.)
        if side > 0.0 or top > 0.0:
            out_side, out_top = max(0.0, side), max(0.0, top)
            length = math.hypot(out_side, out_top)
            return (length - radius,
                    (nx * out_side / length, ny * out_side / length,
                     _sign(dz) * out_top / length))
        if top > side:
            return top - radius, (0., 0., _sign(dz))
        return side - radius, (nx, ny, 0.)

    # Inverse Panda heading: local +Y at heading +90 is world -X.
    x = shape.cosine * dx + shape.sine * dy
    y = -shape.sine * dx + shape.cosine * dy
    local = (x, y, dz)
    q = (abs(x) - shape.half[0], abs(y) - shape.half[1],
         abs(dz) - shape.half[2] - segment)
    outside = tuple(max(0.0, value) for value in q)
    length = math.sqrt(_dot(outside, outside))
    if length > _EPS:
        normal = tuple(_sign(local[i]) * outside[i] / length for i in range(3))
        gap = length - radius
    else:
        # Prefer Z for exact ties so coplanar roof/floor support is stable.
        axis = max(range(3), key=lambda i: (q[i], i))
        normal = tuple(_sign(local[i]) if i == axis else 0.0 for i in range(3))
        gap = q[axis] - radius
    return gap, (shape.cosine * normal[0] - shape.sine * normal[1],
                 shape.sine * normal[0] + shape.cosine * normal[1], normal[2])


def _cast_shape(shape, point, delta, radius, segment, skin=_SKIN,
                ray=False):
    extent = (radius + skin, radius + skin, radius + segment + skin)
    interval = _interval(point, delta, _sub(shape.low, extent), _add(shape.high, extent))
    if interval is None:
        return None
    fraction, end = interval
    for _ in range(_MAX_ADVANCE):
        current = _add(point, _mul(delta, fraction))
        distance, normal = _distance(shape, current, radius, segment)
        gap = distance - skin
        closing = -_dot(delta, normal)
        if gap <= 1e-7:
            if ray or closing > _EPS:
                return _Hit(max(0.0, min(1.0, fraction)), normal, shape.id)
            return None
        if closing <= _EPS:
            # Distance to a convex body cannot start decreasing again along
            # this line once its directional derivative is nonnegative.
            return None
        advance = gap / closing
        if fraction + advance > end:
            current = _add(point, _mul(delta, end))
            distance, normal = _distance(shape, current, radius, segment)
            if distance - skin <= 1e-7 and (ray or -_dot(delta, normal) > _EPS):
                return _Hit(max(0.0, min(1.0, end)), normal, shape.id)
            return None
        fraction = min(end, fraction + advance)
    # A pathological grazing contact is conservatively blocked, never skipped.
    return _Hit(max(0.0, min(1.0, fraction)), normal, shape.id)


def _append_contact(normals, ids, normal, shape_id):
    if not any(_dot(old, normal) > 0.9999 for old in normals):
        normals.append(normal)
    if shape_id not in ids:
        ids.append(shape_id)


def _project(delta, normals):
    """Closest feasible displacement for contact planes, including corners.

    In 3D the optimum lies on zero, one, two, or three contact planes.  Testing
    those subspaces avoids repeated projection oscillating in acute corners.
    """
    def allowed(candidate):
        return all(_dot(candidate, n) >= -1e-8 for n in normals)

    if allowed(delta):
        return delta
    best, best_error = _ZERO, _dot(delta, delta)
    for i, normal in enumerate(normals):
        candidate = _sub(delta, _mul(normal, _dot(delta, normal)))
        if allowed(candidate):
            error = _dot(_sub(candidate, delta), _sub(candidate, delta))
            if error < best_error:
                best, best_error = candidate, error
        for other in normals[:i]:
            crease = _cross(normal, other)
            length_sq = _dot(crease, crease)
            if length_sq <= 1e-10:
                continue
            candidate = _mul(crease, _dot(delta, crease) / length_sq)
            if allowed(candidate):
                error = _dot(_sub(candidate, delta), _sub(candidate, delta))
                if error < best_error:
                    best, best_error = candidate, error
    return best


class CollisionWorld:
    """Grouped convex colliders with a bounded 3D spatial hash.

    Invalid shape records are skipped; replacing a group copies and validates
    every accepted record before touching the previous group.  Each call reads
    at most 8192 records.  Very large shapes and long queries use a bounded
    index fallback instead of creating millions of grid cells.  ``ignore``
    accepts either shape IDs or whole group IDs.  No renderer nodes are held.
    """

    def __init__(self, cell_size=32):
        self.cell_size = _number(cell_size, 32., .25, 4096.)
        self._groups = {}
        self._shapes = {}
        self._grid = {}
        self._shape_cells = {}
        self._large = set()

    @property
    def shape_count(self):
        return len(self._shapes)

    def _cell_range(self, low, high, limit):
        first = tuple(math.floor(x / self.cell_size) for x in low)
        last = tuple(math.floor(x / self.cell_size) for x in high)
        count = math.prod(last[i] - first[i] + 1 for i in range(3))
        if count > limit:
            return None
        return [(x, y, z)
                for x in range(first[0], last[0] + 1)
                for y in range(first[1], last[1] + 1)
                for z in range(first[2], last[2] + 1)]

    @staticmethod
    def _normalize(group, record):
        if not isinstance(record, Mapping):
            return None
        shape_id, kind = record.get("id"), record.get("type")
        if not isinstance(shape_id, str) or not shape_id or len(shape_id) > 256:
            return None
        if kind not in ("box", "sphere", "cylinder"):
            return None
        center = _shape_vector(record.get("center"))
        if center is None:
            return None
        radius, cosine, sine = 0.0, 1.0, 0.0
        if kind == "box":
            half = _shape_vector(record.get("half"), positive=True)
            if half is None:
                return None
            try:
                heading = float(record.get("heading", 0.))
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(heading):
                return None
            angle = math.radians(heading % 360.)
            cosine, sine = math.cos(angle), math.sin(angle)
            extent = (abs(cosine) * half[0] + abs(sine) * half[1],
                      abs(sine) * half[0] + abs(cosine) * half[1], half[2])
        else:
            try:
                radius = float(record.get("radius"))
                height = float(record.get("height")) if kind == "cylinder" else 2 * radius
            except (TypeError, ValueError, OverflowError):
                return None
            if (not math.isfinite(radius) or not 0 < radius <= _SIZE_LIMIT
                    or not math.isfinite(height) or not 0 < height <= 2 * _SIZE_LIMIT):
                return None
            half = extent = (radius, radius, height / 2.)
        return _Shape(shape_id, group, kind, center, half, radius, cosine, sine,
                      _sub(center, extent), _add(center, extent))

    def set_group(self, group_id, shapes):
        if not isinstance(group_id, str) or not group_id or len(group_id) > 256:
            return
        if isinstance(shapes, Mapping):
            shapes = (shapes,)
        try:
            records = islice(iter(shapes), _MAX_GROUP_SHAPES)
        except TypeError:
            records = ()
        prepared = {}
        for record in records:
            shape = self._normalize(group_id, record)
            if shape is not None:
                prepared[(group_id, shape.id)] = shape
        # All potentially invalid caller data has been handled before removal.
        self.remove_group(group_id)
        if not prepared:
            return
        self._groups[group_id] = tuple(sorted(prepared))
        for key in self._groups[group_id]:
            shape = prepared[key]
            self._shapes[key] = shape
            cells = self._cell_range(shape.low, shape.high, _MAX_SHAPE_CELLS)
            if cells is None:
                self._large.add(key)
            else:
                self._shape_cells[key] = cells
                for cell in cells:
                    self._grid.setdefault(cell, set()).add(key)

    def remove_group(self, group_id):
        if not isinstance(group_id, str):
            return
        for key in self._groups.pop(group_id, ()):
            self._shapes.pop(key, None)
            self._large.discard(key)
            for cell in self._shape_cells.pop(key, ()):
                bucket = self._grid[cell]
                bucket.discard(key)
                if not bucket:
                    del self._grid[cell]

    def clear(self):
        self._groups.clear()
        self._shapes.clear()
        self._grid.clear()
        self._shape_cells.clear()
        self._large.clear()

    def _query(self, low, high, ignore):
        cells = self._cell_range(low, high, _MAX_QUERY_CELLS)
        if cells is None:
            keys = self._shapes.keys()
        else:
            keys = set(self._large)
            for cell in cells:
                keys.update(self._grid.get(cell, ()))
        return [shape for key in sorted(keys)
                if (shape := self._shapes[key]).id not in ignore
                and shape.group not in ignore
                and _bounds_overlap(low, high, shape.low, shape.high)]

    def _near(self, point, delta, radius, segment, ignore, padding=_CONTACT):
        end = _add(point, delta)
        extent = (radius + padding, radius + padding, radius + segment + padding)
        low = tuple(min(point[i], end[i]) - extent[i] for i in range(3))
        high = tuple(max(point[i], end[i]) + extent[i] for i in range(3))
        return self._query(low, high, ignore)

    def _first_hit(self, point, delta, radius, segment, ignore):
        best = None
        for shape in self._near(point, delta, radius, segment, ignore):
            hit = _cast_shape(shape, point, delta, radius, segment)
            if hit is not None and (best is None or hit.fraction < best.fraction - 1e-10):
                best = hit
        return best

    def _recover(self, point, radius, segment, ignore):
        normals, ids = [], []
        for _ in range(_MAX_RECOVERY):
            worst = None
            for shape in self._near(point, _ZERO, radius, segment, ignore):
                gap, normal = _distance(shape, point, radius, segment)
                if gap < -1e-6 and (worst is None or gap < worst[0] - 1e-10):
                    worst = gap, normal, shape.id
            if worst is None:
                break
            gap, normal, shape_id = worst
            point = _add(point, _mul(normal, -gap + _SKIN))
            _append_contact(normals, ids, normal, shape_id)
        return point, normals, ids

    def _slide(self, point, delta, radius, segment, ignore):
        normals, ids = [], []
        remaining = delta
        time_left = 1.0
        for _ in range(_MAX_SWEEPS):
            if _dot(remaining, remaining) < 1e-14:
                break
            hit = self._first_hit(point, remaining, radius, segment, ignore)
            if hit is None:
                point = _add(point, remaining)
                break
            point = _add(point, _mul(remaining, hit.fraction))
            _append_contact(normals, ids, hit.normal, hit.id)
            time_left *= 1.0 - hit.fraction
            # Resolve every active plane against the requested motion. Using
            # the previous projection here pushes outward along one wall at
            # a simultaneous acute-corner contact.
            remaining = _project(_mul(delta, time_left), normals)
        return point, normals, ids

    def _step(self, point, delta, radius, segment, step_height, ignore, baseline):
        horizontal = (delta[0], delta[1], 0.)
        length = math.hypot(*horizontal[:2])
        if length < 1e-7 or delta[2] > 1e-7:
            return None
        direction = _mul(horizontal, 1.0 / length)
        progress = _dot(_sub(baseline[0], point), direction)
        if progress >= length - 1e-5:
            return None
        if not any(abs(n[2]) < 1.0 - 1e-6 for n in baseline[1]):
            return None
        upward = (0., 0., step_height + _SKIN)
        roof = self._first_hit(point, upward, radius, segment, ignore)
        rise = upward[2] * roof.fraction if roof else upward[2]
        if rise <= _SKIN:
            return None
        raised = _add(point, (0., 0., rise))
        across, normals, ids = self._slide(raised, horizontal, radius, segment, ignore)
        if across[2] > point[2] + step_height + 2 * _SKIN:
            return None
        downward = (0., 0., -(rise + max(0., -delta[2]) + _CONTACT))
        floor = self._first_hit(across, downward, radius, segment, ignore)
        if floor:
            if floor.normal[2] < _GROUND_Z:
                # A short physics step can land on the rounded foot edge
                # before its center reaches the tread. Permit that support
                # only when the obstacle's entire top is a legal step up.
                feet = point[2] - segment - radius
                supports = self._near(across, downward, radius, segment, ignore)
                if (floor.normal[2] <= _EPS or not any(
                        shape.id == floor.id and shape.high[2] <= feet + step_height + 1e-7
                        for shape in supports)):
                    return None
            landing = _add(across, _mul(downward, floor.fraction))
            _append_contact(normals, ids, floor.normal, floor.id)
        else:
            # Terrain is owned by the controller.  A complete step over a low
            # obstacle can end back at the original terrain-relative height.
            landing = (across[0], across[1], point[2] + delta[2])
        if landing[2] > point[2] + step_height + 2 * _SKIN:
            return None
        if _dot(_sub(landing, point), direction) <= progress + 1e-5:
            return None
        if any(_distance(shape, landing, radius, segment)[0] < -1e-6
               for shape in self._near(landing, _ZERO, radius, segment, ignore)):
            return None
        return landing, normals, ids

    def _move(self, position, displacement, radius, height, step_height, ignore):
        top = _vector(position)
        delta = _vector(displacement)
        segment = max(0., height / 2. - radius)
        offset = height / 2.
        point = (top[0], top[1], top[2] - offset)
        ignore = _ignored(ignore)
        point, normals, ids = self._recover(point, radius, segment, ignore)
        result = self._slide(point, delta, radius, segment, ignore)
        did_step = False
        if step_height > 0.:
            stepped = self._step(point, delta, radius, segment, step_height, ignore, result)
            if stepped is not None:
                result = stepped
                did_step = True
        end, move_normals, move_ids = result
        for normal in move_normals:
            if not any(_dot(old, normal) > .9999 for old in normals):
                normals.append(normal)
        ids.extend(shape_id for shape_id in move_ids if shape_id not in ids)
        for shape in self._near(end, _ZERO, radius, segment, ignore):
            gap, normal = _distance(shape, end, radius, segment)
            if gap <= _CONTACT:
                _append_contact(normals, ids, normal, shape.id)
        output = Vec3(*(_number(x) for x in (end[0], end[1], end[2] + offset)))
        return MoveResult(output, [Vec3(*n) for n in normals],
                          any(n[2] >= _GROUND_Z or (did_step and n[2] > _EPS)
                              for n in normals),
                          any(n[2] <= -_GROUND_Z for n in normals), ids, did_step)

    def move_capsule(self, position, displacement, radius=.38, height=1.8,
                     step_height=.40, ignore=()):
        radius = _number(radius, .38, .001, 10_000.)
        height = _number(height, 1.8, 2. * radius, 100_000.)
        height = max(2. * radius, height)
        step_height = _number(step_height, .4, 0., min(2., height))
        return self._move(position, displacement, radius, height, step_height, ignore)

    def move_sphere(self, position, displacement, radius=3.0, ignore=()):
        radius = _number(radius, 3., .001, 10_000.)
        return self._move(position, displacement, radius, 0., 0., ignore)

    def overlaps_capsule(self, position, radius=.38, height=1.8, ignore=()):
        radius = _number(radius, .38, .001, 10_000.)
        height = max(2. * radius, _number(height, 1.8, 2. * radius, 100_000.))
        segment = height / 2. - radius
        top = _vector(position)
        point = (top[0], top[1], top[2] - height / 2.)
        return any(_distance(shape, point, radius, segment)[0] < -1e-6
                   for shape in self._near(point, _ZERO, radius, segment, _ignored(ignore)))

    def raycast(self, origin, direction, max_distance, ignore=()):
        origin, direction = _vector(origin), _vector(direction)
        length = math.sqrt(_dot(direction, direction))
        maximum = _number(max_distance, 0., 0., _LIMIT)
        if length <= _EPS or maximum <= 0.:
            return None
        delta = _mul(direction, maximum / length)
        best = None
        for shape in self._near(origin, delta, 0., 0., _ignored(ignore), padding=0.):
            hit = _cast_shape(shape, origin, delta, 0., 0., skin=0., ray=True)
            if hit is not None and (best is None or hit.fraction < best.fraction - 1e-10):
                best = hit
        if best is None:
            return None
        return RayHit(best.fraction * maximum, Vec3(*best.normal), best.id,
                      Vec3(*_add(origin, _mul(delta, best.fraction))))
