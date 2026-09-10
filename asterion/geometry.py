"""Original, asset-free geometry for Asterion Expedition.

Everything is ordinary vertex-coloured geometry.  It needs neither a shader
pipeline nor a texture download, and remains useful on the software renderer.
"""
from __future__ import annotations

import math
import random

from panda3d.core import (
    Geom, GeomNode, GeomTriangles, GeomVertexData, GeomVertexFormat,
    GeomVertexWriter, Material, NodePath, TransparencyAttrib, Vec3,
)

TAU = math.tau


def rgba(color, alpha=1.0):
    if len(color) < 3:
        raise ValueError("color sequence must have at least 3 components")
    return (color[0], color[1], color[2], color[3] if len(color) > 3 else alpha)


def shade(color, factor):
    if len(color) < 3:
        raise ValueError("color sequence must have at least 3 components")
    if not math.isfinite(factor):
        factor = 1.0
    r, g, b = color[0] * factor, color[1] * factor, color[2] * factor
    if r > 1.0 or r != r:
        r = 1.0
    elif r < 0.0:
        r = 0.0
    if g > 1.0 or g != g:
        g = 1.0
    elif g < 0.0:
        g = 0.0
    if b > 1.0 or b != b:
        b = 1.0
    elif b < 0.0:
        b = 0.0
    return (r, g, b, color[3] if len(color) > 3 else 1.0)


def mix(a, b, t):
    if len(a) < 3 or len(b) < 3:
        raise ValueError("color sequence must have at least 3 components")
    if not math.isfinite(t):
        t = 0.0
    else:
        t = max(0.0, min(1.0, t))
    s = 1.0 - t
    la, lb = len(a), len(b)
    return (a[0] * s + b[0] * t, a[1] * s + b[1] * t,
            a[2] * s + b[2] * t,
            (a[3] if la > 3 else 1.0) * s + (b[3] if lb > 3 else 1.0) * t)


