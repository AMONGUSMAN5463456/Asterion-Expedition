"""Physical-scale landscape materials for the continuous planet surface.

Only colour and normals are shaded: vertices remain exactly on PlanetField's
collision envelope. The desktop material uses body-space triplanar mapping so
detail stays attached through a floating-origin change and across cube seams.
The same original soil texture and palette have a baked software fallback.
"""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path

from panda3d.core import Shader, TransparencyAttrib

from .planet_visuals import terrain_detail_texture, terrain_relief_texture
from .orbital_materials import GLSL as ORBITAL_GLSL, configure_orbital_material


_BIOMES = ("verdant", "desert", "frozen", "volcanic", "toxic", "oceanic",
           "crystalline", "fungal")


def _mix(a, b, value):
    value = max(0., min(1., value))
    return tuple(a[i] * (1 - value) + b[i] * value for i in range(3))


def terrain_albedo(field, direction):
    """Give geology metre-scale colour variation without altering geography.

    Alpha stores signed seabed depth for the opaque GPU ocean material. It is
    explicitly never used for transparency, including on the software path.
    """
    color = field.color(direction)
    depth = field.seabed_elevation(direction) - field.water_level
    payload = .5 + max(-32., min(32., depth)) / 64.
    if depth < -.5:
        # A clear turquoise shelf grades into deep, saturated ocean basins.
        shallows = math.exp(min(0., depth) / 13.)
        color = _mix(tuple(c * .44 for c in field.water),
                     _mix(field.water, (.21, .70, .65), .24), shallows)
        return (*color, payload)
    x, y, z = (float(c) * field.radius for c in direction)
    phase = field._phase
    broad = field._noise(x * .012 + phase[0], y * .012 + phase[1], z * .012 + phase[2])
    fleck = field._noise(x * .091 + phase[3], y * .091 + phase[4], z * .091 + phase[5])
    biome = field.biome
    if biome in ("verdant", "oceanic"):
        # Moss beds alternate with exposed warm soil without competing with
        # leaf colours. Oceanic shores keep their drier, sandy palette.
        moss = _mix(color, (.255,.395,.245) if biome == "verdant" else (.29,.36,.235), .62)
        ochre = (.395,.37,.245) if biome == "verdant" else (.48,.43,.30)
        exposure = max(0.,min(1.,(broad+fleck*.22+.18)/.58))
        exposure = exposure*exposure*(3-2*exposure)
        color = _mix(moss,ochre,.08+exposure*.56 if biome == "verdant" else .16+exposure*.70)
    elif biome == "desert":
        color = _mix(color, (.75, .51, .30), max(0., broad + .25) * .37)
    elif biome == "frozen":
        color = _mix(color, (.80, .89, .91), max(0., broad + .35) * .50)
    elif biome == "fungal":
        color = _mix(color, (.31, .38, .44), max(0., broad + .22) * .40)
    elif biome == "toxic":
        color = _mix(color, (.45, .43, .25), max(0., broad + .18) * .43)
    elif biome == "crystalline":
        color = _mix(color, (.46, .43, .53), max(0., broad + .12) * .48)
    # Keep luminous fissures intact while making ash and soil readable.
    factor = 1.0 + .09 * broad + .045 * fleck
    color = tuple(max(0., min(1., c * factor)) for c in color)
    return (*color, payload)


_VERTEX = """#version 150
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;
uniform vec3 ae_patch_origin;
in vec4 p3d_Vertex;
in vec3 p3d_Normal;
in vec4 p3d_Color;
out vec3 body_position;
out vec3 body_normal;
out vec3 patch_position;
out vec3 render_position;
out vec4 surface_color;
void main() {
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
    body_position = ae_patch_origin + p3d_Vertex.xyz;
    body_normal = p3d_Normal;
    patch_position = p3d_Vertex.xyz;
    render_position = (p3d_ModelMatrix * p3d_Vertex).xyz;
    surface_color = p3d_Color;
}
"""


