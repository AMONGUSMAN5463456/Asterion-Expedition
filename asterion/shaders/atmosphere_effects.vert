#version 150
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform vec4 p3d_ColorScale;
in vec4 p3d_Vertex;
in vec4 p3d_Color;
out vec2 point;
out vec4 color;
void main(){
    gl_Position=p3d_ModelViewProjectionMatrix*p3d_Vertex;
    point=p3d_Vertex.xz;
    color=p3d_Color*p3d_ColorScale;
}
