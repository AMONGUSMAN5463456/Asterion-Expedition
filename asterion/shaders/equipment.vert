#version 150
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelViewMatrix;
uniform mat3 p3d_NormalMatrix;
uniform vec4 p3d_ColorScale;
in vec4 p3d_Vertex;
in vec3 p3d_Normal;
in vec4 p3d_Color;
out vec3 normal;
out vec3 view;
out vec4 color;
void main(){
    gl_Position=p3d_ModelViewProjectionMatrix*p3d_Vertex;
    normal=p3d_NormalMatrix*p3d_Normal;
    view=(p3d_ModelViewMatrix*p3d_Vertex).xyz;
    color=p3d_Color*p3d_ColorScale;
}
