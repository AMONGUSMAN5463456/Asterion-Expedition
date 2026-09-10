"""Bounded, deterministic orbital routes around clearance spheres.

The caller supplies exclusion radii already expanded for the ship.  Every
returned straight segment is checked against every supplied sphere, including
after conversion to Panda's single-precision Vec3.  This is a conservative
sampled visibility search, not a complete or shortest-path solver: an empty
list means unsafe endpoints, invalid input, or no route within the budget.

Touching a boundary is allowed.  An embedded endpoint is rejected; recovery
from an existing overlap belongs to the collision controller, not autopilot.
"""

from __future__ import annotations

from heapq import heappop, heappush
from itertools import islice, product
import math

from panda3d.core import Vec3


_MAX_OBSTACLES = 256
_MAX_NODES = 384
_MAX_EDGES = 32000
_MAX_ROUNDS = 4
_MAX_DETOURS = 10
_COORDINATE_LIMIT = 1.0e9
_SHELL_DIRECTIONS = tuple(
    (x / math.sqrt(x*x + y*y + z*z),
     y / math.sqrt(x*x + y*y + z*z),
     z / math.sqrt(x*x + y*y + z*z))
    for x, y, z in product((-1, 0, 1), repeat=3) if x or y or z
)


def _point(value):
    point = tuple(float(value[i]) for i in range(3))
    if any(not math.isfinite(n) or abs(n) > _COORDINATE_LIMIT for n in point):
        raise ValueError("Navigation coordinates must be finite and bounded")
    return point


def _subtract(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _distance(a, b):
    dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _unit(vector):
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if not length:
        return (1.0, 0.0, 0.0)
    return (x / length, y / length, z / length)


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0])


def _intersects(a, b, sphere):
    """Exact closest-point test, with only double-precision contact tolerance."""
    x, y, z, radius = sphere
    a0, a1, a2 = a
    b0, b1, b2 = b
    # Most orbital bodies miss even the segment's axis-aligned bounds.
    if a0 < b0:
        lo0, hi0 = a0, b0
    else:
        lo0, hi0 = b0, a0
    if a1 < b1:
        lo1, hi1 = a1, b1
    else:
        lo1, hi1 = b1, a1
    if a2 < b2:
        lo2, hi2 = a2, b2
    else:
        lo2, hi2 = b2, a2
    if (x + radius < lo0 or x - radius > hi0 or
            y + radius < lo1 or y - radius > hi1 or
            z + radius < lo2 or z - radius > hi2):
        return False
    dx, dy, dz = b0 - a0, b1 - a1, b2 - a2
    ox, oy, oz = x - a0, y - a1, z - a2
    length2 = dx*dx + dy*dy + dz*dz
    fraction = max(0.0, min(1.0, (ox*dx + oy*dy + oz*dz) / length2)) if length2 else 0.0
    ox, oy, oz = ox - fraction*dx, oy - fraction*dy, oz - fraction*dz
    radius2 = radius*radius
    return ox*ox + oy*oy + oz*oz < radius2 - max(1.0, radius2)*1.0e-12


def _blocker(a, b, spheres):
    for index, sphere in enumerate(spheres):
        if _intersects(a, b, sphere):
            return index
    return None


class _BudgetExhausted(Exception):
    pass


