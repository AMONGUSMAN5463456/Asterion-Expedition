#version 150
uniform sampler2D ae_scene;
uniform vec2 ae_pixel;
in vec2 uv;
out vec4 fragColor;
void main() {
    vec3 result=vec3(0);
    float weight=0.0;
    for (int y=-2;y<=2;++y) for (int x=-2;x<=2;++x) {
        float w=exp(-float(x*x+y*y)*.35);
        vec3 c=texture(ae_scene,uv+vec2(x,y)*ae_pixel*3.0).rgb;
        float br=max(c.r,max(c.g,c.b));
        result+=c*smoothstep(.72,1.3,br)*w;
        weight+=w;
    }
    fragColor=vec4(result/weight,1);
}
