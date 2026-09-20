"""Orbital colour sampled from PlanetField's exact seeded noise lattice.

Per-pixel geography refines coastlines independently of silhouette LOD. It
never changes physical terrain vertices or the collision surface.
"""
from array import array
from functools import lru_cache
from panda3d.core import Texture

GLSL = """
uniform sampler3D ae_geography;
uniform vec3 ae_phase_a;
uniform vec3 ae_phase_b;
uniform vec3 ae_flora;
uniform vec3 ae_accent;
uniform vec3 ae_planet_params;
float geography_noise(vec3 p) {
    vec3 cell=floor(p),f=fract(p); f=f*f*(3.0-2.0*f);
    return texture(ae_geography,(cell+f+.5)/16.0).r;
}
vec3 orbital_albedo(vec3 direction,float distance,out float depth) {
    float radius=ae_planet_params.x,water=ae_planet_params.y;
    float warp=geography_noise(direction*1.8+ae_phase_a);
    vec3 domain=direction*3.2+ae_phase_a+warp*vec3(.6,.45,.2);
    float low=geography_noise(domain);
    float medium=geography_noise(domain*2.03+ae_phase_b);
    float fine=geography_noise(domain*4.9+ae_phase_b.yzx);
    float continental=low+.27*medium+.075*fine-ae_planet_params.z;
    float detail=1.6*sin(radius*dot(direction,vec3(.036,.022,.013))+ae_phase_a.x)
                    *cos(radius*dot(direction,vec3(0,.029,-.017))+ae_phase_a.y);
    detail*=1.0-smoothstep(500.0,2500.0,distance);
    float home=radius*atan(length(direction.xy),direction.z);
    float blend=smoothstep(42.0,200.0,home);
    depth=mix(20.0-water,75.0*continental+detail,blend);
    vec3 vegetation=mix(ae_flora*.78,ae_ground,.40);
    vec3 land=mix(vegetation,ae_ground*1.22,smoothstep(9.0,54.0,depth));
    float ridges=geography_noise(direction*58.0+ae_phase_a)*.5+.5;
    float terraces=geography_noise(direction*127.0+ae_phase_b)*.5+.5;
    if(ae_biome==0.0 || ae_biome==5.0) {
        vec3 forest=mix(vegetation,vec3(.135,.255,.17),.42);
        vec3 meadow=mix(ae_ground,vec3(.40,.42,.235),.31);
        land=mix(forest,meadow,smoothstep(-.12,.36,medium));
        land=mix(land,vec3(.48,.46,.36),smoothstep(31.0,77.0,depth)*.57);
    } else if(ae_biome==1.0) {
        land=mix(ae_ground*.72,mix(ae_ground,vec3(.92,.62,.35),.24),ridges);
    } else if(ae_biome==2.0) {
        land=mix(vec3(.25,.49,.65),vec3(.81,.90,.94),smoothstep(-5.0,34.0,depth));
    } else if(ae_biome==3.0) {
        land=mix(vec3(.055,.045,.065),ae_ground*.8,.5+.3*medium);
        float fissure=(1.0-smoothstep(.012,.065,abs(medium+.23*fine)))*smoothstep(.05,.4,continental);
        land=mix(land,vec3(1.,.23,.025),fissure*.86);
    } else if(ae_biome==6.0) {
        land=mix(ae_ground*.65,ae_accent,smoothstep(15.0,66.0,depth)*.6);
    } else if(ae_biome==7.0) {
        land=mix(ae_flora*.65,ae_ground,smoothstep(-.3,.4,medium));
    }
    land*=.86+.13*ridges+.06*terraces;
    vec3 coast=mix(ae_ground,ae_accent,.20);
    land=mix(coast,land,smoothstep(.5,5.0,depth));
    vec3 sea=mix(ae_water*.36,ae_water*.86,smoothstep(-38.0,-.5,depth));
    vec3 result=mix(sea,land,smoothstep(-.7,.9,depth));
    if(ae_biome!=1.0 && ae_biome!=3.0 && ae_biome!=4.0) {
        float ice=smoothstep(.86,.975,abs(direction.y)+.025*medium);
        result=mix(result,vec3(.80,.90,.94),ice*.94);
    }
    return mix(vegetation,result,blend)*(.94+.06*fine+.045*medium);
}
"""

@lru_cache(maxsize=8)
def _lattice_texture(lattice):
    texture=Texture("shared-planet-geography")
    texture.setup3dTexture(16,16,16,Texture.TFloat,Texture.FR32)
    texture.setRamImage(array("f",lattice).tobytes())
    texture.setMinfilter(Texture.FTLinear)
    texture.setMagfilter(Texture.FTLinear)
    for axis in (texture.setWrapU,texture.setWrapV,texture.setWrapW):axis(Texture.WMRepeat)
    return texture

def configure_orbital_material(node,field):
    node.setShaderInput("ae_geography",_lattice_texture(field._lattice))
    node.setShaderInput("ae_phase_a",*field._phase[:3])
    node.setShaderInput("ae_phase_b",*field._phase[3:])
    node.setShaderInput("ae_flora",*field.flora)
    node.setShaderInput("ae_accent",*field.accent)
    node.setShaderInput("ae_planet_params",field.radius,field.water_level,field.threshold)
