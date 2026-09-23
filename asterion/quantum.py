"""Deterministic quantum travel along a caller-verified orbital polyline.

This model does not own a ship, scene, save, fuel tank, or collision world.
``begin`` quotes a route and spools it; only a later ``launch`` enters transit.
The caller deducts ``cost`` exactly once after a successful launch.  A launch
rechecks available fuel, and cancelling before launch has no fuel cost.

``advance`` returns the current world position.  ``motion_points`` also lists
every waypoint crossed by that call, followed by its endpoint.  The caller
must sweep those segments in order, rather than sweeping one chord between
frame endpoints, then ``cancel(position=...)`` if a collision intervenes.
Route validation here checks numeric shape and bounds; obstacle clearance is
the caller's responsibility (normally ``navigation.plan_route``).
"""

from __future__ import annotations

from bisect import bisect_right
from itertools import islice
import math

from panda3d.core import Vec3


SPOOL_DURATION = 3.0
COOLDOWN_DURATION = 4.0
MAX_SPEED = 55000.0
RAMP_DURATION = 1.5
BASE_FUEL_COST = 3.0
METRES_PER_FUEL = 100000.0
MAX_WAYPOINTS = 384
_COORDINATE_LIMIT = 1.0e9
_TIME_EPSILON = 1.0e-9
_ZERO = (0.0, 0.0, 0.0)


def _point(value):
    """Use exactly the finite, bounded coordinates exposed to Panda callers."""
    if len(value) != 3:
        raise ValueError("Quantum coordinates need three components")
    point = tuple(float(value[i]) for i in range(3))
    if any(not math.isfinite(n) or abs(n) > _COORDINATE_LIMIT for n in point):
        raise ValueError("Quantum coordinates must be finite and bounded")
    return tuple(Vec3(*point))


def _has_fuel(fuel, cost):
    try:
        fuel = float(fuel)
        return math.isfinite(fuel) and fuel >= cost
    except (TypeError, ValueError, OverflowError):
        return False