_FRAGMENT = """#version 150
uniform sampler2D ae_soil;
uniform sampler2D ae_relief;
uniform vec3 ae_detail_origin;
uniform vec3 ae_broad_origin;
uniform vec3 ae_grain_origin;
uniform vec3 ae_observer;
uniform vec3 ae_sky;
uniform vec3 ae_water;
uniform vec3 ae_ground;
uniform vec3 ae_fog_color;
uniform float ae_fog_density;
uniform float ae_time;
uniform float ae_biome;
uniform vec4 p3d_ColorScale;
in vec3 body_position;
in vec3 body_normal;
in vec3 patch_position;
in vec3 render_position;
in vec4 surface_color;
out vec4 fragColor;
// AE_SHADOW
// AE_GEOGRAPHY
const vec3 SUN = vec3(-0.680374,-0.620341,0.390214);

float triplanar(vec3 p, vec3 weights) {
    return texture(ae_soil, p.yz).r * weights.x
         + texture(ae_soil, p.zx).r * weights.y
         + texture(ae_soil, p.xy).r * weights.z;
}

vec3 relief_normal(vec3 p, vec3 n, vec3 weights, float strength) {
    vec2 x = texture(ae_relief,p.yz).rg*2.0-1.0;
    vec2 y = texture(ae_relief,p.zx).rg*2.0-1.0;
    vec2 z = texture(ae_relief,p.xy).rg*2.0-1.0;
    vec3 gradient = vec3(0,x.x,x.y)*weights.x
                  + vec3(y.y,0,y.x)*weights.y
                  + vec3(z.x,z.y,0)*weights.z;
    gradient -= n*dot(gradient,n);
    return normalize(n-gradient*strength);
}

void main() {
    vec3 p = body_position;
    vec3 radial = normalize(p);
    vec3 n = normalize(body_normal);
    vec3 to_eye = ae_observer - p;
    float distance = max(length(to_eye), 0.01);
    vec3 view = to_eye / distance;
    float close_detail = 1.0 - smoothstep(85.0, 900.0, distance);
    vec3 weights = pow(abs(n), vec3(4.0));
    weights /= max(dot(weights, vec3(1.0)), .001);
    // Phase is reduced on the CPU before the tile origin becomes float32.
    // A near tile never differentiates or textures a huge radial coordinate.
    vec3 detail_uv=patch_position*.12+ae_detail_origin;
    vec3 broad_uv=patch_position*.019+ae_broad_origin;
    vec3 grain_uv=patch_position*.47+ae_grain_origin;
    float fine = triplanar(detail_uv, weights);
    float broad = triplanar(broad_uv, weights);
    float grain = triplanar(grain_uv, weights);
    float soil = mix(1.0, .65 + fine * .31 + broad * .11, close_detail);
    vec3 albedo = surface_color.rgb * soil;
    float geographic_depth=(surface_color.a-.5)*64.0;
    float orbital=smoothstep(550.0,2800.0,distance);
    if(orbital>.001) {
        float sampled_depth;
        vec3 sampled_color=orbital_albedo(radial,distance,sampled_depth);
        albedo=mix(albedo,sampled_color,orbital);
        geographic_depth=mix(geographic_depth,sampled_depth,orbital);
    }
    // Pebbles and eroded bedding remain quieter than the planet's biome hue.
    float exposed = smoothstep(.10, .42, 1.0 - dot(n, radial));
    albedo = mix(albedo, mix(ae_ground, vec3(.43,.40,.35), .36)
                         * (.65 + .42 * broad), exposed * .64);
    float geometric_light=max(0.0,dot(n,SUN));
    n = relief_normal(detail_uv,n,weights,.13*close_detail);
    float ndotl = max(0.0, dot(n, SUN));
    float daylight = smoothstep(-.18, .20, dot(radial, SUN));
    float shadow = ae_shadow(render_position, geometric_light);
    vec3 ambient = mix(vec3(.075,.115,.19), vec3(.43,.51,.59), daylight);
    vec3 sunlight = vec3(1.12, 1.02, .84) * pow(ndotl, .82) * shadow;
    vec3 color = albedo * (ambient + sunlight);
    // Pale mica in ice and mineral flats catches narrow, restrained highlights.
    vec3 half_vector = normalize(SUN + view);
    float mineral = (ae_biome == 2.0 || ae_biome == 6.0) ? .18 : .025;
    color += vec3(.91,.94,1.0) * pow(max(0.0,dot(n,half_vector)), 74.0)
             * smoothstep(.77,.94,grain) * mineral * shadow;

    // Seabed depth is interpolated through the same shoreline triangles as
    // the solid ocean datum. No translucent sphere or displaced collision.
    float depth = geographic_depth;
    float ocean = 1.0 - smoothstep(-.45, .55, depth);
    if (ocean > .001) {
        vec3 w0 = vec3(.842,.491,.222), w1 = vec3(-.312,.927,.206);
        vec3 w2 = vec3(.234,-.429,.872);
        vec3 slope = w0 * cos(dot(p,w0) * .38 + ae_time * .83) * .073
                   + w1 * cos(dot(p,w1) * .79 - ae_time * 1.21) * .034
                   + w2 * cos(dot(p,w2) * 1.61 + ae_time * 1.73) * .015;
        slope -= radial * dot(slope, radial);
        vec3 water_normal = normalize(radial + slope * (.16 + .84 * close_detail));
        water_normal = relief_normal(grain_uv,water_normal,weights,
                                     .028*close_detail);
        float facing = max(0.0,dot(water_normal,view));
        float fresnel = .035 + .90 * pow(1.0 - facing, 4.2);
        vec3 reflected = reflect(-view, water_normal);
        float zenith = pow(max(0.0, dot(reflected,radial)), .4);
        vec3 horizon = mix(ae_sky, vec3(.77,.84,.83), .56);
        vec3 reflection = mix(horizon, ae_sky * vec3(.55,.75,.91), zenith);
        reflection *= mix(.14, 1.0, daylight);
        float shallow = exp(min(depth,0.0) / 10.0);
        vec3 basin = ae_water * vec3(.18,.30,.39);
        vec3 shelf = mix(ae_water, vec3(.19,.65,.57), .28);
        vec3 water_color = mix(basin, shelf, shallow)
                        * (.61 + .49 * max(0.0,dot(radial,SUN))) * daylight;
        water_color = mix(water_color, reflection, fresnel);
        float glint = pow(max(0.0, dot(water_normal,half_vector)), 160.0);
        water_color += vec3(1.0,.88,.65) * glint * mix(.36,.95,close_detail) * shadow * daylight;
        float shore = (1.0 - smoothstep(.15, 2.6, -depth))
                    * smoothstep(-.5, .15, -depth);
        float wave = .5 + .5 * sin(depth * 4.7 + ae_time * 1.5
                         + (broad - .5) * 12.0);
        float foam = shore * smoothstep(.49,.78,wave) * (.3 + .6*fine);
        water_color = mix(water_color, vec3(.78,.86,.77) * daylight,
                          foam * .63);
        color = mix(color, water_color, ocean);
    }
    // The common world haze remains continuous through surface/orbit frames.
    float haze = 1.0 - exp(-distance * ae_fog_density);
    color = mix(color, ae_fog_color, clamp(haze,0.0,1.0));
    fragColor = vec4(max(color,vec3(0.0)), 1.0) * p3d_ColorScale;
}
"""


