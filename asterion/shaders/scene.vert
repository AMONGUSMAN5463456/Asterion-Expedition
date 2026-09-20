#version 150
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;
uniform mat4 p3d_ModelViewMatrix;
uniform mat3 p3d_NormalMatrix;
uniform vec4 p3d_ColorScale;
uniform float ae_time;
uniform vec3 ae_wind;
in vec4 p3d_Vertex;
in vec3 p3d_Normal;
in vec4 p3d_Color;
in vec2 p3d_MultiTexCoord0;
out vec3 v_world;
out vec3 v_normal;
out vec3 v_view;
out vec4 v_color;
void main() {
    vec4 point = p3d_Vertex;
    float weight = clamp(p3d_MultiTexCoord0.x,0.0,1.0);
    float phase = dot((p3d_ModelMatrix * point).xyz,vec3(.37,.23,.19));
    point.xyz += ae_wind * weight * (.075*sin(ae_time*1.5+phase)+.035*sin(ae_time*3.1+phase*2.3));
    gl_Position = p3d_ModelViewProjectionMatrix * point;
    v_world = (p3d_ModelMatrix * point).xyz;
    v_normal = normalize(mat3(p3d_ModelMatrix) * p3d_Normal);
    v_view = (p3d_ModelViewMatrix * p3d_Vertex).xyz;
    v_color = p3d_Color * p3d_ColorScale;
}
