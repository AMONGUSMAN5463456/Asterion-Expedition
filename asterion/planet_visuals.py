"""Deterministic atlas-free planet art, baked entirely on the CPU.

The texture contains albedo, cloud cover and tiny terrain shadows. Lighting is
an explicit vertex colour on the sphere. Neither depends on a GPU shader or an
inherited material, including Panda's software renderer. Maps wrap in 3D, so
continents and clouds have no longitude seam. Cache size bounds system travel.
"""
from __future__ import annotations

from functools import lru_cache
import math
import random

from panda3d.core import SamplerState, Texture

from .geometry import Mesh


def _smooth(a, b, value):
    if value <= a:
        return 0.0
    if value >= b:
        return 1.0
    t=(value-a)/(b-a)
    return t*t*(3.0-2.0*t)


def _mix(a,b,t):
    """RGB-only hot path; avoid creating and discarding alpha per texel."""
    t=0.0 if t<0 else 1.0 if t>1 else t
    s=1.0-t
    return (a[0]*s+b[0]*t,a[1]*s+b[1]*t,a[2]*s+b[2]*t)


def _shade(color,factor):
    r,g,b=color[0]*factor,color[1]*factor,color[2]*factor
    return (max(0.0,min(1.0,r)),max(0.0,min(1.0,g)),max(0.0,min(1.0,b)))


