"""First-person exploration and inertial ship controls, without a global task.

Surface ``position`` is the player's eye (terrain height + 1.8 when standing).
In ``flight`` and ``orbit`` it is the ship/camera center.  Panda coordinates are
Z-up; heading zero faces +Y.  Root app actions deliberately own E/F/etc.
"""

from __future__ import annotations

import math
from itertools import combinations

from direct.showbase.DirectObject import DirectObject
from panda3d.core import KeyboardButton, Vec3, WindowProperties


EYE_HEIGHT = 1.8
PLAYER_RADIUS = 0.38
STEP_HEIGHT = 0.40
SHIP_RADIUS = 3.0
SHIP_CLEARANCE = 4.0
MAX_FRAME_DT = 0.25
PHYSICS_STEP = 1.0 / 120.0
JUMP_BUFFER = 0.16
COYOTE_TIME = 0.13
_COORDINATE_LIMIT = 10_000_000.0


def _number(value, default=0.0, minimum=-math.inf, maximum=math.inf):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _vector(value, fallback=(0.0, 0.0, 0.0)):
    try:
        return Vec3(*(_number(value[i], fallback[i], -_COORDINATE_LIMIT,
                              _COORDINATE_LIMIT) for i in range(3)))
    except (TypeError, IndexError, KeyError):
        return Vec3(*fallback)


def _accelerate(velocity, target, response, dt):
    """Exact integration of an exponential approach to a constant target."""
    decay = math.exp(-response * dt)
    displacement = target * dt + (velocity - target) * ((1.0 - decay) / response)
    return target + (velocity - target) * decay, displacement


def _limited(vector, maximum):
    length = vector.length()
    return vector * (maximum / length) if length > maximum else vector