@lru_cache(maxsize=1)
def surface_shader():
    shared = Path(__file__).with_name("shaders") / "shadow.glsl"
    # The small fallback is useful to standalone renderers and tests that do
    # not instantiate the optional postprocess/shadow pipeline.
    shadow = (shared.read_text(encoding="utf-8") if shared.is_file() else
              "float ae_shadow(vec3 position, float ndotl) { return 1.0; }")
    return Shader.make(Shader.SL_GLSL, _VERTEX,
                       _FRAGMENT.replace("// AE_SHADOW", shadow).replace("// AE_GEOGRAPHY", ORBITAL_GLSL))


def configure_surface(node, field, origin, *, gpu=False):
    """Apply a hardware material or its deliberately complete CPU fallback."""
    texture = terrain_detail_texture(field.seed)
    node.setTexture(texture, 20)
    node.setTransparency(TransparencyAttrib.MNone, 25)
    if not gpu:
        return
    node.setShader(surface_shader(), 30)
    configure_orbital_material(node,field)
    node.setShaderInput("ae_patch_origin", *origin)
    node.setShaderInput("ae_soil", texture)
    node.setShaderInput("ae_relief",terrain_relief_texture(field.seed))
    for name,scale,offset in (("detail",.12,(0.,0.,0.)),
                              ("broad",.019,(13.5,41.9,5.6)),
                              ("grain",.47,(71.2,18.7,31.5))):
        node.setShaderInput("ae_"+name+"_origin",
            *((float(origin[i])*scale+offset[i])%1. for i in range(3)))
    node.setShaderInput("ae_biome", float(_BIOMES.index(field.biome)
                                         if field.biome in _BIOMES else 0))
    node.setShaderInput("ae_sky", *field.planet["sky"][:3])
    node.setShaderInput("ae_water", *field.water)
    node.setShaderInput("ae_ground", *field.ground)


def update_surface_materials(body, field, observer, elapsed):
    """Shared body uniforms keep every LOD leaf in the same material frame."""
    body.setShaderInput("ae_observer", *(float(observer[i]) - field._center[i]
                                       for i in range(3)))
    body.setShaderInput("ae_time", float(elapsed) % 86400.)
