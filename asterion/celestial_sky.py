"""A continuous painted atmosphere and a fine, system-fixed stellar backdrop."""
from __future__ import annotations

from functools import lru_cache
import math
import random

from panda3d.core import (ColorBlendAttrib, PNMImage, Shader, Texture,
                          TransparencyAttrib, Vec3)

from .geometry import Mesh, mix

_VERTEX = """#version 150
uniform mat4 p3d_ModelViewProjectionMatrix;
in vec4 p3d_Vertex;
out vec3 ray;
void main(){gl_Position=p3d_ModelViewProjectionMatrix*p3d_Vertex;ray=p3d_Vertex.xyz;}
"""

_FRAGMENT = """#version 150
uniform vec3 ae_sky_tone;
uniform vec3 ae_sky_sun;
uniform mat4 ae_sky_rotation;
uniform float ae_sky_density;
uniform float ae_sky_daylight;
in vec3 ray;
out vec4 fragColor;
float hash(vec3 p){p=fract(p*.1031);p+=dot(p,p.yzx+33.33);return fract((p.x+p.y)*p.z);}
float noise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),
mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
float fbm(vec3 p){return noise(p)*.56+noise(p*2.03+5.7)*.28+noise(p*4.13+12.1)*.11+noise(p*8.3)*.05;}
void main(){
    vec3 d=normalize(ray);
    float height=clamp(d.z,0.,1.);
    float sun=clamp(dot(d,normalize(ae_sky_sun)),0.,1.);
    float day=ae_sky_daylight;
    vec3 zenith=mix(vec3(.075,.22,.40),ae_sky_tone*1.15,.68);
    vec3 horizon=mix(vec3(.67,.78,.80),ae_sky_tone*.90+vec3(.32),.54);
    vec3 air=mix(horizon,zenith,pow(height,.42));
    air*=mix(.16,1.,smoothstep(.22,.65,day));
    air+=vec3(.45,.28,.105)*pow(sun,12.)*day;
    air+=vec3(.24,.26,.24)*pow(sun,140.)*day;
    // High, slowly eroded cirrus is directional sky texture, not a terrain layer.
    vec3 weather=d*vec3(9.,9.,25.);
    float cirrus=smoothstep(.61,.76,fbm(weather+fbm(d*5.)*2.));
    cirrus*=smoothstep(.06,.22,d.z)*(1.-smoothstep(.65,.95,d.z));
    air=mix(air,vec3(.87,.88,.83)*mix(.25,1.,day),cirrus*.24);
    // A restrained dust lane gives orbit depth while leaving planets dominant.
    vec3 fixed_ray=normalize((ae_sky_rotation*vec4(d,0.)).xyz);
    vec3 galactic=vec3(fixed_ray.x*.87-fixed_ray.z*.5,fixed_ray.y,fixed_ray.x*.5+fixed_ray.z*.87);
    float band=exp(-pow((galactic.z+.15*sin(galactic.x*3.))/.19,2.));
    float dust=fbm(galactic*9.+vec3(14.,3.,7.));
    float filament=fbm(galactic*26.);
    vec3 space=vec3(.004,.008,.021);
    space+=mix(vec3(.055,.085,.13),vec3(.20,.10,.16),dust)*band*smoothstep(.28,.76,dust);
    space+=vec3(.055,.075,.11)*band*pow(filament,4.);
    float alpha=clamp(ae_sky_density,0.,1.);
    fragColor=vec4(mix(space,air,alpha),1.);
}
"""


def create_sky(parent, gpu=False):
    mesh = Mesh()
    def tint(n):
        h = max(0., n[2])
        return mix((.73, .84, .90), (.13, .37, .68), h ** .4)
    mesh.sphere((0, 0, 0), (9000,) * 3, (1, 1, 1), 64, 32,
                color_fn=tint, smooth=True)
    node = mesh.node("celestial-continuous-sky", parent, two_sided=True, unlit=True)
    node.setBin("background", 0)
    node.setDepthWrite(False)
    node.setDepthTest(False)
    node.setTransparency(TransparencyAttrib.MAlpha)
    node.setPythonTag("celestial-gpu", bool(gpu))
    if gpu:
        node.setShader(Shader.make(Shader.SL_GLSL, _VERTEX, _FRAGMENT), 20)
    update_sky(node, (.3, .5, .7), 1., Vec3(-.68, -.62, .39), .7)
    return node


