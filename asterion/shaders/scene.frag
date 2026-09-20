#version 150
// SHADOW_INCLUDE
uniform vec3 ae_sun_direction;
uniform vec3 ae_camera;
uniform vec3 ae_up;
uniform vec3 ae_fog_color;
uniform float ae_fog_density;
uniform vec4 ae_material;
uniform vec4 ae_body;
in vec3 v_world;
in vec3 v_normal;
in vec3 v_view;
in vec4 v_color;
out vec4 fragColor;
void main() {
    vec3 n = normalize(v_normal);
    if (!gl_FrontFacing) n = -n;
    vec3 l = normalize(ae_sun_direction);
    vec3 v = normalize(ae_camera-v_world);
    vec3 h = normalize(l+v);
    float nl = max(dot(n,l), 0.0);
    vec3 radial = ae_up;
    float solar = 1.0;
    float daylight = 1.0;
    if (ae_body.w > 0.0) {
        vec3 offset = v_world-ae_body.xyz;
        float radius = max(length(offset),ae_body.w);
        radial = normalize(offset);
        float horizon = -sqrt(max(0.0,1.0-pow(ae_body.w/radius,2.0)));
        float elevation = dot(radial,l);
        solar = smoothstep(horizon-.03,horizon+.03,elevation);
        daylight = smoothstep(-.18,.20,elevation);
    }
    float sky = dot(n,radial)*0.5+0.5;
    float rough = clamp(ae_material.x, 0.08, 1.0);
    float metal = ae_material.y;
    vec3 albedo = max(v_color.rgb, vec3(0.0));
    float shadow = ae_shadow(v_world, nl) * solar;
    vec3 ambient = mix(vec3(.15,.17,.15), vec3(.39,.48,.57), sky);
    ambient = mix(vec3(.055,.085,.14)*(.65+sky*.35),ambient,daylight);
    vec3 diffuse = albedo * (ambient + vec3(1.12,1.01,.79) * nl * shadow);
    float spec = pow(max(dot(n,h),0.0), mix(120.0,8.0,rough)) * (1.0-rough*.72);
    vec3 f0 = mix(vec3(.055),albedo,metal);
    vec3 fresnel = f0+(1.0-f0)*pow(1.0-max(dot(v,h),0.0),5.0);
    vec3 reflection = fresnel * spec * shadow * 2.5;
    float rim = pow(1.0-max(dot(n,v),0.0),4.0)*sky;
    vec3 sheen = vec3(.28,.43,.51)*rim*(.12+metal*.35)*mix(.2,1.,daylight);
    float organic = max(ae_material.w, smoothstep(.03,.18,albedo.g-max(albedo.r,albedo.b)));
    float transmission = pow(max(dot(-n,l),0.0),2.0)*organic;
    vec3 color = diffuse + reflection + sheen + albedo*transmission*.30*solar;
    color += albedo*ae_material.z;
    float fog = 1.0-exp(-length(v_view)*max(0.0,ae_fog_density));
    color = mix(color,ae_fog_color,clamp(fog,0.0,1.0));
    fragColor=vec4(color,v_color.a);
}