class QuantumDrive:
    """Idle -> spooling -> ready -> transit -> cooldown -> idle.

    ``active`` is true during spooling, ready and transit; normal flight may
    resume during cooldown.  All public vectors are copies.  Work per call is
    bounded by ``MAX_WAYPOINTS`` even for huge time steps or infinite route
    iterators.  Invalid routes/fuel return False without changing the model;
    invalid time steps raise ValueError without changing the model.
    """

    def __init__(self):
        self._phase = "idle"
        self._position = _ZERO
        self._velocity = _ZERO
        self._points = ()
        self._ends = ()
        self._motion_points = ()
        self._completed_this_step = False
        self._target_name = ""
        self._distance = 0.0
        self._travelled = 0.0
        self._cost = 0.0
        self._peak_speed = 0.0
        self._cruise_duration = 0.0
        self._duration = 0.0
        self._elapsed = 0.0
        self._spool_elapsed = 0.0
        self._cooldown_remaining = 0.0

    @property
    def phase(self):
        return self._phase

    @property
    def active(self):
        return self._phase in ("spooling", "ready", "transit")

    @property
    def travelling(self):
        return self._phase == "transit"

    @property
    def position(self):
        return Vec3(*self._position)

    @property
    def velocity(self):
        return Vec3(*self._velocity)

    @property
    def waypoints(self):
        """Copied route endpoints, including goal and excluding start."""
        return tuple(Vec3(*point) for point in self._points[1:])

    @property
    def motion_points(self):
        """Copied endpoints of the ordered swept segments from last advance."""
        return tuple(Vec3(*point) for point in self._motion_points)

    @property
    def next_direction(self):
        if not self._ends or self._travelled >= self._distance:
            return Vec3(0)
        index = min(bisect_right(self._ends, self._travelled), len(self._ends) - 1)
        a, b = self._points[index:index + 2]
        length = self._ends[index] - (self._ends[index - 1] if index else 0.0)
        return Vec3(*((b[i] - a[i]) / length for i in range(3)))

    @property
    def progress(self):
        return self._travelled / self._distance if self._distance else 0.0

    @property
    def spool_progress(self):
        return min(1.0, self._spool_elapsed / SPOOL_DURATION)

    @property
    def cooldown_progress(self):
        if self._phase != "cooldown":
            return 0.0
        return 1.0 - self._cooldown_remaining / COOLDOWN_DURATION

    @property
    def cooldown_remaining(self):
        return self._cooldown_remaining

    @property
    def remaining(self):
        """Route distance remaining, in world metres."""
        return max(0.0, self._distance - self._travelled)

    @property
    def eta(self):
        """Flight time remaining; excludes spool and cooldown."""
        if self._phase in ("spooling", "ready", "transit"):
            return max(0.0, self._duration - self._elapsed)
        return 0.0

    @property
    def duration(self):
        return self._duration

    @property
    def total_distance(self):
        return self._distance

    @property
    def cost(self):
        return self._cost

    @property
    def target_name(self):
        return self._target_name

    def begin(self, start, waypoints, target_name="", fuel=0.0):
        """Validate/quote a route and begin spooling, only while idle.

        Waypoints follow ``plan_route``: include the destination but omit the
        start.  Consecutive duplicate points are harmless; a zero-length or
        over-budget route fails.  The supplied fuel is inspected, never spent.
        """
        if self._phase != "idle" or not isinstance(target_name, str):
            return False
        try:
            records = list(islice(iter(waypoints), MAX_WAYPOINTS + 1))
            if not records or len(records) > MAX_WAYPOINTS:
                return False
            points = [_point(start)]
            ends = []
            distance = 0.0
            for record in records:
                point = _point(record)
                length = math.dist(points[-1], point)
                if length:
                    next_distance = distance + length
                    if next_distance <= distance:
                        return False
                    distance = next_distance
                    ends.append(distance)
                    points.append(point)
        except (TypeError, ValueError, IndexError, KeyError, OverflowError, AttributeError):
            return False
        if not distance:
            return False
        cost = BASE_FUEL_COST + distance / METRES_PER_FUEL
        if not _has_fuel(fuel, cost):
            return False

        self._phase = "spooling"
        self._points = tuple(points)
        self._ends = tuple(ends)
        self._position = points[0]
        self._velocity = _ZERO
        self._motion_points = ()
        self._completed_this_step = False
        self._target_name = target_name
        self._distance = distance
        self._travelled = 0.0
        self._cost = cost
        self._peak_speed = min(MAX_SPEED, distance / RAMP_DURATION)
        self._cruise_duration = max(0.0, distance / self._peak_speed - RAMP_DURATION)
        self._duration = 2.0 * RAMP_DURATION + self._cruise_duration
        self._elapsed = 0.0
        self._spool_elapsed = 0.0
        self._cooldown_remaining = 0.0
        return True

    def launch(self, fuel):
        """Explicit second activation.  Caller spends cost only on True."""
        if self._phase != "ready" or not _has_fuel(fuel, self._cost):
            return False
        self._phase = "transit"
        self._motion_points = ()
        self._completed_this_step = False
        return True

    def cancel(self, position=None):
        """Stop at current position, or a caller's swept-collision correction.

        Transit cancellation starts cooldown and never refunds its reserved
        fuel.  A correction is also accepted immediately after an advance that
        completed transit, including a large step that consumed cooldown.
        """
        if not self.active and not self._completed_this_step:
            return False
        try:
            position = self._position if position is None else _point(position)
        except (TypeError, ValueError, IndexError, KeyError, OverflowError, AttributeError):
            return False
        was_transit = self.travelling or self._completed_this_step
        self._phase = "cooldown" if was_transit else "idle"
        self._position = position
        self._velocity = _ZERO
        self._motion_points = ()
        self._completed_this_step = False
        self._cooldown_remaining = COOLDOWN_DURATION if was_transit else 0.0
        return True

    def _sample(self, elapsed):
        """Analytic integral of smoothstep speed ramps, with optional cruise."""
        if elapsed >= self._duration:
            return self._distance, 0.0
        if elapsed < RAMP_DURATION:
            fraction = elapsed / RAMP_DURATION
            distance = self._peak_speed * RAMP_DURATION * (fraction**3 - .5*fraction**4)
        elif elapsed < RAMP_DURATION + self._cruise_duration:
            return self._peak_speed * (elapsed - .5*RAMP_DURATION), self._peak_speed
        else:
            fraction = (self._duration - elapsed) / RAMP_DURATION
            distance = self._distance - self._peak_speed * RAMP_DURATION * (fraction**3 - .5*fraction**4)
        speed = self._peak_speed * fraction*fraction * (3.0 - 2.0*fraction)
        return max(0.0, min(self._distance, distance)), speed

    def _move(self, distance, speed):
        previous = self._travelled
        self._travelled = distance
        if distance >= self._distance:
            position = self._points[-1]
            velocity = _ZERO
        else:
            index = min(bisect_right(self._ends, distance), len(self._ends) - 1)
            start_distance = self._ends[index - 1] if index else 0.0
            length = self._ends[index] - start_distance
            fraction = (distance - start_distance) / length
            a, b = self._points[index:index + 2]
            position = tuple(a[i] + (b[i] - a[i]) * fraction for i in range(3))
            velocity = tuple((b[i] - a[i]) * (speed / length) for i in range(3))
        path = [self._points[i + 1] for i, endpoint in enumerate(self._ends)
                if previous < endpoint <= distance]
        if distance > previous and (not path or path[-1] != position):
            path.append(position)
        self._motion_points = tuple(path)
        self._position = position
        self._velocity = velocity

    def advance(self, dt):
        """Advance a finite nonnegative number of seconds; never auto-launch.

        Large steps cross every route segment in bounded work.  Residual time
        after arrival counts toward cooldown; residual spool time stops at
        ready, where a separate explicit launch is required.
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Quantum time step must be finite and nonnegative") from error
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError("Quantum time step must be finite and nonnegative")
        self._motion_points = ()
        self._completed_this_step = False
        if self._phase == "spooling":
            self._spool_elapsed = min(SPOOL_DURATION, self._spool_elapsed + dt)
            if self._spool_elapsed >= SPOOL_DURATION - _TIME_EPSILON:
                self._spool_elapsed = SPOOL_DURATION
                self._phase = "ready"
        elif self._phase == "transit":
            remaining_time = max(0.0, self._duration - self._elapsed)
            step = min(dt, remaining_time)
            self._elapsed = min(self._duration, self._elapsed + step)
            if self._elapsed >= self._duration - _TIME_EPSILON:
                self._elapsed = self._duration
            self._move(*self._sample(self._elapsed))
            if self._elapsed >= self._duration:
                self._phase = "cooldown"
                self._cooldown_remaining = COOLDOWN_DURATION
                self._completed_this_step = True
                self._advance_cooldown(dt - step)
        elif self._phase == "cooldown":
            self._advance_cooldown(dt)
        return self.position

    def _advance_cooldown(self, dt):
        self._cooldown_remaining = max(0.0, self._cooldown_remaining - dt)
        if self._cooldown_remaining < _TIME_EPSILON:
            self._cooldown_remaining = 0.0
            self._phase = "idle"