class PlayerController(DirectObject):
    """Input owner; the application calls :meth:`update` once per frame.

    ``settings['mouse_capture'] = False`` selects click-drag mouse look.
    Absolute recentering is used for capture because it works consistently on
    macOS as well as Windows/Linux. If pointer warping is unavailable, visible
    click-drag look is selected automatically. Arrow keys always turn the view.
    """

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.position = Vec3(0, 0, EYE_HEIGHT)
        self.velocity = Vec3(0)
        self.heading = 0.0
        self.pitch = 0.0
        self.mode = "surface"
        self.speed = 0.0
        self.grounded = False
        self.jetpacking = False
        self.boosting = False
        # Read-only presentation state. Physical position never includes bob,
        # landing/step smoothing, banking, or FOV effects.
        self.throttle = 0.0
        self.braking = False
        self.drifting = False
        self.flight_assist = True
        self.vertical_speed = 0.0
        self.collision_feedback = 0.0
        self.collision_ids = ()
        self.ceiling = False
        self.camera_offset = Vec3(0)
        self.camera_roll = 0.0
        self.fov_offset = 0.0
        self.fov = 78.0
        self.keys = {key: False for key in (
            "w", "a", "s", "d", "space", "shift", "control",
            "arrow_left", "arrow_right", "arrow_up", "arrow_down",
        )}
        self.mouse_down = False
        self._drag_down = False
        self._enabled = False
        self._focused = True
        self._destroyed = False
        self._capture_requested = True
        self._capture_active = False
        self._mouse_last = None
        self._roll = 0.0
        self._collision_world = None
        self._solid_grounded = False
        self._sprint_amount = 0.0
        self._mouse_velocity = Vec3(0)
        self._camera_motion = 0.35
        self._base_fov = 78.0
        self._bob_phase = 0.0
        self._bob_amount = 0.0
        self._camera_step = 0.0
        self._landing_offset = 0.0
        self._jump_active = False
        self._jump_cut = False
        self._space_was_down = False
        self._space_hold = 0.0
        self._jump_buffer = 0.0
        self._coyote_time = 0.0
        self._last_height = 0.0
        self._last_valid_position = Vec3(self.position)
        disable_mouse = getattr(app, "disableMouse", None)
        if callable(disable_mouse):
            disable_mouse()
        self._bind_inputs()

    def _bind_inputs(self):
        # Panda includes active modifier names in button events. Polling the
        # physical buttons below also covers platform-specific modifier order
        # and a modifier released before its movement key.
        modifiers = ("shift", "control", "alt", "meta")
        prefixes = [""] + ["-".join(group) + "-"
                           for count in range(1, 5)
                           for group in combinations(modifiers, count)]
        for key in self.keys:
            for prefix in prefixes:
                self.accept(prefix + key, self._set_key, [key, True])
                self.accept(prefix + key + "-up", self._set_key, [key, False])
        for source, key in (("lshift", "shift"), ("rshift", "shift"),
                            ("lcontrol", "control"), ("rcontrol", "control")):
            self.accept(source, self._set_key, [key, True])
            self.accept(source + "-up", self._set_key, [key, False])
        for prefix in prefixes:
            self.accept(prefix + "mouse1", self._set_mouse, [1, True])
            self.accept(prefix + "mouse1-up", self._set_mouse, [1, False])
            self.accept(prefix + "mouse3", self._set_mouse, [3, True])
            self.accept(prefix + "mouse3-up", self._set_mouse, [3, False])
        self.accept("window-event", self._window_event)

    def _set_key(self, key, down):
        down = bool(down and self._enabled and self._focused)
        if key == "space" and down and not self.keys.get(key):
            # Retain a quick press/release that occurs between render frames.
            self._jump_buffer = JUMP_BUFFER
        self.keys[key] = down

    def _set_mouse(self, button, down):
        down = bool(down and self._enabled and self._focused)
        if button == 1:
            self.mouse_down = down
        else:
            self._drag_down = down
        self._mouse_last = None

    def reset_keys(self):
        for key in self.keys:
            self.keys[key] = False
        self.mouse_down = False
        self._drag_down = False
        self._space_was_down = False
        self._space_hold = 0.0
        self._jump_buffer = 0.0
        self._mouse_last = None
        self._mouse_velocity = Vec3(0)

    def set_collision_world(self, world_or_none):
        """Use a streamed CollisionWorld; None keeps terrain-only operation.

        The renderer owns its lifetime and shape groups. Changing worlds clears
        support/contact state without changing the player's transform.
        """
        self._collision_world = world_or_none
        self._solid_grounded = False
        self.grounded = False
        self.ceiling = False
        self.collision_ids = ()
        self.collision_feedback = 0.0

    def set_mode(self, mode, position, heading=0, pitch=0):
        if mode not in ("surface", "flight", "orbit"):
            raise ValueError("Controller mode must be surface, flight, or orbit")
        self.mode = mode
        fallback = (0, 0, EYE_HEIGHT if mode == "surface" else SHIP_CLEARANCE)
        self.position = _vector(position, fallback)
        self._last_valid_position = Vec3(self.position)
        self.velocity = Vec3(0)
        self.heading = _number(heading) % 360.0
        self.pitch = _number(pitch, 0, -85.0, 85.0)
        self.speed = 0.0
        self.grounded = self.jetpacking = self.boosting = False
        self._solid_grounded = self.braking = self.drifting = False
        self.throttle = self.vertical_speed = 0.0
        self.collision_feedback = 0.0
        self.collision_ids = ()
        self.ceiling = False
        self._roll = self._coyote_time = 0.0
        self._sprint_amount = self._bob_phase = self._bob_amount = 0.0
        self._camera_step = self._landing_offset = 0.0
        self._jump_active = self._jump_cut = False
        self.camera_offset = Vec3(0)
        self.camera_roll = self.fov_offset = 0.0
        self.fov = self._base_fov
        self._last_height = 0.0
        self.reset_keys()
        self._apply_camera()

    def set_enabled(self, enabled):
        enabled = bool(enabled) and not self._destroyed
        changed = enabled != self._enabled
        self._enabled = enabled
        if not enabled:
            self.reset_keys()
            self.jetpacking = self.boosting = False
            self.braking = self.drifting = False
            self.throttle = 0.0
        if changed:
            self._configure_pointer()

    def _window(self):
        window = getattr(self.app, "win", None)
        if window is None or not all(callable(getattr(window, method, None))
                                     for method in ("requestProperties", "getPointer",
                                                    "movePointer", "getXSize", "getYSize")):
            return None  # GraphicsBuffer / headless rendering has no pointer.
        return window

    def _configure_pointer(self):
        self._mouse_last = None
        self._capture_active = False
        window = self._window()
        if window is None:
            return
        capture = self._enabled and self._focused and self._capture_requested
        try:
            if capture:
                width, height = window.getXSize(), window.getYSize()
                capture = width > 0 and height > 0 and bool(
                    window.movePointer(0, width // 2, height // 2))
            properties = WindowProperties()
            properties.setMouseMode(WindowProperties.M_absolute)
            properties.setCursorHidden(capture)
            window.requestProperties(properties)
            self._capture_active = capture
        except (AttributeError, TypeError, RuntimeError):
            # An offscreen or closing window must never prevent saving/quitting.
            self._capture_active = False

    def _window_event(self, window):
        if window is not getattr(self.app, "win", None):
            return
        try:
            properties = window.getProperties()
            focused = (not properties.hasForeground() or properties.getForeground())
            if properties.hasMinimized() and properties.getMinimized():
                focused = False
        except (AttributeError, TypeError, RuntimeError):
            return
        if not focused or focused != self._focused:
            self.reset_keys()
        self._focused = bool(focused)
        self._configure_pointer()

    def _poll_keys(self):
        watcher = getattr(self.app, "mouseWatcherNode", None)
        if watcher is None or self._window() is None:
            return
        buttons = {key: KeyboardButton.asciiKey(key) for key in ("w", "a", "s", "d")}
        buttons.update(space=KeyboardButton.space(), shift=KeyboardButton.shift(),
                       control=KeyboardButton.control(), arrow_up=KeyboardButton.up(),
                       arrow_down=KeyboardButton.down(), arrow_left=KeyboardButton.left(),
                       arrow_right=KeyboardButton.right())
        try:
            for key, button in buttons.items():
                self.keys[key] = bool(watcher.isButtonDown(button))
        except (AttributeError, TypeError, RuntimeError):
            pass

    def _mouse_delta(self):
        window = self._window()
        if window is None:
            return 0.0, 0.0
        try:
            pointer = window.getPointer(0)
            if hasattr(pointer, "getInWindow") and not pointer.getInWindow():
                self._mouse_last = None
                return 0.0, 0.0
            x, y = pointer.getX(), pointer.getY()
            width, height = window.getXSize(), window.getYSize()
            if width <= 0 or height <= 0:
                return 0.0, 0.0
            if self._capture_active:
                dx, dy = x - width // 2, y - height // 2
                if not window.movePointer(0, width // 2, height // 2):
                    self._configure_pointer()
                    return 0.0, 0.0
            else:
                previous, self._mouse_last = self._mouse_last, (x, y)
                if previous is None or not (self.mouse_down or self._drag_down):
                    return 0.0, 0.0
                dx, dy = x - previous[0], y - previous[1]
            return (_number(dx, 0, -width / 2, width / 2),
                    _number(dy, 0, -height / 2, height / 2))
        except (AttributeError, TypeError, RuntimeError):
            return 0.0, 0.0

    def _key(self, key):
        if key == "control":
            return bool(self.keys.get(key) or self.keys.get("ctrl"))
        return bool(self.keys.get(key, False))

    def _axis(self, positive, negative):
        return int(self._key(positive)) - int(self._key(negative))

    def forward(self):
        heading = math.radians(_number(self.heading) % 360.0)
        pitch = math.radians(_number(self.pitch, 0, -89.0, 89.0))
        return Vec3(-math.sin(heading) * math.cos(pitch),
                    math.cos(heading) * math.cos(pitch), math.sin(pitch))

    def _height(self, height_fn, x, y):
        try:
            height = height_fn(float(x), float(y))
        except (TypeError, ValueError, OverflowError, RuntimeError):
            height = self._last_height
        self._last_height = _number(height, self._last_height, -100_000, 100_000)
        return self._last_height

    def update(self, dt, height_fn, vitals, upgrades, settings,
               enabled=True, gravity=12):
        if self._destroyed:
            return
        settings = settings or {}
        self.flight_assist = bool(settings.get("flight_assist", True))
        self._camera_motion = _number(settings.get("camera_motion", 0.35), 0.35, 0, 1)
        self._base_fov = _number(settings.get("fov", 78), 78, 60, 100)
        capture = bool(settings.get("mouse_capture", True))
        if capture != self._capture_requested:
            self._capture_requested = capture
            self._configure_pointer()
        self.set_enabled(enabled)
        if not self._enabled or not self._focused:
            return
        dt = _number(dt, 0, 0, MAX_FRAME_DT)
        if dt == 0:
            return
        self.position = _vector(self.position, self._last_valid_position)
        self.velocity = _limited(_vector(self.velocity), 2000.0)
        self._poll_keys()
        dx, dy = self._mouse_delta()
        sensitivity = _number(settings.get("sensitivity", 0.16), 0.16, 0.015, 1.5)
        invert = -1.0 if settings.get("invert_y", False) else 1.0
        smoothing = _number(settings.get("mouse_smoothing", 0), 0, 0, 1)
        mouse_rate = Vec3(dx / dt, dy / dt, 0)
        space = self._key("space")
        if space and not self._space_was_down:
            self._jump_buffer = JUMP_BUFFER
        self._space_was_down = space
        self.jetpacking = self.boosting = False
        self.braking = self.ceiling = False
        self.collision_ids = ()
        steps = max(1, math.ceil(dt / PHYSICS_STEP))
        step = dt / steps
        gravity = _number(gravity, 12, 1, 40)
        for _ in range(steps):
            if smoothing > 0:
                self._mouse_velocity, mouse_motion = _accelerate(
                    self._mouse_velocity, mouse_rate, 1.0 / (0.018 + 0.10 * smoothing), step)
                mouse_x, mouse_y = mouse_motion.x, mouse_motion.y
            else:
                self._mouse_velocity = Vec3(0)
                mouse_x, mouse_y = dx / steps, dy / steps
            turn = (-mouse_x * sensitivity
                    + self._axis("arrow_left", "arrow_right") * 100.0 * step)
            tilt = (-mouse_y * sensitivity * invert
                    + self._axis("arrow_up", "arrow_down") * 80.0 * step)
            # A midpoint view direction keeps keyboard steering and travel
            # consistent at 30, 60, and high refresh rates.
            self._turn_view(turn * 0.5, tilt * 0.5)
            self._space_hold = self._space_hold + step if space else 0.0
            self.collision_feedback *= math.exp(-5.0 * step)
            self._camera_step *= math.exp(-15.0 * step)
            self._landing_offset *= math.exp(-10.0 * step)
            previous = Vec3(self.position)
            if self.mode == "surface":
                self._walk(step, height_fn, vitals, upgrades or {}, gravity)
            else:
                self._fly(step, height_fn, vitals, upgrades or {})
            self._turn_view(turn * 0.5, tilt * 0.5)
            self._update_camera_motion(step, previous, turn / step)
        self.position = _vector(self.position, self._last_valid_position)
        self.velocity = _limited(_vector(self.velocity), 2000.0)
        self.speed = _number(self.velocity.length(), 0, 0, 2000)
        self.vertical_speed = float(self.velocity.z)
        self.drifting = (self.mode != "surface" and not self.flight_assist
                         and not self.braking and self.speed > 1.0)
        self._last_valid_position = Vec3(self.position)
        self._apply_camera()

    def _turn_view(self, turn, tilt):
        self.heading = (_number(self.heading) + turn) % 360.0
        limit = 85.0 if self.mode == "surface" else 89.0
        self.pitch = _number(_number(self.pitch) + tilt, 0, -limit, limit)

    @staticmethod
    def _fall(velocity, gravity, dt):
        """Integrate gravity exactly, including the terminal-speed crossing."""
        terminal = -55.0
        falling = min(dt, max(0.0, (velocity - terminal) / gravity))
        movement = velocity * falling - 0.5 * gravity * falling * falling
        movement += terminal * (dt - falling)
        return max(terminal, velocity - gravity * dt), movement

    def _clip_contact(self, normal):
        """Remove velocity into a surface, retaining tangent momentum."""
        normal = _vector(normal)
        if normal.lengthSquared() < 1e-8:
            return
        normal.normalize()
        inward = self.velocity.dot(normal)
        if inward >= 0:
            return
        self.velocity -= normal * inward
        if -inward > (1.2 if self.mode == "surface" else 2.0):
            scale = 12.0 if self.mode == "surface" else 90.0
            self.collision_feedback = max(self.collision_feedback, min(1, -inward / scale))

    def _contacts(self, result):
        for normal in result.normals:
            # A validated step raises the feet positionally. Its rounded
            # edge must not convert horizontal walking speed into a jump.
            step_support = getattr(result, "stepped", False) and normal.z > 0
            self._clip_contact(Vec3(0, 0, 1) if step_support else normal)
        if result.ceiling:
            self.velocity.z = min(0, self.velocity.z)
            self.ceiling = True
        self.collision_ids = tuple(dict.fromkeys((*self.collision_ids, *result.hit_ids)))

    def _surface_vertical(self, dt, vitals, upgrades, gravity, was_grounded):
        energy = _number(vitals.get("energy", 100), 100, 0, 100)
        level = _number(upgrades.get("jetpack", 0), 0, 0, 5)
        lift_time = min(dt, max(0.0, self._space_hold - 0.20)) if self._key("space") else 0.0
        air_brake = self._key("control") and not self.grounded
        requested = dt if air_brake else lift_time
        powered = 0.0
        movement = 0.0
        if requested > 0:
            drain = (8.0 if air_brake else 15.0) / (1.0 + 0.18 * level)
            powered = min(requested, energy / drain)
            waiting = dt - requested
            self.velocity.z, movement = self._fall(self.velocity.z, gravity, waiting)
            if powered > 0:
                climb = 0.0 if air_brake else 10.0 + 1.5 * level
                response = 5.5 if air_brake else 3.4 + 0.2 * level
                self.velocity.z, lift = _accelerate(self.velocity.z, climb, response, powered)
                movement += lift
                energy = max(0.0, energy - drain * powered)
                self.jetpacking = True
                self.braking = self.braking or air_brake
                self.grounded = False
            self.velocity.z, falling = self._fall(self.velocity.z, gravity, requested - powered)
            movement += falling
        else:
            self.velocity.z, movement = self._fall(self.velocity.z, gravity, dt)
            energy = min(100, energy + (14.0 if was_grounded else 3.5) * dt)
        vitals["energy"] = energy
        return movement, powered > 0

    def _walk(self, dt, height_fn, vitals, upgrades, gravity):
        floor = self._height(height_fn, self.position.x, self.position.y) + EYE_HEIGHT
        terrain_grounded = self.position.z <= floor + 0.035 and self.velocity.z <= 0
        if terrain_grounded:
            self.position.z = floor
            self.velocity.z = 0
            self.grounded = True
        else:
            self.grounded = self._solid_grounded and self.velocity.z <= 0.05
        was_grounded, solid_support = self.grounded, self._solid_grounded
        self._coyote_time = COYOTE_TIME if was_grounded else max(0, self._coyote_time - dt)
        jumped = self._jump_buffer > 0 and self._coyote_time > 0
        if jumped:
            self.velocity.z = 7.4
            self.grounded = False
            self._jump_buffer = self._coyote_time = 0.0
            self._jump_active, self._jump_cut = True, False
        self._jump_buffer = max(0, self._jump_buffer - dt)
        if self._jump_active and not self._key("space") and not self._jump_cut:
            # A quick tap makes a controlled hop; holding Space transitions
            # seamlessly from the ordinary jump into powered lift.
            self.velocity.z = min(self.velocity.z, 4.5)
            self._jump_cut = True

        h = math.radians(self.heading)
        wish = Vec3(-math.sin(h), math.cos(h), 0) * self._axis("w", "s")
        wish += Vec3(math.cos(h), math.sin(h), 0) * self._axis("d", "a")
        if wish.lengthSquared() > 1:
            wish.normalize()
        sprint = 1.0 if self._key("shift") and wish.lengthSquared() > 0 else 0.0
        self._sprint_amount, _ = _accelerate(self._sprint_amount, sprint, 12.0, dt)
        target = wish * (9.0 + 7.0 * self._sprint_amount)
        horizontal = Vec3(self.velocity.x, self.velocity.y, 0)
        stopping = wish.lengthSquared() < 1e-6
        reversing = horizontal.dot(wish) < -0.1
        response = (24.0 if stopping or reversing else 18.0) if was_grounded else 5.0
        if self._key("control") and not was_grounded:
            response = 10.0 if stopping else 8.0
        elif self._key("space") and self._space_hold >= 0.2:
            response = 8.0
        horizontal, movement = _accelerate(horizontal, target, response, dt)
        self.velocity.x, self.velocity.y = horizontal.x, horizontal.y
        vertical, jet = self._surface_vertical(dt, vitals, upgrades, gravity, was_grounded)
        incoming_vertical = float(self.velocity.z)
        start = Vec3(self.position)
        next_z = start.z + vertical
        next_x, next_y = start.x + movement.x, start.y + movement.y
        next_floor = self._height(height_fn, next_x, next_y) + EYE_HEIGHT
        # Reject an abrupt wall higher than both the current ground and feet;
        # permit smooth uphill travel and landings approached from above.
        max_floor = max(floor + STEP_HEIGHT, next_z + 0.20)
        if next_floor > max_floor:
            x_floor = self._height(height_fn, next_x, self.position.y) + EYE_HEIGHT
            y_floor = self._height(height_fn, self.position.x, next_y) + EYE_HEIGHT
            if x_floor <= max_floor and abs(movement.x) > 1e-7:
                next_y, next_floor = self.position.y, x_floor
                self.velocity.y = 0
            elif y_floor <= max_floor and abs(movement.y) > 1e-7:
                next_x, next_floor = self.position.x, y_floor
                self.velocity.x = 0
            else:
                next_x, next_y, next_floor = self.position.x, self.position.y, floor
                self.velocity.x = self.velocity.y = 0
        # A roof has its own support. Only terrain contact can authorize
        # following the terrain down; otherwise gravity finds the next floor.
        follow_ground = (terrain_grounded and not jumped and not jet
                         and next_floor >= floor - STEP_HEIGHT)
        if next_z <= next_floor or follow_ground:
            next_z = next_floor
            self.velocity.z = 0
        target_position = Vec3(next_x, next_y, next_z)
        self._solid_grounded = False
        world = self._collision_world
        if world is not None:
            step_height = STEP_HEIGHT if was_grounded and not jumped and not jet else 0.0
            result = world.move_capsule(start, target_position - start,
                                        radius=PLAYER_RADIUS, height=EYE_HEIGHT,
                                        step_height=step_height)
            self._contacts(result)
            target_position = Vec3(result.position)
            self._solid_grounded = bool(result.grounded and self.velocity.z <= 0.05)
            if (solid_support and not self._solid_grounded and not jumped and not jet
                    and self.velocity.z <= 0):
                probe = world.move_capsule(target_position, Vec3(0, 0, -0.12),
                                           radius=PLAYER_RADIUS, height=EYE_HEIGHT, step_height=0)
                if probe.grounded:
                    target_position = Vec3(probe.position)
                    self._contacts(probe)
                    self._solid_grounded = True
            actual_floor = self._height(height_fn, target_position.x, target_position.y) + EYE_HEIGHT
            if target_position.z < actual_floor - 0.001:
                # Sliding can change X/Y after terrain resolution. Resolve the
                # resulting height correction against ceilings too.
                correction = world.move_capsule(target_position, Vec3(0, 0, actual_floor - target_position.z),
                                                radius=PLAYER_RADIUS, height=EYE_HEIGHT, step_height=0)
                self._contacts(correction)
                if correction.position.z >= actual_floor - 0.001:
                    target_position = Vec3(correction.position)
                else:
                    # A narrowing gap between terrain and a roof is impassable.
                    # Keep the last safe horizontal position instead of being
                    # pushed up through the roof or left inside the terrain.
                    fallback_z = max(floor, min(next_z, start.z))
                    result = world.move_capsule(start, Vec3(0, 0, fallback_z - start.z),
                                                radius=PLAYER_RADIUS, height=EYE_HEIGHT, step_height=0)
                    self._contacts(result)
                    target_position = Vec3(result.position)
                    self._solid_grounded = bool(result.grounded)
                    self.velocity.x = self.velocity.y = 0
        self.position = target_position
        final_floor = self._height(height_fn, self.position.x, self.position.y) + EYE_HEIGHT
        terrain_contact = self.position.z <= final_floor + 0.035 and self.velocity.z <= 0
        self.grounded = self._solid_grounded or terrain_contact
        if self.grounded:
            self.velocity.z = max(0.0, self.velocity.z)
            self._jump_active = False
            if not was_grounded and incoming_vertical < -2.5:
                self._landing_offset = -min(0.18, -incoming_vertical * 0.012)
            if was_grounded and self.position.z - start.z > 0.045:
                self._camera_step = max(-0.45, self._camera_step + start.z - self.position.z)
        self.throttle, _ = _accelerate(self.throttle, min(1.0, wish.length()), 12.0, dt)

    def _fly(self, dt, height_fn, vitals, upgrades):
        self.grounded = False
        self._solid_grounded = False
        h = math.radians(self.heading)
        thrust = self._axis("w", "s")
        wish = self.forward() * (thrust if thrust >= 0 else thrust * 0.45)
        wish += Vec3(math.cos(h), math.sin(h), 0) * self._axis("d", "a") * 0.72
        wish += Vec3(0, 0, 1) * self._axis("space", "control") * 0.70
        if wish.lengthSquared() > 1:
            wish.normalize()
        amount = min(1.0, wish.length())
        fuel = _number(vitals.get("fuel", 100), 100, 0, 100)
        boost = self._key("shift") and amount > 0 and fuel > 0
        orbital = self.mode == "orbit"
        engine = _number(upgrades.get("engine", 0), 0, 0, 5)
        cruise = (110.0 if orbital else 35.0) * (1.0 + 0.08 * engine)
        maximum = ((800.0 if orbital else 100.0) * (1.0 + 0.06 * engine)
                   if boost else cruise)
        response = 1.9 if orbital else 3.2
        drain = ((0.22 if orbital else 0.16) if boost else 0.025)
        drain *= amount / (1.0 + engine * 0.10)
        powered = min(dt, fuel / drain) if amount > 0 and fuel > 0 else 0.0
        movement = Vec3(0)
        if powered > 0:
            target = wish * maximum
            if thrust < 0:
                # Reverse thrust is an explicit full-velocity brake even with
                # assist off. It crosses zero smoothly into a slower reverse.
                response = 5.0
                self.braking = self.braking or self.velocity.dot(self.forward()) > 0.5
            elif not self.flight_assist:
                # Only the requested thrust axis is driven. Turning the view
                # or releasing the controls preserves inertial travel.
                direction = wish / amount
                target += self.velocity - direction * self.velocity.dot(direction)
                target = _limited(target, max(maximum, self.velocity.length()))
            self.velocity, movement = _accelerate(self.velocity, target, response, powered)
            fuel = max(0, fuel - drain * powered)
        unpowered = dt - powered
        if unpowered > 0:
            if self.flight_assist:
                self.braking = self.braking or self.velocity.lengthSquared() > 4.0
                self.velocity, coast = _accelerate(self.velocity, Vec3(0),
                                                   1.5 if orbital else 3.0, unpowered)
            else:
                coast = self.velocity * unpowered
            movement += coast
        vitals["fuel"] = fuel
        self.boosting = self.boosting or (boost and powered > 0)
        self.throttle, _ = _accelerate(self.throttle, amount * powered / dt, 10.0, dt)
        start = Vec3(self.position)
        target_position = (start + movement if orbital else
                           self._sweep_ship_terrain(start, movement, height_fn))
        world = self._collision_world
        if world is not None:
            result = world.move_sphere(start, target_position - start, radius=SHIP_RADIUS)
            target_position = Vec3(result.position)
            self._contacts(result)
            self.grounded = self.grounded or result.grounded
            if not orbital:
                # A wall can redirect the sweep onto higher terrain. Resolve
                # that floor adjustment against structures as well.
                floor = self._height(height_fn, target_position.x, target_position.y) + SHIP_CLEARANCE
                if target_position.z < floor - 0.001:
                    correction = world.move_sphere(target_position,
                                                   Vec3(0, 0, floor - target_position.z),
                                                   radius=SHIP_RADIUS)
                    self._contacts(correction)
                    if correction.position.z >= floor - 0.001:
                        target_position = Vec3(correction.position)
                    else:
                        safe_floor = self._height(height_fn, start.x, start.y) + SHIP_CLEARANCE
                        recovery = world.move_sphere(start, Vec3(0, 0, max(0, safe_floor - start.z)),
                                                     radius=SHIP_RADIUS)
                        target_position = Vec3(recovery.position)
                        self._contacts(recovery)
                        self.velocity = Vec3(0)
                    self.grounded = True
        self.position = target_position

    def _sweep_ship_terrain(self, start, movement, height_fn):
        """Sweep the clearance envelope over a sampled height field.

        Samples are at most 0.5 m apart, including unusually fast recovered
        velocities. A binary search finds the first contact and the remaining
        motion slides along its slope; a ridge cannot be skipped by checking
        only a frame's endpoint. The streamed solid world handles ship radius.
        """
        position, remaining = Vec3(start), Vec3(movement)
        floor = self._height(height_fn, position.x, position.y) + SHIP_CLEARANCE
        if position.z < floor:
            position.z = floor
            self._clip_contact(Vec3(0, 0, 1))
            self.grounded = True
        for _ in range(4):
            samples = max(1, math.ceil(math.hypot(remaining.x, remaining.y) / 0.5))
            lower, hit = 0.0, None
            for sample in range(1, samples + 1):
                fraction = sample / samples
                point = position + remaining * fraction
                floor = self._height(height_fn, point.x, point.y) + SHIP_CLEARANCE
                if point.z < floor - 0.0001:
                    hit = fraction
                    break
                lower = fraction
            if hit is None:
                position += remaining
                break
            upper = hit
            for _ in range(12):
                middle = (lower + upper) * 0.5
                point = position + remaining * middle
                floor = self._height(height_fn, point.x, point.y) + SHIP_CLEARANCE
                if point.z < floor:
                    upper = middle
                else:
                    lower = middle
            position += remaining * lower
            # Central differences use a one-metre baseline. The procedural
            # terrain is smooth; abrupt synthetic cliffs become steep walls.
            dx = (self._height(height_fn, position.x + 0.5, position.y)
                  - self._height(height_fn, position.x - 0.5, position.y))
            dy = (self._height(height_fn, position.x, position.y + 0.5)
                  - self._height(height_fn, position.x, position.y - 0.5))
            normal = Vec3(-dx, -dy, 1)
            normal.normalize()
            remaining *= 1.0 - lower
            into = remaining.dot(normal)
            if into >= -1e-8:
                # A very narrow discontinuity may fall between the derivative
                # probes. Stop at the known safe edge rather than pass through.
                normal = -Vec3(remaining.x, remaining.y, 0)
                if normal.lengthSquared() < 1e-8:
                    normal = Vec3(0, 0, 1)
                else:
                    normal.normalize()
                into = min(0.0, remaining.dot(normal))
            remaining -= normal * into
            self._clip_contact(normal)
            self.collision_ids = tuple(dict.fromkeys((*self.collision_ids, "terrain")))
            self.grounded = self.grounded or normal.z >= 0.6
            position += normal * 0.001
            if remaining.lengthSquared() < 1e-10:
                break
        floor = self._height(height_fn, position.x, position.y) + SHIP_CLEARANCE
        if position.z <= floor + 0.01 and self.velocity.z <= 0:
            self.grounded = True
            self._clip_contact(Vec3(0, 0, 1))
        return position

    def _update_camera_motion(self, dt, previous, turn_rate):
        """Presentation only: never feed eye animation back into physics."""
        motion = self._camera_motion
        if motion <= 0:
            self._roll = self._bob_amount = 0.0
            self.camera_offset = Vec3(0)
            self.camera_roll = self.fov_offset = 0.0
            self.fov = self._base_fov
            return
        speed = self.velocity.length()
        if self.mode == "surface":
            travelled = self.position - previous
            planar_speed = math.hypot(travelled.x, travelled.y) / dt
            walking = min(1.3, planar_speed / 9.0) if self.grounded else 0.0
            self._bob_amount, _ = _accelerate(self._bob_amount, walking, 12.0, dt)
            if self.grounded:
                self._bob_phase = (self._bob_phase + planar_speed * dt * 1.1) % math.tau
            heading = math.radians(self.heading)
            side = math.sin(self._bob_phase) * 0.035 * self._bob_amount
            height = math.cos(self._bob_phase * 2) * 0.035 * self._bob_amount
            height += self._camera_step + self._landing_offset
            self.camera_offset = Vec3(math.cos(heading) * side, math.sin(heading) * side,
                                      height) * motion
            roll_target = _number(-self._axis("d", "a") * 1.5 - turn_rate * 0.006,
                                  0, -2.5, 2.5)
            fov_target = 3.0 * self._sprint_amount
        else:
            self.camera_offset = Vec3(0)
            roll_target = _number(-self._axis("d", "a") * 6.0 - turn_rate * 0.05,
                                  0, -14, 14)
            cruise, maximum = (110.0, 800.0) if self.mode == "orbit" else (35.0, 100.0)
            fov_target = min(2.0, speed / cruise * 2.0)
            fov_target += _number((speed - cruise) / (maximum - cruise), 0, 0, 1) * 6.0
        self._roll, _ = _accelerate(self._roll, roll_target, 8.0, dt)
        self.camera_roll = self._roll * motion
        self.fov_offset, _ = _accelerate(self.fov_offset, fov_target * motion, 5.0, dt)
        self.fov = self._base_fov + self.fov_offset

    def _apply_camera(self):
        camera = getattr(self.app, "camera", None)
        if camera is not None:
            camera.setPos(self.position + self.camera_offset)
            camera.setHpr(self.heading, self.pitch, self.camera_roll)
        lens = getattr(self.app, "camLens", None)
        if lens is not None and callable(getattr(lens, "setFov", None)):
            lens.setFov(self.fov)

    def destroy(self):
        if self._destroyed:
            return
        self.set_enabled(False)
        self.ignoreAll()
        self._destroyed = True