class PlanetPaint:
    """Continuous spherical biome palette; independent of gameplay randomness."""

    def __init__(self, planet, clouds=True):
        self.seed=int(planet["seed"])
        self.biome=planet.get("biome","verdant")
        self.ground=tuple(planet["ground"][:3])
        self.water=tuple(planet["water"][:3])
        self.flora=tuple(planet.get("flora",self.ground)[:3])
        self.accent=tuple(planet["accent"][:3])
        self.clouds=clouds
        self.threshold={"oceanic":.16,"verdant":-.025,"fungal":-.10,
            "crystalline":-.17,"frozen":-.14,"toxic":-.13,
            "desert":-.55,"volcanic":-.60}.get(self.biome,-.02)
        self.cloud_density={"desert":.25,"volcanic":.17,"toxic":.55,
            "frozen":.60}.get(self.biome,.43)
        self.deep_water=_shade(self.water,.39)
        self.shallow_water=_shade(self.water,.85)
        self.coast=_mix(self.water,self.accent,.24)
        self.vegetation=_mix(_shade(self.flora,.70),self.ground,.45)
        self.highland=_shade(self.ground,1.17)
        self.beach=_mix(self.ground,(.75,.67,.43),.45)
        self.dune_shadow=_shade(self.ground,.65)
        self.dune_light=_mix(self.ground,(.91,.57,.25),.27)
        self.basalt=_shade(self.ground,.70)
        self.crystalline_shadow=_shade(self.ground,.52)
        self.fungal_shadow=_shade(self.flora,.56)
        rng=random.Random(self.seed ^ 0x51972)
        self.phase=tuple(rng.uniform(-30,30) for _ in range(6))
        # A tiny tileable lattice avoids per-pixel hashing and global RNG use.
        self.lattice=tuple(rng.uniform(-1,1) for _ in range(4096))

    def noise(self,x,y,z):
        ix,iy,iz=math.floor(x),math.floor(y),math.floor(z)
        fx,fy,fz=x-ix,y-iy,z-iz
        fx,fy,fz=fx*fx*(3-2*fx),fy*fy*(3-2*fy),fz*fz*(3-2*fz)
        table=self.lattice
        x0,x1=ix&15,(ix+1)&15
        y0,y1=(iy&15)*16,((iy+1)&15)*16
        z0,z1=(iz&15)*256,((iz+1)&15)*256
        a=table[x0+y0+z0]*(1-fx)+table[x1+y0+z0]*fx
        b=table[x0+y1+z0]*(1-fx)+table[x1+y1+z0]*fx
        c=table[x0+y0+z1]*(1-fx)+table[x1+y0+z1]*fx
        d=table[x0+y1+z1]*(1-fx)+table[x1+y1+z1]*fx
        return (a*(1-fy)+b*fy)*(1-fz)+(c*(1-fy)+d*fy)*fz

    def color(self,normal):
        x,y,z=normal
        p,q,r,s,t,u=self.phase
        # Rotated domain warping produces irregular connected continental
        # shelves, island arcs and eroded highlands instead of latitude bands.
        warp=self.noise(x*2+p,y*2+q,z*2+r)
        a,b,c=x*3.2+p+warp*.9,y*3.2+q+warp*.7,z*3.2+r
        low=self.noise(a,b,c)
        medium=self.noise(a*2.13+s,b*2.13+t,c*2.13+u)
        fine=self.noise(a*5.7+t,b*5.7+u,c*5.7+s)
        elevation=low+.28*medium+.095*fine
        biome=self.biome
        height=elevation-self.threshold
        sea=_mix(self.deep_water,self.shallow_water,_smooth(-.47,-.045,height))
        sea=_mix(sea,self.coast,_smooth(-.10,.01,height)*.70)
        land=_mix(self.vegetation,self.highland,_smooth(.025,.46,height))
        if biome=="desert":
            dunes=.5+.5*math.sin((x+y*.45)*53+z*22+medium*6)
            land=_mix(self.dune_shadow,self.dune_light,.25+.65*dunes)
        elif biome=="volcanic":
            land=_mix((.055,.042,.065),self.basalt,.4+.4*medium)
            fissure=(1-_smooth(.012,.075,abs(medium+.23*fine)))*_smooth(-.1,.22,low)
            land=_mix(land,(1,.22,.025),fissure*.92)
        elif biome=="frozen":
            land=_mix((.22,.48,.66),(.78,.89,.93),_smooth(-.12,.37,elevation))
            land=_mix(land,(.10,.31,.47),(1-_smooth(.008,.045,abs(fine)))*.25)
        elif biome=="crystalline":
            land=_mix(self.crystalline_shadow,self.accent,_smooth(.10,.62,elevation)*.62)
        elif biome=="fungal":
            land=_mix(self.fungal_shadow,self.ground,_smooth(-.1,.45,medium))
        # Narrow beaches and ridges read clearly at normal orbit distances.
        land=_mix(self.beach,land,_smooth(.008,.062,height))
        base=_mix(sea,land,_smooth(-.012,.014,height))
        relief=.88+.12*_smooth(-.2,.4,fine)+.10*medium
        base=_shade(base,relief)
        if biome not in ("desert","volcanic","toxic"):
            ice=_smooth(.79,.93,abs(z)+.04*medium)
            base=_mix(base,(.80,.91,.94),ice*.96)
        if self.clouds:
            # Twisted weather fronts and small puffs are baked into the same
            # opaque surface: no near-coincident transparent cloud sphere.
            curl=.45*math.sin(z*8+s)+.26*math.sin(y*5+t)
            front=self.noise(x*6.2+p+curl,y*6.2+q-curl,z*6.2+r)
            small=self.noise(x*17+s,y*17+t,z*17+u)
            density=self.cloud_density
            cover=_smooth(.24,.67,front+small*.26)*density
            wisps=(1-_smooth(.015,.10,abs(front-.11)))*_smooth(.04,.45,small)*density*.42
            cover=min(.80,cover+wisps)
            shadow=_smooth(.18,.5,front)*.10*(1-cover)
            base=_shade(base,1-shadow)
            cloud=(.80,.87,.92) if biome!="toxic" else (.71,.77,.38)
            base=_mix(base,cloud,cover)
        return base


def _paint_key(planet,clouds):
    return (int(planet["seed"]),planet.get("biome","verdant"),
            tuple(planet["ground"][:3]),tuple(planet["water"][:3]),
            tuple(planet.get("flora",planet["ground"])[:3]),tuple(planet["accent"][:3]),bool(clouds))


