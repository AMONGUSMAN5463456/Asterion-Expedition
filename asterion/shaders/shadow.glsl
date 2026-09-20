uniform sampler2D ae_shadow_map;
uniform mat4 ae_world_to_shadow;
uniform float ae_shadow_enabled;
uniform float ae_shadow_texel;

float ae_shadow(vec3 renderPosition, float ndotl) {
    if (ae_shadow_enabled <= 0.0) return 1.0;
    vec4 clip = ae_world_to_shadow * vec4(renderPosition, 1.0);
    vec3 p = clip.xyz / clip.w * 0.5 + 0.5;
    if (p.z <= 0.0 || p.z >= 1.0) return 1.0;
    float edge = smoothstep(0.0, 0.07, min(min(p.x, p.y), min(1.0-p.x, 1.0-p.y)));
    if (edge <= 0.0) return 1.0;
    float bias = max(0.0011, 0.0030 * (1.0 - ndotl));
    bias = max(bias, min(0.006, fwidth(p.z)*2.0));
    float visibility = 0.0;
    float total_weight = 0.0;
    vec2 position = p.xy / ae_shadow_texel - 0.5;
    vec2 base = floor(position);
    vec2 fraction = fract(position);
    for (int y = -1; y <= 2; ++y) {
        for (int x = -1; x <= 2; ++x) {
            vec2 delta = abs(vec2(x,y)-fraction);
            vec2 weights = max(vec2(0), vec2(2.0)-delta);
            float weight = weights.x*weights.y;
            float depth = texture(ae_shadow_map, (base+vec2(x,y)+0.5)*ae_shadow_texel).r;
            visibility += step(p.z - bias, depth)*weight;
            total_weight += weight;
        }
    }
    return mix(1.0, visibility / total_weight, edge*ae_shadow_enabled);
}