def update_sky(node, tone, density, sun_local, daylight, elapsed=0.):
    if node.getPythonTag("celestial-gpu"):
        node.setShaderInput("ae_sky_tone", Vec3(*tone[:3]))
        node.setShaderInput("ae_sky_sun", sun_local)
        node.setShaderInput("ae_sky_density", float(density))
        node.setShaderInput("ae_sky_daylight", float(daylight))
        node.setShaderInput("ae_sky_rotation", node.getMat(node.getParent()))
        node.setColorScale(1, 1, 1, 1)
    else:
        node.setColorScale(*tuple(min(1., .32+c*1.25) for c in tone[:3]), density)


@lru_cache(maxsize=1)
def _star_texture():
    image = PNMImage(64, 64, 4)
    for y in range(64):
        for x in range(64):
            dx, dy = (x-31.5)/31.5, (y-31.5)/31.5
            radius = math.hypot(dx, dy)
            core = math.exp(-radius*radius*42.)
            halo = math.exp(-radius*radius*6.)*.16
            image.setXelA(x, y, 1., 1., 1., min(1., core+halo))
    texture = Texture("stellar-point-spread")
    texture.load(image)
    texture.setMinfilter(Texture.FTLinearMipmapLinear)
    texture.setMagfilter(Texture.FTLinear)
    texture.setWrapU(Texture.WMClamp)
    texture.setWrapV(Texture.WMClamp)
    return texture


def create_starfield(parent, seed, count=1800):
    rng = random.Random(seed)
    mesh = Mesh()
    for _ in range(count):
        z, angle = rng.uniform(-1, 1), rng.random()*math.tau
        r = math.sqrt(max(0., 1-z*z))
        direction = Vec3(r*math.cos(angle), r*math.sin(angle), z)
        center = direction*13000
        side = direction.cross(Vec3(0, 0, 1))
        if side.lengthSquared() < .001:
            side = Vec3(1, 0, 0)
        side.normalize()
        up = side.cross(direction)
        prominent = rng.random() < .075
        size = rng.uniform(9, 15) if not prominent else rng.uniform(20, 43)
        color = mix((.53, .72, 1), (1, .83, .59), rng.random())
        alpha = rng.uniform(.35, .8) if not prominent else 1.
        color = (*color[:3], alpha)
        points = tuple(tuple(p) for p in (center-side*size-up*size, center+side*size-up*size,
                        center+side*size+up*size, center-side*size+up*size))
        mesh.tri(*points[:3], color, texcoords=((0,0),(1,0),(1,1)))
        mesh.tri(points[0], points[2], points[3], color, texcoords=((0,0),(1,1),(0,1)))
    node = mesh.node("celestial-stellar-field", parent, two_sided=True, unlit=True)
    node.setTexture(_star_texture(), 1)
    node.setBin("background", 1)
    node.setDepthWrite(False)
    node.setDepthTest(False)
    node.setTransparency(TransparencyAttrib.MAlpha)
    node.setAttrib(ColorBlendAttrib.make(ColorBlendAttrib.MAdd,
        ColorBlendAttrib.OIncomingAlpha, ColorBlendAttrib.OOne))
    return node


def create_sun(parent, direction, color):
    root = parent.attachNewNode("celestial-sun-corona")
    root.setPos(direction*13900)
    root.setBillboardPointEye()
    mesh = Mesh()
    for radius, alpha in ((570, .08), (340, .13), (210, .34)):
        c = (*color[:3], alpha)
        mesh.tri((-radius,0,-radius),(radius,0,-radius),(radius,0,radius),c,
                 texcoords=((0,0),(1,0),(1,1)))
        mesh.tri((-radius,0,-radius),(radius,0,radius),(-radius,0,radius),c,
                 texcoords=((0,0),(1,1),(0,1)))
    glow = mesh.node("solar-corona", root, two_sided=True, unlit=True)
    glow.setTexture(_star_texture())
    glow.setTransparency(TransparencyAttrib.MAlpha)
    glow.setDepthWrite(False)
    glow.setAttrib(ColorBlendAttrib.make(ColorBlendAttrib.MAdd,
        ColorBlendAttrib.OIncomingAlpha, ColorBlendAttrib.OOne))
    disc = Mesh()
    for i in range(96):
        a,b = i*math.tau/96,(i+1)*math.tau/96
        disc.tri((0,-.1,0),(85*math.cos(a),-.1,85*math.sin(a)),
                 (85*math.cos(b),-.1,85*math.sin(b)),(1.,.97,.83))
    surface = disc.node("stellar-photosphere", root, two_sided=True, unlit=True)
    surface.setDepthWrite(False)
    return root