@lru_cache(maxsize=12)
def _texture(key,width,height):
    seed,biome,ground,water,flora,accent,clouds=key
    paint=PlanetPaint(dict(seed=seed,biome=biome,ground=ground,water=water,
                          flora=flora,accent=accent),clouds)
    pixels=bytearray(width*height*3)
    trig=tuple((math.cos(math.tau*(i+.5)/width),math.sin(math.tau*(i+.5)/width)) for i in range(width))
    index=0
    # RAM image rows run bottom-to-top, matching sphere v=0 at the south pole.
    for j in range(height):
        if j in (0,height-1):
            # One shared pole colour avoids a pinwheel of different texels
            # meeting at the sphere's single pole vertex under V clamping.
            pole=bytes(int(c*255+.5) for c in paint.color((0,0,-1 if j==0 else 1)))
            pixels[index:index+width*3]=pole*width
            index+=width*3
            continue
        lat=-math.pi*.5+math.pi*(j+.5)/height
        cl,sl=math.cos(lat),math.sin(lat)
        for ca,sa in trig:
            color=paint.color((cl*ca,cl*sa,sl))
            for component in color:
                pixels[index]=int(component*255+.5)
                index+=1
    texture=Texture(f"planet-map-{seed}-{biome}")
    texture.setup2dTexture(width,height,Texture.TUnsignedByte,Texture.FRgb8)
    texture.setRamImageAs(bytes(pixels),"RGB")
    texture.setMinfilter(SamplerState.FTLinearMipmapLinear)
    texture.setMagfilter(SamplerState.FTLinear)
    texture.setWrapU(SamplerState.WMRepeat)
    texture.setWrapV(SamplerState.WMClamp)
    texture.setAnisotropicDegree(2)
    texture.generateRamMipmapImages()
    return texture


def planet_texture(planet,clouds=True,width=512,height=256):
    return _texture(_paint_key(planet,clouds),int(width),int(height))


@lru_cache(maxsize=8)
def terrain_detail_texture(seed):
    """Small repeating soil grain; world-coordinate UVs join across chunks."""
    rng=random.Random(int(seed)^0xA597)
    width=128
    pixels=bytearray(width*width*3)
    for y in range(width):
        for x in range(width):
            grain=rng.random()
            vein=math.sin(math.tau*(x*3+y*2)/width+1.8*math.sin(math.tau*y*4/width))
            value=.76+.17*grain+.065*vein
            if grain>.974:
                value=.98
            i=(y*width+x)*3
            pixels[i:i+3]=bytes((int(value*255),)*3)
    texture=Texture(f"soil-grain-{seed}")
    texture.setup2dTexture(width,width,Texture.TUnsignedByte,Texture.FRgb8)
    texture.setRamImageAs(bytes(pixels),"RGB")
    texture.setMinfilter(SamplerState.FTLinearMipmapLinear)
    texture.setMagfilter(SamplerState.FTLinear)
    texture.setWrapU(SamplerState.WMRepeat)
    texture.setWrapV(SamplerState.WMRepeat)
    texture.setAnisotropicDegree(4)
    texture.generateRamMipmapImages()
    return texture


def planet_mesh(radius,segments=96,rings=48):
    mesh=Mesh()
    # A long soft terminator keeps the night side dimensional and coloured.
    def illumination(n):
        incidence=n[0]*-.68+n[1]*-.62+n[2]*.39
        daylight=max(0,incidence)**.66
        edge=_smooth(-.15,.18,incidence)
        return (.07+.85*daylight+.055*edge,
                .095+.82*daylight+.045*edge,
                .15+.76*daylight+.025*edge,1)
    mesh.sphere((0,0,0),(radius,)*3,(1,1,1),segments,rings,
                color_fn=illumination,smooth=True,textured=True)
    return mesh


def atmosphere_mesh(radius,color):
    """Camera-facing limb glow, with depth-tested empty space at its center."""
    mesh=Mesh()
    for inner,outer,a,b in ((.998,1.012,.13,.26),(1.012,1.035,.26,.09),(1.035,1.085,.09,0)):
        for i in range(96):
            a0,a1=i*math.tau/96,(i+1)*math.tau/96
            def point(r,angle):
                return (radius*r*math.cos(angle),0,radius*r*math.sin(angle))
            c0,c1=(*color[:3],a),(*color[:3],b)
            p0,p1,p2,p3=point(inner,a0),point(outer,a0),point(outer,a1),point(inner,a1)
            mesh.tri(p0,p1,p2,c0,colors=(c0,c1,c1))
            mesh.tri(p0,p2,p3,c0,colors=(c0,c1,c0))
    return mesh