class Mesh:
    """A small batched mesh builder with per-corner normals and colours."""

    def __init__(self):
        self.vertices = []
        self.normals = []
        self.colors = []
        self.texcoords = []

    def tri(self, a, b, c, color, normals=None, colors=None, texcoords=None):
        if normals is None:
            abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            acx, acy, acz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = aby * acz - abz * acy, abz * acx - abx * acz, abx * acy - aby * acx
            length2 = nx * nx + ny * ny + nz * nz
            if length2 < 1e-16:
                return
            length = math.sqrt(length2)
            normals = ((nx / length, ny / length, nz / length),) * 3
        self.vertices.extend((tuple(a), tuple(b), tuple(c)))
        self.normals.extend(normals)
        self.colors.extend(tuple(map(rgba, colors)) if colors else (rgba(color),) * 3)
        if texcoords is not None:
            if not self.texcoords:
                self.texcoords.extend(((0, 0),) * (len(self.vertices)-3))
            self.texcoords.extend(texcoords)
        elif self.texcoords:
            self.texcoords.extend(((0, 0),) * 3)

    def quad(self, a, b, c, d, color):
        self.tri(a, b, c, color)
        self.tri(a, c, d, color)

    def box(self, center, size, color):
        x, y, z = center
        sx, sy, sz = (v * .5 for v in size)
        p = [(x + a*sx, y + b*sy, z + c*sz)
             for a, b, c in ((-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                              (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1))]
        for inds, tone in (((0,3,2,1),.7), ((4,5,6,7),1.08), ((0,1,5,4),.82),
                           ((1,2,6,5),.93), ((2,3,7,6),1), ((3,0,4,7),.9)):
            self.quad(*(p[i] for i in inds), shade(color, tone))

    def tube(self, a, b, radius, color, sides=8, end_radius=None, cap=True):
        """A capped tapered cylinder along an arbitrary segment."""
        a, b = Vec3(*a), Vec3(*b)
        axis = b - a
        if axis.lengthSquared() < 1e-12:
            return
        axis.normalize()
        ref = Vec3(0, 0, 1) if abs(axis.z) < .9 else Vec3(0, 1, 0)
        u = axis.cross(ref)
        u.normalize()
        v = axis.cross(u)
        end_radius = radius if end_radius is None else end_radius
        lower, upper = [], []
        cos, sin = math.cos, math.sin
        for i in range(sides):
            angle = TAU * i / sides
            radial = u * cos(angle) + v * sin(angle)
            lower.append(tuple(a + radial * radius))
            upper.append(tuple(b + radial * end_radius))
        for i in range(sides):
            j = (i + 1) % sides
            col = shade(color, .87 + .15 * sin(i*2.31 + .8))
            self.quad(lower[i], lower[j], upper[j], upper[i], col)
            if cap:
                self.tri(tuple(a), lower[j], lower[i], shade(color, .75))
                self.tri(tuple(b), upper[i], upper[j], shade(color, 1.1))

    def sphere(self, center, size, color, segments=12, rings=8, roughness=0,
               seed=0, color_fn=None, smooth=False, textured=False):
        """Ellipsoid; optional deterministic radial variation gives natural rocks."""
        rng = random.Random(seed)
        rand, cos, sin = rng.random, math.cos, math.sin
        shade_local = shade
        cx, cy, cz = center
        sx, sy, sz = size
        half_pi, pi = math.pi * 0.5, math.pi
        grid = []
        for j in range(rings + 1):
            lat = -half_pi + pi * j / rings
            cla, sla = cos(lat), sin(lat)
            pole = (j == 0 or j == rings)
            # One shared radius per pole row: independent offsets per segment
            # would splay the pole into a pinhole fan.
            pole_r = 1 + roughness * (rand() - .5) if pole else 1.0
            row = []
            for i in range(segments + 1):
                lon = TAU * (i % segments) / segments
                clon, slon = cos(lon), sin(lon)
                nx, ny, nz = cla * clon, cla * slon, sla
                n = (nx, ny, nz)
                r = pole_r if pole else 1 + roughness * (rand() - .5)
                point = (cx + nx * sx * r, cy + ny * sy * r, cz + nz * sz * r)
                col = color_fn(n) if color_fn else shade_local(color, .92 + rand() * .14)
                row.append((point, n, col))
            row[-1] = row[0]
            grid.append(row)
        for j in range(rings):
            for i in range(segments):
                a, b, c, d = grid[j][i], grid[j][i+1], grid[j+1][i+1], grid[j+1][i]
                uv=((i/segments,j/rings),((i+1)/segments,j/rings),
                    ((i+1)/segments,(j+1)/rings),(i/segments,(j+1)/rings))
                for tri,uvs in (((a,b,c),(uv[0],uv[1],uv[2])),
                                ((a,c,d),(uv[0],uv[2],uv[3]))):
                    ns = tuple(q[1] for q in tri) if smooth and size[0] == size[1] == size[2] else None
                    self.tri(*(q[0] for q in tri), color, normals=ns,
                             colors=tuple(q[2] for q in tri),texcoords=uvs if textured else None)

    def ring(self, center, inner, outer, color, segments=64, tilt=0):
        x, y, z = center
        ca, sa = math.cos(math.radians(tilt)), math.sin(math.radians(tilt))
        cos, sin, shade_local = math.cos, math.sin, shade
        for i in range(segments):
            aa, ab = TAU * i / segments, TAU * (i + 1) / segments
            caa, saa, cab, sab = cos(aa), sin(aa), cos(ab), sin(ab)
            yia, yib, yoa, yob = inner * saa, inner * sab, outer * saa, outer * sab
            self.quad((x + inner * caa, y + yia * ca, z + yia * sa),
                      (x + outer * caa, y + yoa * ca, z + yoa * sa),
                      (x + outer * cab, y + yob * ca, z + yob * sa),
                      (x + inner * cab, y + yib * ca, z + yib * sa),
                      shade_local(color, .93 + .07 * math.sin(i * .38)))

    def prism(self, points, bottom, top, color):
        """Extrude a counterclockwise polygon in the XY plane."""
        lower = [(x,y,bottom) for x,y in points]
        upper = [(x,y,top) for x,y in points]
        cx = sum(p[0] for p in points)/len(points)
        cy = sum(p[1] for p in points)/len(points)
        for i in range(len(points)):
            j = (i+1) % len(points)
            self.quad(lower[i], lower[j], upper[j], upper[i], shade(color,.88))
            self.tri((cx,cy,top), upper[i], upper[j], color)
            self.tri((cx,cy,bottom), lower[j], lower[i], shade(color,.7))

    def add(self, other, pos=(0,0,0), scale=1, heading=0):
        if isinstance(scale, (int, float)):
            scale = (scale,)*3
        sx, sy, sz = scale
        if min(abs(sx), abs(sy), abs(sz)) < 1e-9:
            return
        px, py, pz = pos
        if heading == 0:
            ca, sa = 1.0, 0.0
        else:
            ca, sa = math.cos(math.radians(heading)), math.sin(math.radians(heading))
        nsx, nsy, nsz = abs(sx), abs(sy), abs(sz)
        # Normal rotation/scale folded once per add; the per-vertex loop only
        # applies it. For heading-0/scale-1 this is the identity, and vertex
        # positions skip the multiply/rotate entirely (bit-identical output).
        m00, m01 = ca / nsx, -sa / nsy
        m10, m11 = sa / nsx, ca / nsy
        m22 = 1.0 / nsz
        normal = Vec3()
        if other.texcoords and not self.texcoords:
            self.texcoords.extend(((0,0),)*len(self.vertices))
        if self.texcoords or other.texcoords:
            self.texcoords.extend(other.texcoords or ((0,0),)*len(other.vertices))
        verts, norms, cols = self.vertices, self.normals, self.colors
        if sx == 1 and sy == 1 and sz == 1:
            for p, n, color in zip(other.vertices, other.normals, other.colors):
                verts.append((px + p[0], py + p[1], pz + p[2]))
                normal.set(n[0] * m00 + n[1] * m01, n[0] * m10 + n[1] * m11, n[2] * m22)
                normal.normalize()
                norms.append(tuple(normal))
                cols.append(color)
            return
        for p, n, color in zip(other.vertices, other.normals, other.colors):
            x, y, z = p[0] * sx, p[1] * sy, p[2] * sz
            verts.append((px + x * ca - y * sa, py + x * sa + y * ca, pz + z))
            normal.set(n[0] * m00 + n[1] * m01, n[0] * m10 + n[1] * m11, n[2] * m22)
            normal.normalize()
            norms.append(tuple(normal))
            cols.append(color)

    def node(self, name="mesh", parent=None, two_sided=False, unlit=False):
        steps = self._node_steps(name, parent, two_sided, unlit)
        while True:
            try:
                next(steps)
            except StopIteration as result:
                return result.value

    def _node_steps(self, name="mesh", parent=None, two_sided=False, unlit=False):
        """Fill bounded vertex batches; attach only when the geometry is complete."""
        fmt=GeomVertexFormat.getV3n3c4t2() if self.texcoords else GeomVertexFormat.getV3n3c4()
        data = GeomVertexData(name, fmt, Geom.UHStatic)
        data.setNumRows(len(self.vertices))
        vertex, normal, color = (GeomVertexWriter(data, field) for field in ("vertex", "normal", "color"))
        for index, (p, n, c) in enumerate(zip(self.vertices, self.normals, self.colors)):
            vertex.addData3f(*p)
            normal.addData3f(*n)
            color.addData4f(*c)
            if index % 256 == 255:
                yield
        if self.texcoords:
            uv=GeomVertexWriter(data,"texcoord")
            for index, coord in enumerate(self.texcoords):
                uv.addData2f(*coord)
                if index % 256 == 255:
                    yield
        triangles = GeomTriangles(Geom.UHStatic)
        if self.vertices:
            triangles.addConsecutiveVertices(0, len(self.vertices))
            triangles.closePrimitive()
        geom = Geom(data)
        geom.addPrimitive(triangles)
        node = GeomNode(name)
        node.addGeom(geom)
        path = parent.attachNewNode(node) if parent else NodePath(node)
        if two_sided:
            path.setTwoSided(True)
        if unlit:
            # Baked colours and emissive details must survive a parent material
            # or an optional auto-shader on desktop and software backends.
            path.setLightOff(10)
            path.setMaterialOff(10)
            path.setShaderOff(10)
        return path


def crystal_mesh(color=(.24,.82,.88), seed=0, size=1):
    rng = random.Random(seed)
    mesh = Mesh()
    pale = mix(color, (1, 1, 1), .62)
    mesh.sphere((0,0,.17*size), (.95*size,.75*size,.3*size), shade(color,.32), 8,4, .25, seed)
    # Flatter, darker contact shade grounds the cluster without widening it.
    mesh.sphere((0,0,.06*size), (.9*size,.7*size,.12*size), shade(color,.22), 8,3, .12, seed+11)
    for i in range(5):
        angle = TAU*i/5 + rng.random()*.45
        radius = (.45 if i else 0)*size
        x,y = math.cos(angle)*radius, math.sin(angle)*radius
        h = (1.5+rng.random()*1.5)*size*(1 if i==0 else .72)
        tip = (x+math.cos(angle)*h*.16, y+math.sin(angle)*h*.16, h)
        mid = (tip[0]*.75, tip[1]*.75, h*.74)
        mesh.tube((x,y,.14*size), mid, (.18+rng.random()*.18)*size,
                  shade(color,.7+rng.random()*.4), 5, end_radius=.22*size)
        mesh.tube(mid, tip, .22*size, shade(color,1.18), 5, end_radius=0)
        # Pale tip facet: second tone toward the extremity.
        mesh.tube(mid, tip, .1*size, shade(pale,.95), 4, end_radius=0)
        if i == 0:
            # Inner bright core nested well inside the main prism.
            core_mid = (x*.5, y*.5, h*.5)
            core_tip = (x*.55, y*.55, h*.86)
            mesh.tube((x,y,.2*size), core_mid, .09*size, shade(pale,.9), 5, end_radius=.07*size)
            mesh.tube(core_mid, core_tip, .07*size, pale, 5, end_radius=0)
    return mesh


def rock_mesh(color=(.36,.4,.44), seed=0, size=1):
    mesh = Mesh()
    rng = random.Random(seed+3)
    mesh.sphere((0,0,.58*size), (1.0*size,.8*size,.82*size), color, 8,5,.35,seed)
    mesh.sphere((.7*size,.1*size,.22*size), (.44*size,.5*size,.34*size), shade(color,.82), 7,4,.3,seed+7)
    # Flatter shaded contact base; stays inside the enclosing sphere.
    mesh.sphere((0,0,.08*size), (.92*size,.74*size,.14*size), shade(color,.55), 8,3, .12, seed+13)
    # Exposed mineral veins are embedded geometric facets.
    for i in range(3):
        x = (-.35+.32*i)*size
        mesh.tube((x,-.63*size,.3*size), (x+.17*size,-.55*size,.95*size),
                  .035*size, (1,.72,.32), 4)
    # Moss/crystal flecks: tiny two-tone quads resting on the top faces.
    moss = mix(color, (.32,.72,.35), .55)
    fleck = mix(color, (1, 1, 1), .55)
    for i in range(3):
        cx = (rng.random()-.5)*1.1*size
        cy = (rng.random()-.5)*.9*size
        cz = (1.05+rng.random()*.25)*size
        s = (.09+rng.random()*.07)*size
        col = moss if i % 2 == 0 else fleck
        mesh.quad((cx-s,cy,cz),(cx,cy+s,cz+s*.3),(cx+s,cy,cz),(cx,cy-s,cz+s*.3), col)
    return mesh


def flora_mesh(style, color, accent, seed=0, size=1):
    """Four distinct botanical silhouettes, with coloured undersides and fronds."""
    rng = random.Random(seed)
    mesh = Mesh()
    stem = mix(color, (.19,.22,.28), .57)
    if style == "mushroom":
        for index, (x,y,height,rad) in enumerate(((0,0,4.3,2.8),(1.7,.4,2.3,1.4),(-1.1,.65,1.65,1.0))):
            h = height*(.85+rng.random()*.3)
            droop = rng.uniform(-.18,.05)
            mesh.tube((x,y,0),(x+.13,y,h),.24 if not index else .14,stem,7,end_radius=.13)
            rings = [(rad*.35,h+.65), (rad*.78,h+.42), (rad,h), (rad*.86,h-.22+droop), (.22,h-.36+droop)]
            for j in range(len(rings)-1):
                r1,z1 = rings[j]
                r2,z2 = rings[j+1]
                for k in range(12):
                    a,b = TAU*k/12, TAU*(k+1)/12
                    # Second tone strengthens toward the rim extremities.
                    cc = mix(color,accent,.2 + (.7 if j>1 else 0))
                    if j >= 2:
                        cc = mix(cc, accent, .35)
                    mesh.quad((x+r1*math.cos(a),y+r1*math.sin(a),z1),
                              (x+r2*math.cos(a),y+r2*math.sin(a),z2),
                              (x+r2*math.cos(b),y+r2*math.sin(b),z2),
                              (x+r1*math.cos(b),y+r1*math.sin(b),z1), shade(cc,.85+.15*(k%3)/2))
            mesh.tube((x,y,h+.65),(x,y,h+.76),rad*.35,shade(color,1.12),12,end_radius=0)
            mesh.sphere((x,y,h+.78),(.12,.12,.07),mix(color,accent,.75),6,3)
            for k in range(6):
                a = TAU*k/6 + rng.random()*.2
                mesh.sphere((x+rad*.6*math.cos(a),y+rad*.6*math.sin(a),h+.48+droop*.5),
                            (.15,.15,.06),accent,6,3)
                # Drooping rim fleck curled under the cap edge.
                mesh.tri((x+rad*.82*math.cos(a),y+rad*.82*math.sin(a),h+.1+droop),
                         (x+rad*.82*math.cos(a+.3),y+rad*.82*math.sin(a+.3),h+.1+droop),
                         (x+rad*.7*math.cos(a+.15),y+rad*.7*math.sin(a+.15),h-.12+droop),
                         mix(accent,color,.3))
        if seed % 3 == 0:
            # Small glowing seed-pod/bloom quad cluster above the main cap.
            bx, by, bz = (0, 0, 4.3*.85+.95)
            s = .16
            mesh.quad((bx-s,by,bz),(bx,by+s,bz+s*.4),(bx+s,by,bz),(bx,by-s,bz+s*.4), mix(accent,(1,1,1),.35))
            mesh.quad((bx,by,bz-s),(bx+s*.4,by,bz),(bx,by,bz+s),(bx-s*.4,by,bz), mix(accent,(1,1,1),.35))
    elif style == "coral":
        for i in range(6):
            angle = i*TAU/6 + rng.random()*.18
            x,y = math.cos(angle),math.sin(angle)
            h = 2.4+rng.random()*2.3
            pitch = rng.uniform(-.25,.3)
            mid = (x*.7,y*.7,h*.6)
            tip = (x*1.1+pitch*.5,y*1.1,h+pitch*.4)
            mesh.tube((0,0,0),mid,.38,color,6,end_radius=.22)
            mesh.tube(mid,tip,.22,shade(color,1.12),6,end_radius=.04)
            # Pale second-tone sleeve just below each tip.
            mesh.tube((mid[0]*.6+tip[0]*.4,mid[1]*.6+tip[1]*.4,mid[2]*.6+tip[2]*.4),
                      tip,.13,mix(color,accent,.65),5,end_radius=.05)
            for side in (-1,1):
                droop_end = (x*1.7+side*.3,y*1.5,h*.9-rng.random()*.35)
                mesh.tube(mid,droop_end,.14,accent,5,end_radius=.035)
                mesh.sphere(droop_end,(.09,.09,.11),mix(accent,(1,1,1),.4),6,3)
            mesh.sphere(tip,(.25,.25,.32),accent,7,4)
            mesh.sphere(tip,(.12,.12,.16),mix(accent,(1,1,1),.45),6,3)
        if seed % 3 == 1:
            s = .2
            mesh.quad((-s,0,4.6),(0,s,4.75),(s,0,4.6),(0,-s,4.75), mix(accent,(1,1,1),.4))
    elif style == "fan":
        mesh.tube((0,0,0),(.18,0,4.2),.24,stem,7,end_radius=.08)
        for i in range(9):
            angle = i*TAU/9 + rng.random()*.08
            cos,sin = math.cos(angle),math.sin(angle)
            root = (.12,0,2.9+(i%3)*.42)
            # Varied pitch per frond: some arc high, some droop low.
            lift = (i%2)*.7 + rng.uniform(-.55,.35)
            tip = (cos*3.0,sin*3.0,3.8+lift)
            cross_sections=[]
            for j in range(7):
                t=j/6
                width=math.sin(math.pi*t)**.8*.58
                curl = math.sin(math.pi*t)*(.64+rng.uniform(-.05,.05)) - t*t*rng.uniform(0,.5)
                center=(root[0]*(1-t)+tip[0]*t,root[1]*(1-t)+tip[1]*t,
                        root[2]*(1-t)+tip[2]*t+curl)
                left=(center[0]-sin*width,center[1]+cos*width,center[2]-.17*math.sin(math.pi*t))
                right=(center[0]+sin*width,center[1]-cos*width,center[2]-.17*math.sin(math.pi*t))
                cross_sections.append((left,center,right))
            for j in range(6):
                a,b=cross_sections[j],cross_sections[j+1]
                leaf_color=mix(color,accent,max(0,(j/6-.55))*.55 + (0.3 if j>=4 else 0))
                mesh.quad(a[0],a[1],b[1],b[0],shade(leaf_color,.94))
                mesh.quad(a[1],a[2],b[2],b[1],shade(leaf_color,1.1))
                sx,sy=-sin*.022,cos*.022
                mesh.quad((a[1][0]-sx,a[1][1]-sy,a[1][2]+.012),
                          (a[1][0]+sx,a[1][1]+sy,a[1][2]+.012),
                          (b[1][0]+sx,b[1][1]+sy,b[1][2]+.012),
                          (b[1][0]-sx,b[1][1]-sy,b[1][2]+.012),accent)
                if j in (2,4):
                    for point in (b[0],b[2]):
                        mesh.tri((a[1][0]-sx,a[1][1]-sy,a[1][2]+.012),
                                 (a[1][0]+sx,a[1][1]+sy,a[1][2]+.012),
                                 (point[0],point[1],point[2]+.012),shade(accent,.8))
            # Accent tip cap on each frond extremity.
            tend = cross_sections[-1][1]
            mesh.tri((tend[0]-sin*.12,tend[1]+cos*.12,tend[2]),
                     (tend[0]+sin*.12,tend[1]-cos*.12,tend[2]),
                     (tend[0]+cos*.2,tend[1]+sin*.2,tend[2]+.1), mix(accent,(1,1,1),.3))
        mesh.sphere((.12,0,4.4),(.38,.38,.6),accent,8,5)
        if seed % 3 == 2:
            s = .18
            mesh.quad((.12-s,0,5.15),(.12, -s+0,5.3),(.12+s,0,5.15),(.12,s,5.3), mix(accent,(1,1,1),.4))
    else:  # ribbed, branched succulent
        mesh.tube((0,0,0),(0,0,3.1),.56,color,9,end_radius=.36)
        mesh.sphere((0,0,3.1),(.37,.37,.38),color,9,5)
        for i in range(3):
            angle = i*TAU/3+.3+rng.random()*.15
            x,y = math.cos(angle),math.sin(angle)
            mid = (x*1.15,y*1.15,1.55+i*.35)
            lean = rng.uniform(-.2,.2)
            mesh.tube((0,0,1+i*.25),mid,.27,shade(color,.85),7,end_radius=.22)
            mesh.tube(mid,(mid[0]+lean,mid[1],mid[2]+1),.22,mix(color,accent,.25),7,end_radius=.17)
            mesh.sphere((mid[0]+lean,mid[1],mid[2]+1.06),(.3,.3,.22),accent,7,4)
            mesh.sphere((mid[0]+lean,mid[1],mid[2]+1.12),(.14,.14,.12),mix(accent,(1,1,1),.45),6,3)
        for i in range(7):
            a = i*TAU/7
            mesh.tube((.52*math.cos(a),.52*math.sin(a),.2),
                      (.36*math.cos(a),.36*math.sin(a),2.95),.02,shade(color,1.5),3)
        mesh.sphere((0,0,3.5),(.45,.45,.23),accent,8,4)
        mesh.sphere((0,0,3.62),(.2,.2,.1),mix(accent,(1,1,1),.4),7,3)
        if seed % 3 == 0:
            s = .17
            mesh.quad((-s,0,3.95),(0,s,4.08),(s,0,3.95),(0,-s,4.08), mix(accent,(1,1,1),.4))
    if size != 1:
        result = Mesh()
        result.add(mesh,scale=size)
        return result
    return mesh


def grass_mesh(color, accent, seed=0):
    rng = random.Random(seed)
    mesh = Mesh()
    for i in range(9):
        a = rng.random()*TAU
        x,y = rng.uniform(-.9,.9),rng.uniform(-.9,.9)
        h = rng.uniform(.3,1.1)
        w = rng.uniform(.06,.18)
        lean = rng.uniform(.3,.65)
        base = mix(color,accent,rng.random()*.35)
        tip = mix(color,accent,.65+rng.random()*.35)
        mesh.tri((x-w,y,0),(x+w,y,0),(x+math.cos(a)*lean,y+math.sin(a)*lean,h),
                 color, colors=(rgba(base),rgba(base),rgba(tip)))
    return mesh


def fauna_mesh(color, accent, seed=0):
    """Three friendly alien silhouettes, all original procedural models."""
    mesh = Mesh()
    dark = shade(color,.52)
    if seed%3 == 1:
        # A long-tailed hovering filter feeder with layered manta-like fins.
        mesh.sphere((0,0,1.2),(.5,1.15,.43),color,12,7)
        mesh.sphere((0,.98,1.32),(.36,.44,.34),mix(color,accent,.2),10,6)
        glow_eye = mix(accent,(1,1,1),.35)
        for side in (-1,1):
            for i in range(4):
                y=.8-i*.45
                width=2.2-math.fabs(i-1.3)*.32
                mesh.tri((side*.28,y,1.35),(side*width,y-.35,1.5),
                         (side*(width-.35),y-.8,1.17),mix(color,accent,.32+(i%2)*.2))
                mesh.tri((side*.28,y,1.35),(side*(width-.35),y-.8,1.17),
                         (side*.26,y-.5,1.12),shade(color,.75))
                mesh.tube((side*.25,y,1.35),(side*width,y-.35,1.5),.028,accent,4,end_radius=.008)
            mesh.sphere((side*.3,1.14,1.43),(.06,.1,.09),(.1,.19,.24),8,5)
            # Glowing eye dots: tiny accent quads on the head flanks.
            ex = side*.3
            mesh.quad((ex-.05,1.2,1.5),(ex+.05,1.2,1.5),(ex+.05,1.28,1.56),(ex-.05,1.28,1.56), glow_eye)
            mesh.tube((side*.18,1.3,1.35),(side*.55,1.82,1.5),.033,accent,5,end_radius=.01)
            mesh.sphere((side*.55,1.82,1.5),(.05,.05,.06),glow_eye,6,4)
            # Layered fin ridge: one extra thin box along the wing root.
            mesh.box((side*.6,.35,1.42),(.5,.9,.06),mix(color,accent,.5))
        mesh.tube((0,-.9,1.25),(0,-2.7,1.45),.13,color,6,end_radius=.025)
        mesh.box((0,-1.7,1.5),(.08,.9,.05),mix(color,accent,.45))
        mesh.sphere((0,-2.7,1.45),(.15,.31,.11),accent,8,5)
        return mesh
    if seed%3 == 2:
        # A low, wide shellback, with interlocking mineral-like carapace plates.
        mesh.sphere((0,0,.68),(1.05,1.3,.55),dark,12,7)
        for i in range(5):
            y=-.9+i*.43
            width=1-math.fabs(i-2)*.11
            mesh.sphere((0,y,1.05),(width,.42,.55),mix(color,accent,.12+(i%2)*.23),9,5)
        # Layered shell ridges: two extra thin boxes along the spine.
        mesh.box((0,-.2,1.5),(.12,1.6,.08),mix(color,accent,.55))
        mesh.box((0,.5,1.42),(.1,.9,.06),shade(accent,.8))
        for side in (-1,1):
            for i in range(3):
                y=-.72+i*.7
                knee=(side*1.3,y-.1,.32)
                mesh.tube((side*.75,y,.78),knee,.14,color,6,end_radius=.09)
                mesh.tube(knee,(side*1.44,y+.13,.08),.09,dark,6,end_radius=.065)
            mesh.tube((side*.3,1.1,.88),(side*.48,1.67,1.25),.11,dark,6,end_radius=.075)
            mesh.sphere((side*.48,1.67,1.25),(.13,.17,.15),accent,9,5)
            mesh.sphere((side*.48,1.8,1.26),(.075,.06,.07),(.04,.1,.16),8,5)
            # Glowing eye/antenna dots: paired accent quads above the snout.
            ex = side*.48
            mesh.quad((ex-.04,1.72,1.42),(ex+.04,1.72,1.42),(ex+.04,1.78,1.48),(ex-.04,1.78,1.48),
                      mix(accent,(1,1,1),.35))
        return mesh
    mesh.sphere((0,0,1.25),(.72,1.35,.72),color,12,7)
    mesh.sphere((0,.95,1.85),(.53,.65,.55),mix(color,accent,.27),10,7)
    mesh.sphere((0,1.48,1.76),(.42,.45,.31),shade(color,1.15),10,5)
    for side in (-1,1):
        for i in range(3):
            y = -.8+i*.75
            knee = (side*.88,y-.14,.48)
            mesh.tube((side*.5,y,1.12),knee,.16,dark,6,end_radius=.11)
            mesh.tube(knee,(side*.85,y+.08,.12),.11,shade(color,.78),6,end_radius=.08)
            mesh.sphere((side*.85,y+.12,.12),(.15,.25,.12),dark,7,4)
        mesh.sphere((side*.46,1.18,1.99),(.085,.12,.13),(.06,.1,.15),8,5)
        mesh.sphere((side*.49,1.24,2.01),(.04,.06,.07),(.57,1,.95),7,4)
        mesh.tube((side*.25,.9,2.25),(side*.47,.75,2.88),.045,dark,5,end_radius=.025)
        mesh.sphere((side*.47,.75,2.88),(.12,.12,.16),accent,8,5)
        mesh.sphere((side*.47,.75,2.98),(.05,.05,.06),mix(accent,(1,1,1),.4),6,4)
        # Layered shell/fin ridge along the flank.
        mesh.box((side*.78,-.1,1.35),(.08,1.5,.07),mix(color,accent,.5))
    for i in range(5):
        x = (i-2)*.27
        mesh.tri((0,-.85,1.45),(x-.16,-2.05,1.85),(x+.16,-2.05,1.85),
                 mix(color,accent,.6+(i%2)*.3))
    for i in range(4):
        mesh.sphere((0,-.65+i*.4,1.9),(.25,.18,.17),accent,7,4)
    return mesh


def make_ship(parent):
    """The eight-metre Wayfarer: ceramic survey craft, nose along +Y."""
    root = parent.attachNewNode("wayfarer-survey-craft")
    material=Material("wayfarer-ceramic")
    # Leave ambient/diffuse unset: Panda then uses the mesh's vertex colours.
    material.setSpecular((.09,.12,.13,1))
    material.setShininess(12)
    root.setMaterial(material)
    hull, glow = Mesh(), Mesh()
    ceramic = (.77,.83,.81)
    navy = (.085,.14,.21)
    metal = (.26,.34,.4)
    amber = (.96,.52,.15)
    cyan = (.18,.88,1)
    # Sectional fuselage, with a tapered nose and chamfered flanks.
    sections = []
    for y,w,z,h in ((-3.3,.68,1.38,.5),(-2.6,1.08,1.55,.7),
                    (-.7,1.05,1.66,.85),(1.3,.8,1.53,.56),(3.95,.08,1.26,.12)):
        sections.append([(x,y,zz) for x,zz in ((-w*.62,z-h), (w*.62,z-h),
                         (w,z-h*.3),(w*.8,z+h*.62),(0,z+h),(-w*.8,z+h*.62),(-w,z-h*.3))])
    for a,b in zip(sections,sections[1:]):
        for i in range(7):
            j = (i+1)%7
            hull.quad(a[i],b[i],b[j],a[j],ceramic if i in (2,3,4) else metal)
    for ring,rev in ((sections[0],False),(sections[-1],True)):
        center = tuple(sum(p[i] for p in ring)/len(ring) for i in range(3))
        for i in range(len(ring)):
            a,b = ring[i],ring[(i+1)%len(ring)]
            hull.tri(center,b if rev else a,a if rev else b,metal)
    # Swept wings with layered nacelles and trailing control surfaces.
    for side in (-1,1):
        points = [(side*.75,-2.6),(side*3.5,-3.05),(side*3.15,-1.1),
                  (side*1.05,1.45)]
        if side < 0:
            points.reverse()
        hull.prism(points,1.15,1.35,ceramic)
        stripe = [(side*1.2,-2.48),(side*3.3,-2.88),(side*3.15,-2.38),(side*1.1,-1.95)]
        if side < 0:
            stripe.reverse()
        hull.prism(stripe,1.36,1.39,amber)
        hull.tube((side*2.28,-3.65,1.3),(side*2.28,-.68,1.3),.48,navy,10,end_radius=.35)
        hull.tube((side*2.28,-3.69,1.3),(side*2.28,-3.45,1.3),.42,metal,10)
        glow.tube((side*2.28,-3.71,1.3),(side*2.28,-3.73,1.3),.29,cyan,10)
        hull.tube((side*2.28,-.72,1.3),(side*2.28,-.42,1.3),.34,ceramic,10,end_radius=.16)
        # Upright tapered stabilisers.
        hull.tri((side*2.6,-2.8,1.5),(side*2.8,-3.05,2.65),(side*2.78,-1.15,1.45),navy)
        hull.tri((side*2.63,-2.75,1.52),(side*2.81,-2.96,2.48),(side*2.8,-1.65,1.48),amber)
        # Suspension and landing shoes keep the actual resting bottom at zero.
        hull.tube((side*1.7,-1.6,1.22),(side*1.93,-1.65,.21),.09,metal,6)
        hull.box((side*1.93,-1.52,.105),(.55,.9,.21),navy)
        glow.box((side*3.2,-2.78,1.42),(.16,.35,.06),(.9,.35,.12) if side<0 else (.3,1,.62))
    hull.tube((0,2.3,1.18),(0,2.23,.16),.08,metal,6)
    hull.box((0,2.28,.08),(.38,.68,.16),navy)
    # Flush blue cockpit glazing and clearly visible framing.
    windshield = [(-.62,.9,1.91),(.62,.9,1.91),(.56,-.3,2.43),(-.56,-.3,2.43)]
    glow.quad(*windshield,(.1,.4,.5))
    glow.quad((-.56,-.3,2.43),(.56,-.3,2.43),(.44,-1.05,2.5),(-.44,-1.05,2.5),(.18,.5,.58))
    for a,b in zip(windshield,windshield[1:]+windshield[:1]):
        hull.tube(a,b,.045,navy,5)
    hull.tube((0,.88,1.94),(0,-.32,2.47),.035,metal,5)
    hull.box((0,-2.42,2.16),(.73,.58,.1),navy)
    for i in range(5):
        hull.box((-.29+i*.145,-2.43,2.23),(.055,.42,.045),metal)
    # Warm cabin window strip along each fuselage flank.
    for side in (-1,1):
        for i in range(4):
            glow.box((side*.95,-1.5+i*.75,1.85),(.03,.4,.22),(.95,.66,.25))
        # Wingtip navigation beacon quad (saturated red; caller pulses it).
        glow.quad((side*3.5,-3.05,1.42),(side*3.15,-1.1,1.42),(side*3.15,-1.1,1.56),(side*3.5,-3.05,1.56),(1,.12,.1))
    # Tail beacon quad, clear of the hull silhouette.
    glow.quad((-.12,-3.32,1.6),(.12,-3.32,1.6),(.12,-3.32,1.78),(-.12,-3.32,1.78),(1,.12,.1))
    hull.tube((.63,-1.86,2.12),(.76,-1.92,2.9),.025,metal,5)
    glow.sphere((.76,-1.92,2.9),(.05,.05,.05),cyan,6,4)
    hull.node("wayfarer-hull",root,two_sided=True)
    glow.node("wayfarer-luminous-panels",root,two_sided=True,unlit=True)
    return root


def outpost_mesh(accent=(.15,.78,.85)):
    mesh, glow = Mesh(), Mesh()
    pale, dark, steel = (.71,.76,.73),(.09,.17,.22),(.3,.38,.4)
    mesh.tube((0,0,-.4),(0,0,.35),7.4,steel,12,end_radius=7.4)
    mesh.tube((0,0,.35),(0,0,.54),7.2,pale,12,end_radius=6.9)
    # Open-sided observation pavilion, accessible at ground level.
    for i in range(6):
        a = TAU*i/6
        x,y = 4.25*math.cos(a),4.25*math.sin(a)
        mesh.tube((x,y,.5),(x*.94,y*.94,4.6),.22,pale,6,end_radius=.15)
        glow.tube((x*1.004,y*1.004,1.8),(x*.965,y*.965,3.95),.045,accent,5)
    mesh.tube((0,0,4.4),(0,0,4.72),5.6,dark,12,end_radius=5.5)
    mesh.tube((0,0,4.72),(0,0,5.3),5.5,pale,12,end_radius=3.65)
    mesh.tube((0,0,5.3),(0,0,5.5),3.7,steel,12,end_radius=3.5)
    mesh.tube((0,0,5.5),(0,0,6.4),1.4,dark,12,end_radius=1)
    glow.tube((0,0,5.65),(0,0,6.2),1.42,shade(accent,.6),12,end_radius=1.12)
    # Central survey kiosk and storage benches.
    mesh.tube((0,0,.5),(0,0,1.8),.9,dark,8,end_radius=1.2)
    glow.tube((0,0,1.8),(0,0,1.87),1.18,accent,8)
    mesh.tube((0,0,1.87),(0,0,2.4),.09,steel,6)
    glow.sphere((0,0,2.8),(.38,.38,.38),(.42,1,.91),12,8)
    for side in (-1,1):
        mesh.box((side*3.0,.5,.9),(.65,3.0,.75),dark)
        mesh.box((side*3.0,.5,1.3),(.72,3.08,.12),pale)
        for j in range(4):
            glow.box((side*3.01,-.4+j*.58,1.37),(.4,.26,.025),accent)
        # Deck-edge greebles: low crates flush against the bench ends.
        mesh.box((side*3.0,2.35,.75),(.5,.5,.5),steel)
        mesh.box((side*3.0,-1.35,.72),(.44,.44,.44),dark)
    # Deck-edge railing: short posts plus a top rail ring at the rim.
    for i in range(12):
        a = TAU*i/12
        x, y = 6.9*math.cos(a), 6.9*math.sin(a)
        mesh.tube((x,y,.54),(x,y,1.5),.05,steel,5)
    for i in range(12):
        a, b = TAU*i/12, TAU*(i+1)/12
        mesh.tube((6.9*math.cos(a),6.9*math.sin(a),1.5),(6.9*math.cos(b),6.9*math.sin(b),1.5),.035,pale,5)
    # Warm kiosk window strip facing the deck.
    for i in range(6):
        a = TAU*i/6
        glow.box((1.05*math.cos(a),1.05*math.sin(a),1.35),(.3,.08,.3),(.95,.66,.25))
    # Blinking-beacon quad: saturated red plate atop the antenna mast.
    glow.quad((5.85,1.85,9.25),(6.15,1.85,9.25),(6.15,2.15,9.25),(5.85,2.15,9.25),(1,.12,.1))
    # Offset antenna and a concave segmented survey dish.
    mesh.tube((6,2,0),(6,2,7.8),.17,steel,8,end_radius=.1)
    for i in range(12):
        a,b = i*TAU/12,(i+1)*TAU/12
        mesh.quad((6+.5*math.cos(a),2+.5*math.sin(a),7.5),
                  (6+1.9*math.cos(a),2+1.9*math.sin(a),8.35),
                  (6+1.9*math.cos(b),2+1.9*math.sin(b),8.35),
                  (6+.5*math.cos(b),2+.5*math.sin(b),7.5),pale)
    mesh.tube((6,2,7.4),(6,2,9.1),.045,dark,6)
    glow.sphere((6,2,9.1),(.13,.13,.13),accent,8,5)
    # Photovoltaic fins with actual inlaid grid cells.
    for side in (-1,1):
        mesh.tube((side*5,-3,0),(side*8,-3,2.1),.18,steel,6)
        mesh.box((side*8,-3,2.1),(3.4,4.2,.16),dark)
        for ix in range(4):
            for iy in range(5):
                glow.box((side*8-1.25+ix*.82,-4.6+iy*.8,2.19),(.72,.68,.025),(.08,.3,.46))
    return mesh,glow


def ruin_mesh(accent=(.22,.95,.88)):
    mesh, glow = Mesh(), Mesh()
    stone = (.22,.28,.33)
    mesh.tube((0,0,-1),(0,0,.35),8.2,shade(stone,.8),10,end_radius=7.8)
    mesh.tube((0,0,.35),(0,0,.55),6.6,stone,10,end_radius=6.3)
    # A monumental broken halo.  Individual tapered slabs make the silhouette.
    for i in range(18):
        a = -math.pi*.16 + i*TAU/22
        if i in (9,10):
            continue
        b = a+TAU/24
        outer,inner = 6.5,5.45
        front = [(inner*math.cos(a),-.6,6.4+inner*math.sin(a)),
                 (outer*math.cos(a),-.6,6.4+outer*math.sin(a)),
                 (outer*math.cos(b),-.6,6.4+outer*math.sin(b)),
                 (inner*math.cos(b),-.6,6.4+inner*math.sin(b))]
        back = [(x,.6,z) for x,y,z in front]
        mesh.quad(*front,shade(stone,1+(i%3)*.08))
        mesh.quad(*reversed(back),stone)
        for j in range(4):
            k=(j+1)%4
            mesh.quad(front[j],back[j],back[k],front[k],shade(stone,.7))
        aa,bb = a+.015,b-.015
        glow.quad((5.8*math.cos(aa),-.615,6.4+5.8*math.sin(aa)),
                  (5.95*math.cos(aa),-.615,6.4+5.95*math.sin(aa)),
                  (5.95*math.cos(bb),-.615,6.4+5.95*math.sin(bb)),
                  (5.8*math.cos(bb),-.615,6.4+5.8*math.sin(bb)),accent)
    for side,h in ((-1,8.4),(1,6.8)):
        mesh.tube((side*8,1,-.6),(side*8,1,h),1.0,stone,5,end_radius=.6)
        for j in range(6):
            glow.box((side*8,.04,1.4+j*.69),(.11+.1*(j%2),.06,.34),accent)
        # Warm window slit near the pylon crown.
        glow.box((side*8,.28,h-.6),(.5,.06,.3),(.95,.66,.25))
    # Hanging moss/cables: thin tubes draped from the halo rim and pylons.
    for k in range(6):
        a = -math.pi*.16 + k*TAU/22 + .05
        hx, hz = 6.0*math.cos(a), 6.4+6.0*math.sin(a)
        mesh.tube((hx,-.55,hz),(hx+.15,-.5,hz-1.1-k*.12),.035,shade(stone,.6),4,end_radius=.015)
    mesh.tube((-8,1,7.6),(-7.7,1.2,5.9),.03,shade(stone,.55),4,end_radius=.015)
    mesh.tube((8,1,6.1),(7.8,1.2,4.7),.03,shade(stone,.55),4,end_radius=.015)
    # Beacon quad: saturated red plate above the reliquary (caller pulses it).
    glow.quad((-.3,-3.62,3.1),(.3,-3.62,3.1),(.3,-3.62,3.4),(-.3,-3.62,3.4),(1,.12,.1))
    return mesh,glow


def building_mesh(kind, accent=(.21,.9,.9)):
    mesh,glow = Mesh(),Mesh()
    dark,pale = (.12,.2,.25),(.7,.77,.75)
    if kind == "habitat":
        # An actual walk-in shell: these dimensions also define the compound
        # habitat collision walls in world._structure_shapes. The clear door
        # is 2.28 m wide and 2.32 m high above the raised floor.
        mesh.box((0,0,-.05),(6.9,6.6,.5),dark)
        mesh.box((0,0,3.16),(6.8,6.4,.32),dark)
        for x in (-3.1,3.1):
            mesh.box((x,0,1.6),(.24,6,2.8),pale)
        mesh.box((0,2.9,1.6),(6.2,.24,2.8),pale)
        for x in (-2.12,2.12):
            mesh.box((x,-2.9,1.6),(1.96,.24,2.8),pale)
        mesh.box((0,-2.9,2.76),(2.28,.24,.48),pale)
        # Flush windows, roof strips and threshold lights read from outside,
        # while the doorway and floor remain clear of decorative solids.
        for side in (-1,1):
            glow.box((side*3.226,0,1.85),(.012,3.9,.64),shade(accent,.43))
            for y in (-1.95,0,1.95):
                mesh.box((side*3.239,y,1.85),(.016,.065,.68),dark)
            glow.box((side*2.12,-3.026,1.85),(1.25,.012,.64),shade(accent,.43))
            glow.box((side*1.09,-3.026,1.32),(.055,.012,2.13),accent)
            glow.box((side*2.5,0,3.326),(.06,5.7,.012),shade(accent,.75))
        glow.box((0,-3.027,2.63),(2.17,.012,.075),accent)
        glow.box((0,-2.9,.207),(1.8,.25,.014),shade(accent,.7))
        glow.box((0,2.775,2.55),(3.5,.012,.085),shade(accent,.65))
        # Roof-edge beacon quad (saturated red; caller pulses it) and eave strip.
        glow.quad((-3.0,3.03,3.35),(-2.6,3.03,3.35),(-2.6,3.03,3.55),(-3.0,3.03,3.55),(1,.12,.1))
        glow.box((2.0,3.026,2.0),(2.4,.012,.3),(.95,.66,.25))
    elif kind == "solar":
        mesh.tube((0,0,-.2),(0,0,2.3),.2,pale,8)
        mesh.box((0,0,2.3),(6,3.8,.18),dark)
        for ix in range(7):
            for iy in range(4):
                glow.box((-2.5+ix*.83,-1.4+iy*.9,2.4),(.74,.79,.02),(.1,.35,.52))
        mesh.box((0,0,.35),(1.2,1.1,.7),pale)
        # Warm service window strip on the inverter housing; beacon on the rim.
        glow.box((0,-.16,.55),(.9,.02,.25),(.95,.66,.25))
        glow.quad((-2.9,1.9,2.45),(-2.6,1.9,2.45),(-2.6,1.9,2.65),(-2.9,1.9,2.65),(1,.12,.1))
    elif kind == "extractor":
        mesh.tube((0,0,-.1),(0,0,.4),2,dark,8,end_radius=1.7)
        mesh.tube((0,0,.4),(0,0,2.7),1.1,pale,8,end_radius=.8)
        mesh.tube((0,0,2.7),(0,0,3.2),1.3,dark,8,end_radius=1)
        for i in range(4):
            a=i*TAU/4
            mesh.tube((math.cos(a),math.sin(a),2),
                      (2.3*math.cos(a),2.3*math.sin(a),.2),.12,pale,6)
        glow.tube((0,0,1.4),(0,0,1.7),1.1,accent,8)
        # Cool window band around the drum plus a red beacon plate on top.
        glow.tube((0,0,2.15),(0,0,2.4),1.02,shade(accent,.55),8)
        glow.quad((-.2,-1.32,3.3),(.2,-1.32,3.3),(.2,-1.32,3.55),(-.2,-1.32,3.55),(1,.12,.1))
    else:
        mesh.tube((0,0,-.15),(0,0,.2),1.4,dark,6,end_radius=1.2)
        mesh.tube((0,0,.2),(0,0,4.8),.17,pale,6,end_radius=.07)
        for side in (-1,1):
            mesh.tube((0,0,3.5),(side*.9,0,4.3),.07,pale,5)
        glow.sphere((0,0,4.9),(.28,.28,.36),accent,10,6)
        glow.ring((0,0,4.55),.55,.66,accent,24)
        mesh.box((0,-.18,1.4),(.55,.3,.55),dark)
        glow.box((0,-.34,1.4),(.4,.025,.35),accent)
        # Warm slit windows on the hut face; red beacon quad above the mast.
        glow.box((0,-.345,1.62),(.34,.012,.12),(.95,.66,.25))
        glow.quad((-.15,-.1,5.15),(.15,-.1,5.15),(.15,-.1,5.35),(-.15,-.1,5.35),(1,.12,.1))
    return mesh,glow


def station_mesh():
    """A radial civilian survey station, sized for hundreds of metres of orbit."""
    mesh,glow = Mesh(),Mesh()
    pale,dark,metal,cyan = (.64,.71,.73),(.09,.15,.24),(.24,.32,.4),(.22,.84,.93)
    mesh.tube((0,0,-32),(0,0,32),19,metal,12,end_radius=19)
    mesh.tube((0,0,-12),(0,0,12),23,pale,12)
    mesh.tube((0,0,32),(0,0,50),19,pale,12,end_radius=7)
    mesh.tube((0,0,-50),(0,0,-32),7,pale,12,end_radius=19)
    for z in (-27,-20,20,27):
        glow.tube((0,0,z),(0,0,z+1.6),19.4,cyan,12)
    for i in range(8):
        a=i*TAU/8
        x,y=math.cos(a),math.sin(a)
        mesh.tube((x*18,y*18,0),(x*83,y*83,0),3.5,metal,8,end_radius=2.2)
        mesh.tube((x*83,y*83,-6),(x*83,y*83,6),10,pale,8)
        # Module windows and asymmetric communications masts.
        glow.tube((x*83,y*83,-1),(x*83,y*83,1),10.15,cyan,8)
        mesh.tube((x*83,y*83,6),(x*83,y*83,15),.45,metal,6)
        glow.sphere((x*83,y*83,15),(1,1,1),cyan,8,5)
    # Segmented habitat torus (polygonal tube around a broad orbit).
    major,minor=83,5.7
    for i in range(64):
        a,b=TAU*i/64,TAU*(i+1)/64
        for j in range(8):
            c,d=TAU*j/8,TAU*(j+1)/8
            def p(t,u):
                r=major+minor*math.cos(u)
                return (r*math.cos(t),r*math.sin(t),minor*math.sin(u))
            mesh.quad(p(a,c),p(b,c),p(b,d),p(a,d),pale if j%2 else metal)
    for z in (-5.8,5.8):
        glow.ring((0,0,z),80,81.2,cyan,96)
    # Long docking spine and its conspicuous approach lights.
    mesh.box((0,-70,-12),(18,100,5),dark)
    mesh.box((0,-116,-8),(35,30,4),metal)
    for side in (-1,1):
        for j in range(9):
            glow.box((side*7,-28-j*10,-8.9),(1.1,4,.5),(.95,.66,.25))
        mesh.box((side*45,25,25),(44,27,.8),dark)
        for ix in range(5):
            for iy in range(3):
                glow.box((side*45-17+ix*8.5,16+iy*9,25.5),(7.5,7.8,.1),(.065,.26,.4))
        mesh.tube((side*13,12,10),(side*45,25,25),1.5,metal,6)
    # Harbour beacon quads: saturated red plates at the spine tips.
    glow.quad((-2,-131.2,-5.6),(2,-131.2,-5.6),(2,-131.2,-4.6),(-2,-131.2,-4.6),(1,.12,.1))
    glow.quad((-1,50.2,7.2),(1,50.2,7.2),(1,50.2,8.4),(-1,50.2,8.4),(1,.12,.1))
    return mesh,glow
