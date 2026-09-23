"""Celestial's bounded desktop lighting, shadows and photographic finish.

The native UI renders after the scene filter, keeping text sharp. TinyDisplay
retains the richer CPU geometry and palettes without constructing GPU buffers.
No gameplay, camera movement, terrain vertices or saved state live here.
"""
from __future__ import annotations

from pathlib import Path
import time
import warnings

from panda3d.core import (
    AntialiasAttrib, BitMask32, DepthTestAttrib, DepthWriteAttrib,
    FrameBufferProperties, GraphicsOutput, Mat4, NodePath, OrthographicLens,
    Shader, Texture, TransparencyAttrib, Vec3,
)

_SHADERS = Path(__file__).with_name("shaders")
_SUN = Vec3(-.68, -.62, .39).normalized()
SHADOW_MASK = BitMask32.bit(1)
SHADOW_SIZE = 1024


def shader(vertex, fragment):
    vert = (_SHADERS / vertex).read_text(encoding="utf-8")
    frag = (_SHADERS / fragment).read_text(encoding="utf-8")
    frag = frag.replace("// SHADOW_INCLUDE", (_SHADERS / "shadow.glsl").read_text(encoding="utf-8"))
    return Shader.make(Shader.SL_GLSL, vert, frag)


class VisualPipeline:
    """Resources owned by one ShowBase, reusable across system changes."""

    def __init__(self, app):
        self.app = app
        self.manager = self.post = self.bloom_quad = None
        self.shadow_buffer = self.shadow_camera = None
        self.scene_shader = None
        self._destroyed = False
        self._size = None
        self._shadow_snapshot = None
        gsg = app.win.getGsg() if app.win else None
        # Panda's basic-shader flag refers to Cg on macOS; GLSL is independent.
        self.gpu = bool(gsg and (gsg.getDriverShaderVersionMajor(),
                                gsg.getDriverShaderVersionMinor()) >= (1, 50))
        self.renderer = gsg.getDriverRenderer() if gsg else "unavailable"
        app.render.setAntialias(AntialiasAttrib.MAuto)
        if not self.gpu:
            return
        self.scene_shader = shader("scene.vert", "scene.frag")
        app.render.setShader(self.scene_shader)
        app.cam.node().setCameraMask(BitMask32.bit(0))
        app.render.setShaderInput("ae_material", .72, 0., 0., 0.)
        app.render.setShaderInput("ae_body", 0., 0., 0., 0.)
        app.render.setShaderInput("ae_sun_direction", _SUN)
        app.render.setShaderInput("ae_camera", Vec3(0))
        app.render.setShaderInput("ae_up", Vec3(0, 0, 1))
        app.render.setShaderInput("ae_time", 0.)
        app.render.setShaderInput("ae_wind", Vec3(.8, .4, 0))
        app.render.setShaderInput("ae_fog_color", Vec3(.32, .45, .52))
        app.render.setShaderInput("ae_fog_density", 0.)
        app.render.setShaderInput("ae_shadow_enabled", 0.)
        app.render.setShaderInput("ae_world_to_shadow", Mat4.identMat())
        app.render.setShaderInput("ae_shadow_texel", 1. / SHADOW_SIZE)
        self._make_shadows()
        self._make_post()

    def _make_shadows(self):
        app = self.app
        depth = Texture("celestial-sun-depth")
        depth.setFormat(Texture.FDepthComponent)
        depth.setMinfilter(Texture.FTNearest)
        depth.setMagfilter(Texture.FTNearest)
        depth.setWrapU(Texture.WMClamp)
        depth.setWrapV(Texture.WMClamp)
        app.render.setShaderInput("ae_shadow_map", depth)
        fbp = FrameBufferProperties()
        fbp.setRgbColor(True)
        fbp.setDepthBits(24)
        fbp.setMultisamples(0)
        buffer = app.win.makeTextureBuffer("celestial-sun-shadow", SHADOW_SIZE, SHADOW_SIZE,
                                          None, False, fbp)
        if buffer is None:
            warnings.warn("Sun shadow buffer unavailable; using ambient lighting.")
            return
        buffer.setSort(-1200)
        buffer.addRenderTexture(depth, GraphicsOutput.RTMBindOrCopy, GraphicsOutput.RTPDepth)
        buffer.setClearDepthActive(True)
        buffer.setClearDepth(1.)
        lens = OrthographicLens()
        lens.setFilmSize(210, 210)
        lens.setNearFar(1, 440)
        cam = app.makeCamera(buffer, lens=lens, scene=app.render)
        cam.reparentTo(app.render)
        cam.node().setCameraMask(SHADOW_MASK)
        depth_shader = Shader.make(Shader.SL_GLSL,
            "#version 150\nuniform mat4 p3d_ModelViewProjectionMatrix; in vec4 p3d_Vertex;"
            "void main(){gl_Position=p3d_ModelViewProjectionMatrix*p3d_Vertex;}",
            "#version 150\nout vec4 fragColor; void main(){fragColor=vec4(1);}")
        state = NodePath("shadow-render-state")
        state.setShader(depth_shader, 100)
        state.setAttrib(DepthWriteAttrib.make(DepthWriteAttrib.MOn), 100)
        state.setAttrib(DepthTestAttrib.make(DepthTestAttrib.MLessEqual), 100)
        state.setTransparency(TransparencyAttrib.MNone, 100)
        state.setColor(1, 1, 1, 1, 100)
        cam.node().setInitialState(state.getState())
        self.shadow_buffer, self.shadow_camera = buffer, cam

    def _make_post(self):
        from direct.filter.FilterManager import FilterManager
        self.manager = FilterManager(self.app.win, self.app.cam)
        color = Texture("celestial-scene-color")
        color.setMinfilter(Texture.FTLinear)
        color.setMagfilter(Texture.FTLinear)
        fbp = FrameBufferProperties()
        fbp.setRgbColor(True)
        fbp.setRgbaBits(16, 16, 16, 16)
        fbp.setFloatColor(True)
        fbp.setDepthBits(24)
        fbp.setMultisamples(0)
        self.post = self.manager.renderSceneInto(colortex=color, fbprops=fbp)
        if self.post is None:
            self.manager.cleanup()
            self.manager = None
            warnings.warn("Scene filter unavailable; rendering directly.")
            return
        bloom = Texture("celestial-selective-bloom")
        bloom.setMinfilter(Texture.FTLinear)
        bloom.setMagfilter(Texture.FTLinear)
        self.bloom_quad = self.manager.renderQuadInto("celestial-bloom", colortex=bloom, div=4)
        if self.bloom_quad is None:
            self.manager.cleanup()
            self.manager = self.post = None
            return
        self.bloom_quad.setShader(shader("post.vert", "bloom.frag"))
        self.bloom_quad.setShaderInput("ae_scene", color)
        self.post.setShader(shader("post.vert", "post.frag"))
        self.post.setShaderInput("ae_scene", color)
        self.post.setShaderInput("ae_bloom", bloom)
        self._resize()

    def _resize(self):
        size = (max(1, self.app.win.getXSize()), max(1, self.app.win.getYSize()))
        if self.post and size != self._size:
            self._size = size
            pixel = (1. / size[0], 1. / size[1])
            self.post.setShaderInput("ae_pixel", *pixel)
            self.bloom_quad.setShaderInput("ae_pixel", *pixel)

    def bind_world(self, world):
        if not self.gpu:
            return
        world.root.setShader(self.scene_shader)
        world.sky_root.hide(SHADOW_MASK)
        for weather in world._weather.values():
            weather.clouds.hide(SHADOW_MASK)
            weather.limb.hide(SHADOW_MASK)

    def update(self):
        if not self.gpu or self._destroyed:
            return
        app = self.app
        self._resize()
        world = getattr(app, "world", None)
        if world is None or world.root is None:
            return
        camera = app.camera.getPos(app.render)
        rotation = world.root.getQuat(app.render)
        sunlight = rotation.xform(_SUN)
        up = Vec3(0, 0, 1)
        if world.frame is None and world.fields:
            field = min(world.fields.values(), key=lambda f: f.altitude(camera))
            up = (camera-field.center).normalized()
        app.render.setShaderInput("ae_camera", camera)
        app.render.setShaderInput("ae_sun_direction", sunlight)
        app.render.setShaderInput("ae_up", up)
        app.render.setShaderInput("ae_time", float(getattr(app.game, "elapsed", 0.)))
        # Surface assets inherit their body's physical horizon. Space stations
        # retain the default unobstructed sunlight; reframing moves each centre.
        for identifier, body in world.body_roots.items():
            center = body.getPos(app.render)
            body.setShaderInput("ae_body", center.x, center.y, center.z,
                                world.fields[identifier].radius)
        ship = getattr(app, "ship", None)
        planet = getattr(app, "planet", None)
        if ship is not None and not ship.isEmpty() and planet:
            body = world.body_roots.get(planet["id"])
            if body is not None:
                center = body.getPos(app.render)
                ship.setShaderInput("ae_body", center.x, center.y, center.z,
                                    world.fields[planet["id"]].radius)
        if hasattr(world, "_fog"):
            world.root.setShaderInput("ae_fog_color", Vec3(world._fog.getColor().xyz))
            world.root.setShaderInput("ae_fog_density", world._fog.getExpDensity())
        if self.shadow_camera is not None:
            altitude = getattr(world, "environment", {}).get("altitude", 0.)
            quality = app.game.settings.get("quality", "medium")
            large_frame = app.win.getXSize() * app.win.getYSize() >= int(2560 * 1440 * .95)
            active = (quality != "low" and not (quality == "medium" and large_frame) and
                      altitude < 650 and sunlight.dot(up) > .035)
            strength=max(0.,min(1.,(650.-altitude)/160.))
            strength*=max(0.,min(1.,(sunlight.dot(up)-.035)/.12))
            app.render.setShaderInput("ae_shadow_enabled", strength if active else 0.)
            if active:
                forward = app.camera.getQuat(app.render).getForward()
                forward -= up*forward.dot(up)
                if forward.lengthSquared() > .01:
                    forward.normalize()
                now = time.monotonic()
                revision = getattr(world, "_shadow_revision", 0)
                last = self._shadow_snapshot
                refresh = (quality in ("high", "ultra") or last is None or
                           last[0] != revision or now - last[4] >= .12 or
                           (camera-last[1]).lengthSquared() > 1.5**2 or
                           forward.dot(last[2]) < .999 or sunlight.dot(last[3]) < .9999)
                self.shadow_buffer.setActive(refresh)
                if refresh:
                    center = camera+forward*36-up*4
                    self.shadow_camera.setPos(center+sunlight*190)
                    self.shadow_camera.lookAt(center, up)
                    matrix = (app.render.getMat(self.shadow_camera) *
                              self.shadow_camera.node().getLens().getProjectionMat())
                    app.render.setShaderInput("ae_world_to_shadow", matrix)
                    self._shadow_snapshot = (revision, Vec3(camera), Vec3(forward),
                                             Vec3(sunlight), now)
            else:
                self.shadow_buffer.setActive(False)
                self._shadow_snapshot = None

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self.manager:
            self.manager.cleanup()
            self.manager.ignoreAll()
        if self.shadow_camera is not None:
            self.shadow_camera.removeNode()
        if self.shadow_buffer is not None:
            self.app.graphicsEngine.removeWindow(self.shadow_buffer)
