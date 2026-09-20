#version 150
uniform float ae_effect_time;
uniform float ae_effect_kind;
in vec2 point;
in vec4 color;
out vec4 fragColor;
void main(){
    float radius=length(point);
    float angle=atan(point.y,point.x);
    float filament=.5+.5*sin(angle*31.+sin(angle*17.-ae_effect_time*3.)*2.
                            +radius*51.-ae_effect_time*11.);
    float billow=.5+.5*sin(point.x*11.+point.y*16.+sin(point.y*13.)*2.-ae_effect_time*2.);
    float texture=ae_effect_kind<.5 ? mix(.67,1.12,billow) : mix(.54,1.22,filament);
    vec3 light=color.rgb;
    if(ae_effect_kind>.5) light=mix(light,vec3(1.,.88,.59),filament*.22);
    fragColor=vec4(light,color.a*texture);
}
