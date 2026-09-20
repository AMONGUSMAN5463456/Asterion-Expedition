#version 150
in vec3 normal;
in vec3 view;
in vec4 color;
out vec4 fragColor;
void main(){
    vec3 n=normalize(normal),v=normalize(-view),l=normalize(vec3(-.45,.72,.58));
    vec3 h=normalize(l+v);
    float diffuse=max(dot(n,l),0.);
    float spec=pow(max(dot(n,h),0.),48.);
    float rim=pow(1.-max(dot(n,v),0.),3.5);
    vec3 base=color.rgb*(vec3(.33,.42,.51)+vec3(.88,.81,.68)*diffuse);
    float neutral=1.-smoothstep(.05,.3,max(color.r,max(color.g,color.b))-min(color.r,min(color.g,color.b)));
    base+=vec3(.72,.84,.9)*spec*(.10+neutral*.28);
    base+=vec3(.13,.28,.34)*rim*.33;
    fragColor=vec4(base,color.a);
}