class _Search:
    def __init__(self, start, goal, spheres):
        self.nodes = [start, goal]
        self.node_keys = {start, goal}
        self.spheres = spheres
        self.edges = {}
        self.blockers = {}
        self._dist = {}
        self.forward = _unit(_subtract(goal, start))
        axis = min(range(3), key=lambda i: abs(self.forward[i]))
        reference = tuple(1.0 if i == axis else 0.0 for i in range(3))
        self.side = _unit(_cross(self.forward, reference))
        self.up = _cross(self.forward, self.side)

    def _record_blocker(self, index):
        if index is not None:
            self.blockers[index] = self.blockers.get(index, 0) + 1

    def _leg(self, a, b):
        key = (a, b) if a <= b else (b, a)
        distance = self._dist.get(key)
        if distance is None:
            na, nb = self.nodes[a], self.nodes[b]
            dx, dy, dz = na[0] - nb[0], na[1] - nb[1], na[2] - nb[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            self._dist[key] = distance
        return distance

    def edge_clear(self, a, b):
        key = (a, b) if a <= b else (b, a)
        if key not in self.edges:
            if len(self.edges) >= _MAX_EDGES:
                raise _BudgetExhausted
            hit = _blocker(self.nodes[a], self.nodes[b], self.spheres)
            self.edges[key] = hit is None
            self._record_blocker(hit)
        return self.edges[key]

    def add_shell(self, sphere):
        """A circumscribed 26-direction shell plus endpoint-facing samples.

        Inflation keeps chords between adjacent samples outside the sphere.
        Radial endpoint samples allow departure from a touching boundary even
        when the regular sample directions are poorly aligned with it.
        """
        center, radius = sphere[:3], sphere[3]
        cx, cy, cz = center
        reach = radius * 1.25 + max(.001, max(abs(cx), abs(cy), abs(cz)) * 2e-6)
        nodes = self.nodes
        forward, side, up = self.forward, self.side, self.up
        f0, f1, f2 = forward
        s0, s1, s2 = side
        u0, u1, u2 = up
        directions = [_unit(_subtract(nodes[i], center)) for i in (0, 1)]
        for x, y, z in _SHELL_DIRECTIONS:
            directions.append((x * f0 + y * s0 + z * u0,
                               x * f1 + y * s1 + z * u1,
                               x * f2 + y * s2 + z * u2))
        node_keys = self.node_keys
        for direction in directions:
            if len(nodes) >= _MAX_NODES:
                break
            dx, dy, dz = direction
            # Check the coordinates the controller will actually receive.
            try:
                node = _point(Vec3(cx + dx * reach, cy + dy * reach, cz + dz * reach))
            except (ValueError, OverflowError):
                continue
            if node in node_keys:
                continue
            hit = _blocker(node, node, self.spheres)
            if hit is None:
                node_keys.add(node)
                nodes.append(node)
            else:
                self._record_blocker(hit)

    def solve(self):
        """A* node ordering; stop at the first verified connection to goal."""
        costs = {0: 0.0}
        parents = {}
        closed = set()
        leg = self._leg
        edge_clear = self.edge_clear
        node_count = len(self.nodes)
        pending = [(leg(0, 1), 0.0, 0)]
        while pending:
            _, cost, current = heappop(pending)
            if current in closed or cost != costs[current]:
                continue
            if edge_clear(current, 1):
                path = [1, current]
                while path[-1] != 0:
                    path.append(parents[path[-1]])
                return list(reversed(path))
            closed.add(current)
            for other in range(2, node_count):
                if other in closed:
                    continue
                new_cost = cost + leg(current, other)
                if new_cost >= costs.get(other, math.inf):
                    continue
                if edge_clear(current, other):
                    costs[other] = new_cost
                    parents[other] = current
                    heappush(pending, (new_cost + leg(other, 1), new_cost, other))
        return []

    def finish(self, path):
        # Greedy shortcuts keep the ship from following redundant shell edges.
        shortened = [path[0]]
        index = 0
        try:
            while index < len(path) - 1:
                target = len(path) - 1
                while target > index + 1 and not self.edge_clear(path[index], path[target]):
                    target -= 1
                shortened.append(path[target])
                index = target
        except _BudgetExhausted:
            shortened = path
        points = [self.nodes[index] for index in shortened]
        # Independent final check also covers any future changes to smoothing.
        if any(_blocker(a, b, self.spheres) is not None for a, b in zip(points, points[1:])):
            return []
        return [Vec3(*point) for point in points[1:]]


def plan_route(start, goal, obstacles) -> list[Vec3]:
    """Return safe waypoints including goal and excluding start, or ``[]``.

    ``start`` and ``goal`` accept Vec3 or numeric triples.  ``obstacles`` is an
    iterable of dictionaries with ``center``, nonnegative ``radius`` and an
    optional stable ``id``.  Zero-radius obstacles have no solid interior.
    Inputs are not mutated.  Ordering obstacles differently does not change the
    result.  A clear zero-length trip succeeds with one copy of ``goal``.

    Work is capped at 256 input spheres, 384 nodes, 32,000 visibility edges,
    ten sampled obstacle shells and four search rounds.  Nonfinite/unsupported
    inputs, coordinates or radii beyond 1e9, embedded endpoints, and exhausted
    searches fail closed.  A failed bounded search does not prove impossibility.
    """
    try:
        start, goal = _point(Vec3(*_point(start))), _point(Vec3(*_point(goal)))
        records = list(islice(iter(obstacles), _MAX_OBSTACLES + 1))
        if len(records) > _MAX_OBSTACLES:
            return []
        ordered = []
        for record in records:
            center, radius = _point(record["center"]), float(record["radius"])
            identity = record.get("id", "")
            if (not isinstance(identity, str) or not math.isfinite(radius) or
                    radius < 0 or radius > _COORDINATE_LIMIT):
                return []
            if radius:
                ordered.append((*center, radius, identity))
        spheres = [record[:4] for record in sorted(set(ordered))]
    except (TypeError, ValueError, IndexError, KeyError, OverflowError, AttributeError):
        return []
    if _blocker(start, start, spheres) is not None or _blocker(goal, goal, spheres) is not None:
        return []
    direct_blockers = [i for i, sphere in enumerate(spheres) if _intersects(start, goal, sphere)]
    if not direct_blockers:
        return [Vec3(*goal)]

    search = _Search(start, goal, spheres)
    search.blockers = {index: 1 for index in direct_blockers}
    sampled = set()
    try:
        for round_index in range(_MAX_ROUNDS):
            candidates = sorted((i for i in search.blockers if i not in sampled),
                                key=lambda i: (-search.blockers[i], -spheres[i][3], i))
            count = min(4, _MAX_DETOURS - len(sampled))
            for index in candidates[:count]:
                sampled.add(index)
                search.add_shell(spheres[index])
            if round_index == 1:
                # A containing shell supplies exterior waypoints when a group
                # of overlapping bodies hides their individual shell samples.
                group = [spheres[i] for i in direct_blockers]
                low = [min(s[i] - s[3] for s in group) for i in range(3)]
                high = [max(s[i] + s[3] for s in group) for i in range(3)]
                center = tuple((low[i] + high[i])*.5 for i in range(3))
                radius = max(_distance(center, s[:3]) + s[3] for s in group)
                search.add_shell((*center, radius))
            path = search.solve()
            if path:
                return search.finish(path)
            if (len(search.nodes) >= _MAX_NODES or len(sampled) >= _MAX_DETOURS or
                    (round_index > 0 and not candidates)):
                break
    except _BudgetExhausted:
        pass
    return []
