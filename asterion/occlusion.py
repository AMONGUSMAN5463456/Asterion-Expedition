"""Depth-based surface contact shading without changing scene materials."""
from direct.filter.FilterManager import FilterManager
from panda3d.core import Mat4, Shader, Texture


_VERTEX = """#version 130
uniform mat4 p3d_ModelViewProjectionMatrix;
in vec4 p3d_Vertex;
in vec2 p3d_MultiTexCoord0;
out vec2 uv;
void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    uv = p3d_MultiTexCoord0;
}
"""
_POSITION = """
uniform sampler2D depth;
uniform vec2 viewport_size;
uniform mat4 inverse_projection;
vec3 position(vec2 coord) {
    vec2 pixel = 1.0 / viewport_size;
    coord = clamp(coord, pixel * 0.5, 1.0 - pixel * 0.5);
    float z = texture(depth, coord * viewport_size / vec2(textureSize(depth, 0))).r;
    vec4 p = inverse_projection * vec4(coord * 2.0 - 1.0, z * 2.0 - 1.0, 1.0);
    return p.xyz / p.w;
}
"""
_OCCLUSION = """#version 130
in vec2 uv;
out vec4 result;
""" + _POSITION + """
void main() {
    vec2 pixel = 1.0 / viewport_size;
    vec3 p = position(uv);
    // Choose the nearer neighbour on each axis to avoid silhouette normals.
    vec3 left = p - position(uv - vec2(pixel.x, 0));
    vec3 right = position(uv + vec2(pixel.x, 0)) - p;
    vec3 down = p - position(uv - vec2(0, pixel.y));
    vec3 up = position(uv + vec2(0, pixel.y)) - p;
    vec3 dx = dot(left, left) < dot(right, right) ? left : right;
    vec3 dy = dot(down, down) < dot(up, up) ? down : up;
    vec3 normal = normalize(cross(dx, dy));
    if (dot(normal, -p) < 0.0) normal = -normal;
    if (texture(depth, uv * viewport_size / vec2(textureSize(depth, 0))).r >= 0.99999 || length(p) > 120.0) {
        result = vec4(1.0);
        return;
    }
    // A 1.5 metre neighbourhood, bounded in screen space near the camera.
    vec2 radius = min(vec2(48.0) * pixel,
                      1.5 * pixel / max(vec2(length(dx), length(dy)), vec2(0.0001)));
    float blocked = 0.0;
    for (int i = 0; i < 16; ++i) {
        float angle = float(i) * 2.39996323;
        vec2 coord = uv + vec2(cos(angle), sin(angle)) * radius * sqrt((float(i) + 0.5) / 16.0);
        if (any(lessThan(coord, vec2(0))) || any(greaterThan(coord, vec2(1)))) continue;
        vec3 delta = position(coord) - p;
        float distance = length(delta);
        float facing = max(dot(normal, delta) / max(distance, 0.0001) - 0.12, 0.0);
        blocked += facing * (1.0 - smoothstep(0.1, 1.5, distance));
    }
    float fade = 1.0 - smoothstep(80.0, 120.0, length(p));
    result = vec4(vec3(clamp(1.0 - blocked * (2.0 / 16.0) * fade, 0.45, 1.0)), 1.0);
}
"""
_COMPOSITE = """#version 130
in vec2 uv;
out vec4 result;
uniform sampler2D color;
uniform sampler2D occlusion;
uniform vec2 occlusion_size;
""" + _POSITION + """
void main() {
    vec2 pixel = 1.0 / occlusion_size;
    float distance = length(position(uv));
    vec2 center = clamp(uv, pixel * 0.5, 1.0 - pixel * 0.5);
    float sum = texture(occlusion, center * occlusion_size / vec2(textureSize(occlusion, 0))).r;
    float weights = 1.0;
    // Depth-aware upsampling keeps foreground shading off the sky.
    for (int y = -1; y <= 1; ++y) {
        for (int x = -1; x <= 1; ++x) {
            if (x == 0 && y == 0) continue;
            vec2 coord = clamp(uv + vec2(x, y) * pixel, pixel * 0.5, 1.0 - pixel * 0.5);
            float weight = exp(-abs(length(position(coord)) - distance) * 8.0)
                         / (1.0 + float(x*x + y*y));
            sum += texture(occlusion, coord * occlusion_size / vec2(textureSize(occlusion, 0))).r * weight;
            weights += weight;
        }
    }
    vec2 color_uv = clamp(uv, 0.5 / viewport_size, 1.0 - 0.5 / viewport_size);
    vec4 scene = texture(color, color_uv * viewport_size / vec2(textureSize(color, 0)));
    result = vec4(scene.rgb * (sum / max(weights, 0.0001)), scene.a);
}
"""


class AmbientOcclusion:
    """Own the scene/depth target and half-resolution AO pass; HUD stays separate."""

    def __init__(self, win, camera):
        self.manager = FilterManager(win, camera)
        self.lens = camera.node().getLens()
        self.projection = None
        self.quads = []
        self.viewport_size = None
        try:
            color, depth, ao = (Texture(name) for name in ("ao-color", "ao-depth", "ao-shading"))
            for texture in (color, depth, ao):
                texture.setWrapU(Texture.WMClamp)
                texture.setWrapV(Texture.WMClamp)
            depth.setMinfilter(Texture.FTNearest)
            depth.setMagfilter(Texture.FTNearest)
            final = self.manager.renderSceneInto(colortex=color, depthtex=depth)
            shading = self.manager.renderQuadInto("ambient-occlusion", colortex=ao, div=2)
            if final is None or shading is None:
                raise RuntimeError("Ambient occlusion render targets are unavailable")
            self.quads = [shading, final]
            for quad, fragment in zip(self.quads, (_OCCLUSION, _COMPOSITE)):
                shader = Shader.make(Shader.SL_GLSL, _VERTEX, fragment)
                if shader is None or shader.prepareNow(win.getGsg().getPreparedObjects(), win.getGsg()) is None:
                    raise RuntimeError("Ambient occlusion shaders are unavailable")
                quad.setShader(shader)
                quad.setShaderInput("depth", depth)
            final.setShaderInput("color", color)
            final.setShaderInput("occlusion", ao)
            self.update()
        except Exception:
            self.cleanup()
            raise

    def update(self):
        scene, shading = self.manager.buffers
        size = (scene.getXSize(), scene.getYSize())
        if self.viewport_size != size:
            self.viewport_size = size
            for quad in self.quads:
                quad.setShaderInput("viewport_size", *size)
            self.quads[1].setShaderInput("occlusion_size", shading.getXSize(), shading.getYSize())
        projection = self.lens.getProjectionMat()
        if self.projection != projection:
            self.projection = Mat4(projection)
            inverse = Mat4()
            inverse.invertFrom(projection)
            for quad in self.quads:
                quad.setShaderInput("inverse_projection", inverse)

    def cleanup(self):
        self.manager.cleanup()
        self.manager.ignoreAll()
        self.quads.clear()
