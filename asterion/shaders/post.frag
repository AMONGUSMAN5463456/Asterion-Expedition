#version 150
uniform sampler2D ae_scene;
uniform sampler2D ae_bloom;
uniform vec2 ae_pixel;
in vec2 uv;
out vec4 fragColor;
float luma(vec3 c) { return dot(c,vec3(.299,.587,.114)); }
void main() {
    vec3 center=texture(ae_scene,uv).rgb;
    vec3 nw=texture(ae_scene,uv+vec2(-1,-1)*ae_pixel).rgb;
    vec3 ne=texture(ae_scene,uv+vec2(1,-1)*ae_pixel).rgb;
    vec3 sw=texture(ae_scene,uv+vec2(-1,1)*ae_pixel).rgb;
    vec3 se=texture(ae_scene,uv+vec2(1,1)*ae_pixel).rgb;
    float lc=luma(center), lnw=luma(nw), lne=luma(ne), lsw=luma(sw), lse=luma(se);
    vec2 direction=vec2(-((lnw+lne)-(lsw+lse)), (lnw+lsw)-(lne+lse));
    float reduce=max((lnw+lne+lsw+lse)*.03125,.0078125);
    direction=clamp(direction/(min(abs(direction.x),abs(direction.y))+reduce),vec2(-6),vec2(6))*ae_pixel;
    vec3 a=.5*(texture(ae_scene,uv+direction*(-1.0/6.0)).rgb+texture(ae_scene,uv+direction*(1.0/6.0)).rgb);
    vec3 b=a*.5+.25*(texture(ae_scene,uv-direction*.5).rgb+texture(ae_scene,uv+direction*.5).rgb);
    float lb=luma(b), lo=min(lc,min(min(lnw,lne),min(lsw,lse))), hi=max(lc,max(max(lnw,lne),max(lsw,lse)));
    vec3 color=(lb<lo || lb>hi) ? a : b;
    // Selective radiance bloom, then a mild filmic shoulder; UI stays untouched.
    color += texture(ae_bloom,uv).rgb*.14;
    // Continuous highlight shoulder: preserve midtones while keeping bright
    // ceramic, ice and emission below display white instead of clipping them.
    vec3 highlight=max(color-vec3(.65),vec3(0));
    color=min(color,vec3(.65))+.35*(1.0-exp(-highlight/.35));
    color = mix(vec3(luma(color)),color,1.04);
    vec2 p=uv*2.0-1.0;
    color *= 1.0-.075*pow(clamp(dot(p,p)*.5,0.0,1.0),1.5);
    fragColor=vec4(max(color,vec3(0)),1.0);
}
