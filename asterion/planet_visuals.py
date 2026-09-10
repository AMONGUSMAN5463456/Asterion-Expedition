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
        self.threshold={"oceanic":.15,"verdant":-.02,"fungal":-.09,
            "crystalline":-.16,"frozen":-.13,"toxic":-.12,
            "desert":-.53,"volcanic":-.58}.get(self.biome,-.02)
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
        floor=math.floor
        ix,iy,iz=floor(x),floor(y),floor(z)
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
    # Fixed storm eyes live inline in color() as 3D unit vectors, so swirl
    # offsets use only 3D dot/cross products and never show a seam.
    def color(self,normal):
        x,y,z=normal
        p,q,r,s,t,u=self.phase
        noise=self.noise
        smooth=_smooth
        mix=_mix
        shade=_shade
        sin=math.sin
        cos=math.cos
        exp=math.exp
        sqrt=math.sqrt
        # Hoisted once per texel: attribute lookups dominate this hot path
        # (a full 512x256 bake calls color() ~131k times).
        biome=self.biome
        threshold=self.threshold
        deep_water=self.deep_water
        shallow_water=self.shallow_water
        coast=self.coast
        vegetation=self.vegetation
        highland=self.highland
        beach=self.beach
        dune_shadow=self.dune_shadow
        dune_light=self.dune_light
        basalt=self.basalt
        crystalline_shadow=self.crystalline_shadow
        fungal_shadow=self.fungal_shadow
        ground=self.ground
        flora=self.flora
        accent=self.accent
        clouds=self.clouds
        cloud_density=self.cloud_density
        # Rotated domain warping produces irregular connected continental
        # shelves, island arcs and eroded highlands instead of latitude bands.
        warp=noise(x*2+p,y*2+q,z*2+r)
        a,b,c=x*3.2+p+warp*.9,y*3.2+q+warp*.7,z*3.2+r
        low=noise(a,b,c)
        # Sharpened large-scale contrast: an S-curve around zero steepens
        # continental shelves while keeping the noise range and threshold.
        low_s=(smooth(-.6,.6,low)*2-1)*.6
        medium=noise(a*2.13+s,b*2.13+t,c*2.13+u)
        fine=noise(a*5.7+t,b*5.7+u,c*5.7+s)
        # Second archipelago octave: small amplitude flips only coastlines,
        # scattering island chains through the oceans.
        arch=noise(a*4.3+u,b*4.3+s,c*4.3+t)
        elevation=low_s+.28*medium+.095*fine+.06*arch
        height=elevation-threshold
        sea=mix(deep_water,shallow_water,smooth(-.47,-.045,height))
        land=mix(vegetation,highland,smooth(.025,.46,height))
        if biome=="desert":
            dunes=.5+.5*sin((x+y*.45)*53+z*22+medium*6)
            land=mix(dune_shadow,dune_light,.25+.65*dunes)
            band=.5+.5*sin(z*47+medium*7+x*4)
            land=shade(land,.96+.08*band)
        elif biome=="volcanic":
            land=mix((.055,.042,.065),basalt,.4+.4*medium)
            fissure=(1-smooth(.012,.075,abs(medium+.23*fine)))*smooth(-.1,.22,low)
            land=mix(land,(1,.22,.025),fissure*.92)
            ember=(1-smooth(.004,.035,abs(fine-.13)))*(1-smooth(.20,.55,abs(z)))
            land=mix(land,(1.0,.32,.06),ember*.75)
        elif biome=="frozen":
            land=mix((.22,.48,.66),(.78,.89,.93),smooth(-.12,.37,elevation))
            land=mix(land,(.10,.31,.47),(1-smooth(.008,.045,abs(fine)))*.25)
        elif biome=="crystalline":
            land=mix(crystalline_shadow,accent,smooth(.10,.62,elevation)*.62)
        elif biome=="fungal":
            land=mix(fungal_shadow,ground,smooth(-.1,.45,medium))
            blotch=noise(x*9+u+medium*2,y*9+s,z*9+t)
            land=mix(land,accent,smooth(.25,.70,blotch)*.35)
        elif biome=="toxic":
            blotch=noise(x*9+u+medium*2,y*9+s,z*9+t)
            land=mix(land,flora,smooth(.20,.65,blotch)*.30)
            land=mix(land,accent,smooth(.45,.80,fine)*.25)
        # Narrow beaches and ridges read clearly at normal orbit distances.
        land=mix(beach,land,smooth(.008,.062,height))
        base=mix(sea,land,smooth(-.012,.014,height))
        relief=.88+.12*smooth(-.2,.4,fine)+.10*medium
        base=shade(base,relief)
        az=z if z>=0 else -z
        if biome in ("frozen","crystalline","oceanic"):
            # Noisy latitude ice cap reaching over sea and land alike.
            cap=smooth(.68,.76,az+.05*fine)
            base=mix(base,(.87,.93,.96),cap)
        if biome not in ("desert","volcanic","toxic"):
            ice=smooth(.79,.93,az+.04*medium)
            base=mix(base,(.80,.91,.94),ice*.96)
        if clouds:
            # Twisted weather fronts and small puffs are baked into the same
            # opaque surface: no near-coincident transparent cloud sphere.
            # Two large spiral storms swirl the sample point around fixed 3D
            # eyes (Rodrigues rotation, angle ~ 1/radius with gaussian
            # falloff), so fronts wind into spirals near the eyes only.
            qx,qy,qz=x,y,z
            for sx,sy,sz,twist,rad in ((.348,.795,.497,2.6,.55),
                                       (-.781,-.279,-.558,-2.2,.62)):
                d=qx*sx+qy*sy+qz*sz
                d=1.0 if d>1 else -1.0 if d<-1 else d
                ch=sqrt(2-2*d) if d<1 else 0.0
                g=ch/rad
                w=exp(-g*g)
                ang=twist*w/(ch+.25)
                co,si=cos(ang),sin(ang)
                cx=sy*qz-sz*qy
                cy=sz*qx-sx*qz
                cz=sx*qy-sy*qx
                ic=1-co
                qx,qy,qz=(qx*co+cx*si+sx*d*ic,
                          qy*co+cy*si+sy*d*ic,
                          qz*co+cz*si+sz*d*ic)
            curl=.45*sin(qz*8+s)+.26*sin(qy*5+t)
            front=noise(qx*6.2+p+curl,qy*6.2+q-curl,qz*6.2+r)
            small=noise(qx*17+s,qy*17+t,qz*17+u)
            density=cloud_density
            cover=smooth(.24,.67,front+small*.26)*density
            wisps=(1-smooth(.015,.10,abs(front-.11)))*smooth(.04,.45,small)*density*.42
            cover=min(.85,cover+wisps)
            shadow=smooth(.18,.5,front)*.10*(1-cover)
            base=shade(base,1-shadow)
            cloud=(.80,.87,.92) if biome!="toxic" else (.71,.77,.38)
            base=mix(base,cloud,cover)
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
    paint_color=paint.color
    pixels=bytearray(width*height*3)
    tau=math.tau
    cos=math.cos
    sin=math.sin
    trig=tuple((cos(tau*(i+.5)/width),sin(tau*(i+.5)/width)) for i in range(width))
    index=0
    # RAM image rows run bottom-to-top, matching sphere v=0 at the south pole.
    for j in range(height):
        if j in (0,height-1):
            # One shared pole colour avoids a pinwheel of different texels
            # meeting at the sphere's single pole vertex under V clamping.
            pole=paint_color((0,0,-1 if j==0 else 1))
            pr,pg,pb=int(pole[0]*255+.5),int(pole[1]*255+.5),int(pole[2]*255+.5)
            for _ in range(width):
                pixels[index]=pr
                pixels[index+1]=pg
                pixels[index+2]=pb
                index+=3
            continue
        lat=-math.pi*.5+math.pi*(j+.5)/height
        cl,sl=cos(lat),sin(lat)
        for ca,sa in trig:
            color=paint_color((cl*ca,cl*sa,sl))
            pixels[index]=int(color[0]*255+.5)
            pixels[index+1]=int(color[1]*255+.5)
            pixels[index+2]=int(color[2]*255+.5)
            index+=3
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
    rand=rng.random
    sin=math.sin
    tau=math.tau
    step3=tau/width
    step4=tau*4/width
    for y in range(width):
        wave=1.8*sin(y*step4)
        row=y*width*3
        for x in range(width):
            grain=rand()
            tint=rand()
            vein=sin((x*3+y*2)*step3+wave)
            faint=sin((x*2-y*3)*step3-wave*.5)
            # Mean ~0.78 keeps terrain brightness; warm/cool variance tints
            # channels without shifting the average.
            value=.70+.16*grain+.03*vein+.02*faint
            if grain>.976:
                value*=.45
            warm=tint-.5
            r=value*(1+.06*warm)
            g=value*(1+.015*warm)
            b=value*(1-.06*warm)
            i=row+x*3
            pixels[i]=int(max(0.0,min(1.0,r))*255)
            pixels[i+1]=int(max(0.0,min(1.0,g))*255)
            pixels[i+2]=int(max(0.0,min(1.0,b))*255)
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
        # Warm tint hugs the terminator where incidence crosses zero.
        term=1-_smooth(0.0,.28,abs(incidence))
        return (.07+.85*daylight+.055*edge+.06*term,
                .095+.82*daylight+.045*edge+.025*term,
                .15+.76*daylight+.025*edge,1)
    mesh.sphere((0,0,0),(radius,)*3,(1,1,1),segments,rings,
                color_fn=illumination,smooth=True,textured=True)
    return mesh


def atmosphere_mesh(radius,color):
    """Camera-facing limb glow, with depth-tested empty space at its center."""
    mesh=Mesh()
    cos=math.cos
    sin=math.sin
    tau=math.tau
    step=tau/96
    angles=tuple(i*step for i in range(97))
    r3=color[0],color[1],color[2]
    for inner,outer,a,b in ((.998,1.010,.30,.18),(1.010,1.028,.18,.08),(1.028,1.055,.08,.02),(1.055,1.090,.02,0)):
        ir,orr=radius*inner,radius*outer
        c0=(*r3,a)
        c1=(*r3,b)
        for i in range(96):
            a0,a1=angles[i],angles[i+1]
            c0a,s0a=cos(a0),sin(a0)
            c1a,s1a=cos(a1),sin(a1)
            p0=(ir*c0a,0,ir*s0a)
            p1=(orr*c0a,0,orr*s0a)
            p2=(orr*c1a,0,orr*s1a)
            p3=(ir*c1a,0,ir*s1a)
            mesh.tri(p0,p1,p2,c0,colors=(c0,c1,c1))
            mesh.tri(p0,p2,p3,c0,colors=(c0,c1,c0))
    return mesh
