"""Crafted procedural models for Asterion Expedition.

The art is built from curved botanical surfaces, stratified stone and chamfered
ceramic machinery.  Vertex colour carries the material and small-scale shading,
so the same original assets work with both the cinematic and fallback renderer.
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
    return tuple(color[:3]) + (color[3] if len(color) > 3 else alpha,)


def shade(color, factor):
    return tuple(max(0.0, min(1.0, c * factor)) for c in color[:3]) + (rgba(color)[3],)


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    aa, bb = rgba(a), rgba(b)
    return tuple(aa[i] * (1.0 - t) + bb[i] * t for i in range(4))


class Mesh:
    """A small batched mesh builder with per-corner normals and colours."""

    def __init__(self):
        self.vertices = []
        self.normals = []
        self.colors = []
        self.texcoords = []

    def tri(self, a, b, c, color, normals=None, colors=None, texcoords=None):
        if normals is None:
            normal = (Vec3(*b) - Vec3(*a)).cross(Vec3(*c) - Vec3(*a))
            if normal.lengthSquared() < 1e-16:
                return
            normal.normalize()
            normals = (tuple(normal),) * 3
        self.vertices.extend((tuple(a), tuple(b), tuple(c)))
        self.normals.extend(normals)
        self.colors.extend(tuple(map(rgba, colors)) if colors else (rgba(color),) * 3)
        if texcoords is not None:
            if not self.texcoords:
                self.texcoords.extend(((0, 0),) * (len(self.vertices)-3))
            self.texcoords.extend(texcoords)
        elif self.texcoords:
            self.texcoords.extend(((0, 0),) * 3)

    def quad(self, a, b, c, d, color, normals=None, colors=None):
        for indices in ((0, 1, 2), (0, 2, 3)):
            points = (a, b, c, d)
            self.tri(*(points[i] for i in indices), color,
                     normals=tuple(normals[i] for i in indices) if normals else None,
                     colors=tuple(colors[i] for i in indices) if colors else None)

    def box(self, center, size, color):
        x, y, z = center
        sx, sy, sz = (v * .5 for v in size)
        p = [(x + a*sx, y + b*sy, z + c*sz)
             for a, b, c in ((-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                              (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1))]
        for inds, tone in (((0,3,2,1),.7), ((4,5,6,7),1.08), ((0,1,5,4),.82),
                           ((1,2,6,5),.93), ((2,3,7,6),1), ((3,0,4,7),.9)):
            self.quad(*(p[i] for i in inds), shade(color, tone))

    def bevel_box(self, center, size, color, bevel=.08):
        """A solid box with true edge chamfers, retaining its exact bounds.

        Broad faces, twelve edge bands and eight corner facets catch different
        light directions; no texture or per-object shader state is needed.
        """
        half = tuple(abs(v) * .5 for v in size)
        bevel = max(0., min(bevel, min(half) * .95))
        if bevel < 1e-6:
            self.box(center, size, color)
            return

        def face(points, factor=1.):
            points = [tuple(center[k] + p[k] for k in range(3)) for p in points]
            normal = (Vec3(*points[1]) - Vec3(*points[0])).cross(
                Vec3(*points[2]) - Vec3(*points[0]))
            outward = Vec3(*(sum(p[k] for p in points) / len(points) - center[k]
                              for k in range(3)))
            if normal.dot(outward) < 0:
                points.reverse()
            for i in range(1, len(points) - 1):
                self.tri(points[0], points[i], points[i + 1], shade(color, factor))

        for axis in range(3):
            others = [k for k in range(3) if k != axis]
            for sign in (-1, 1):
                points = []
                for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    point = [0., 0., 0.]
                    point[axis] = sign * half[axis]
                    point[others[0]] = a * (half[others[0]] - bevel)
                    point[others[1]] = b * (half[others[1]] - bevel)
                    points.append(point)
                face(points, 1.03 if axis == 2 and sign > 0 else .94)
        for free in range(3):
            a, b = [k for k in range(3) if k != free]
            for sa in (-1, 1):
                for sb in (-1, 1):
                    points = []
                    for end, outer in ((-1, a), (1, a), (1, b), (-1, b)):
                        point = [0., 0., 0.]
                        point[free] = end * (half[free] - bevel)
                        point[a] = sa * (half[a] - (0 if outer == a else bevel))
                        point[b] = sb * (half[b] - (0 if outer == b else bevel))
                        points.append(point)
                    face(points, 1.10)
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    signs = (sx, sy, sz)
                    points = [tuple(signs[k] * (half[k] - (0 if axis == k else bevel))
                                    for k in range(3)) for axis in range(3)]
                    face(points, 1.07)

    def tube(self, a, b, radius, color, sides=8, end_radius=None, cap=True,
             smooth=False, end_color=None):
        """A capped tapered cylinder along an arbitrary segment."""
        a, b = Vec3(*a), Vec3(*b)
        axis = b - a
        if axis.lengthSquared() < 1e-12:
            return
        length = axis.length()
        axis.normalize()
        ref = Vec3(0, 0, 1) if abs(axis.z) < .9 else Vec3(0, 1, 0)
        u = axis.cross(ref)
        u.normalize()
        v = axis.cross(u)
        end_radius = radius if end_radius is None else end_radius
        lower, upper, normals = [], [], []
        for i in range(sides):
            radial = u * math.cos(TAU*i/sides) + v * math.sin(TAU*i/sides)
            lower.append(tuple(a + radial*radius))
            upper.append(tuple(b + radial*end_radius))
            normal = radial + axis * ((radius - end_radius) / length)
            normal.normalize()
            normals.append(tuple(normal))
        for i in range(sides):
            j = (i + 1) % sides
            col = color if smooth else shade(color, .93 + .07 * math.sin(i*1.17 + .8))
            self.quad(lower[i], lower[j], upper[j], upper[i], col,
                      normals=(normals[i], normals[j], normals[j], normals[i]) if smooth else None,
                      colors=(color, color, end_color, end_color) if end_color else None)
            if cap:
                self.tri(tuple(a), lower[j], lower[i], shade(color, .75))
                self.tri(tuple(b), upper[i], upper[j], shade(color, 1.1))

    def sphere(self, center, size, color, segments=12, rings=8, roughness=0,
               seed=0, color_fn=None, smooth=False, textured=False):
        """Ellipsoid; optional deterministic radial variation gives natural rocks."""
        rng = random.Random(seed)
        grid = []
        for j in range(rings + 1):
            lat = -math.pi*.5 + math.pi*j/rings
            row = []
            for i in range(segments + 1):
                lon = TAU*(i % segments)/segments
                n = (math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat))
                r = 1 + roughness*(rng.random() - .5)
                point = tuple(center[k] + n[k]*size[k]*r for k in range(3))
                col = color_fn(n) if color_fn else shade(color, .96 + rng.random()*.055)
                normal = Vec3(*(n[k] / max(abs(size[k]), 1e-9) for k in range(3)))
                normal.normalize()
                row.append((point, tuple(normal), col))
            row[-1] = row[0]
            grid.append(row)
        for j in range(rings):
            for i in range(segments):
                a, b, c, d = grid[j][i], grid[j][i+1], grid[j+1][i+1], grid[j+1][i]
                uv=((i/segments,j/rings),((i+1)/segments,j/rings),
                    ((i+1)/segments,(j+1)/rings),(i/segments,(j+1)/rings))
                for triangle_index, (tri,uvs) in enumerate((((a,b,c),(uv[0],uv[1],uv[2])),
                                                            ((a,c,d),(uv[0],uv[2],uv[3])))):
                    # Untextured smooth poles need a fan, not a duplicate
                    # zero-area triangle. Textured planets retain both UV seam
                    # endpoints for deterministic pole sampling.
                    if not textured and not roughness and (
                            (j == 0 and triangle_index == 0) or
                            (j == rings - 1 and triangle_index == 1)):
                        continue
                    ns = tuple(q[1] for q in tri) if smooth else None
                    self.tri(*(q[0] for q in tri), color, normals=ns,
                             colors=tuple(q[2] for q in tri),texcoords=uvs if textured else None)

    def ring(self, center, inner, outer, color, segments=64, tilt=0):
        x, y, z = center
        ca, sa = math.cos(math.radians(tilt)), math.sin(math.radians(tilt))
        for i in range(segments):
            a, b = i*TAU/segments, (i+1)*TAU/segments
            def p(radius, angle):
                yy = radius*math.sin(angle)
                return (x + radius*math.cos(angle), y + yy*ca, z + yy*sa)
            self.quad(p(inner,a), p(outer,a), p(outer,b), p(inner,b),
                      shade(color, .93 + .07*math.sin(i*.38)))

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
        ca, sa = math.cos(math.radians(heading)), math.sin(math.radians(heading))
        if other.texcoords and not self.texcoords:
            self.texcoords.extend(((0,0),)*len(self.vertices))
        if self.texcoords or other.texcoords:
            self.texcoords.extend(other.texcoords or ((0,0),)*len(other.vertices))
        for p, n, color in zip(other.vertices, other.normals, other.colors):
            x, y, z = (p[i]*scale[i] for i in range(3))
            self.vertices.append((pos[0]+x*ca-y*sa, pos[1]+x*sa+y*ca, pos[2]+z))
            nx, ny, nz = (n[i]/max(abs(scale[i]),1e-9) for i in range(3))
            normal = Vec3(nx*ca-ny*sa, nx*sa+ny*ca, nz)
            normal.normalize()
            self.normals.append(tuple(normal))
            self.colors.append(color)

    def node(self, name="mesh", parent=None, two_sided=False, unlit=False):
        fmt=GeomVertexFormat.getV3n3c4t2() if self.texcoords else GeomVertexFormat.getV3n3c4()
        data = GeomVertexData(name, fmt, Geom.UHStatic)
        data.setNumRows(len(self.vertices))
        vertex, normal, color = (GeomVertexWriter(data, field) for field in ("vertex", "normal", "color"))
        for p, n, c in zip(self.vertices, self.normals, self.colors):
            vertex.addData3f(*p)
            normal.addData3f(*n)
            color.addData4f(*c)
        if self.texcoords:
            uv=GeomVertexWriter(data,"texcoord")
            for coord in self.texcoords:
                uv.addData2f(*coord)
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


def _lathe(mesh, center, profile, color, sides=16, palette=None, smooth=True,
           squash=1.0, phase=0.0):
    """Revolve a radius/height profile, with continuous surface normals."""
    rings, normals = [], []
    for j, (radius, height) in enumerate(profile):
        before, after = profile[max(0, j - 1)], profile[min(len(profile) - 1, j + 1)]
        dr, dz = after[0] - before[0], after[1] - before[1]
        ring, ns = [], []
        for i in range(sides):
            angle = phase + i * TAU / sides
            ca, sa = math.cos(angle), math.sin(angle)
            ring.append((center[0] + radius * ca,
                         center[1] + radius * sa * squash, center[2] + height))
            normal = Vec3(dz * ca, dz * sa / squash, -dr)
            if normal.lengthSquared() < 1e-12:
                normal = Vec3(ca, sa, 0)
            normal.normalize()
            ns.append(tuple(normal))
        rings.append(ring)
        normals.append(ns)
    for j in range(len(profile) - 1):
        low = palette[j] if palette else color
        high = palette[j + 1] if palette else color
        for i in range(sides):
            k = (i + 1) % sides
            if profile[j][0] == 0:
                mesh.tri(rings[j][i], rings[j + 1][k], rings[j + 1][i], color,
                         normals=(normals[j][i], normals[j + 1][k], normals[j + 1][i]) if smooth else None,
                         colors=(low, high, high))
            elif profile[j + 1][0] == 0:
                mesh.tri(rings[j][i], rings[j][k], rings[j + 1][k], color,
                         normals=(normals[j][i], normals[j][k], normals[j + 1][k]) if smooth else None,
                         colors=(low, low, high))
            else:
                mesh.quad(rings[j][i], rings[j][k], rings[j + 1][k], rings[j + 1][i], color,
                          normals=(normals[j][i], normals[j][k], normals[j + 1][k], normals[j + 1][i])
                          if smooth else None,
                          colors=(low, low, high, high))


def _leaf(mesh, root, tip, width, color, accent, arch=.35, sections=7, vein=True):
    """A gently folded, curving blade with smooth normals and a fine midrib."""
    root, tip = Vec3(*root), Vec3(*tip)
    forward = Vec3(tip.x - root.x, tip.y - root.y, 0)
    if forward.lengthSquared() < 1e-8:
        forward = Vec3(1, 0, 0)
    forward.normalize()
    sideways = Vec3(-forward.y, forward.x, 0)
    rows, cols = [], []
    for j in range(sections + 1):
        t = j / sections
        # The asymmetric envelope broadens early and resolves to a fine point.
        envelope = max(.006, math.sin(math.pi * t) ** .75 * (1 - .20 * t))
        center = root * (1 - t) + tip * t + Vec3(0, 0, math.sin(math.pi * t) * arch)
        row = []
        for side in (-1, 0, 1):
            point = center + sideways * (side * width * envelope)
            point.z -= abs(side) * width * envelope * .22
            row.append(tuple(point))
        rows.append(row)
        leaf_color = mix(shade(color, .61 + .49 * t), accent, max(0., t - .64) * .66)
        cols.append((shade(leaf_color, .92), shade(leaf_color, 1.05), leaf_color))
    normals = []
    for j, row in enumerate(rows):
        ns = []
        for k in range(3):
            across = Vec3(*row[min(2, k + 1)]) - Vec3(*row[max(0, k - 1)])
            along = Vec3(*rows[min(sections, j + 1)][k]) - Vec3(*rows[max(0, j - 1)][k])
            normal = along.cross(across)
            if normal.lengthSquared() < 1e-12:
                normal = Vec3(0, 0, 1)
            if normal.z < 0:
                normal = -normal
            normal.normalize()
            ns.append(tuple(normal))
        normals.append(ns)
    for j in range(sections):
        for k in range(2):
            mesh.quad(rows[j][k + 1], rows[j][k], rows[j + 1][k], rows[j + 1][k + 1], color,
                      normals=(normals[j][k + 1], normals[j][k],
                               normals[j + 1][k], normals[j + 1][k + 1]),
                      colors=(cols[j][k + 1], cols[j][k], cols[j + 1][k], cols[j + 1][k + 1]))
        if vein and 0 < j < sections - 1:
            a, b = Vec3(*rows[j][1]), Vec3(*rows[j + 1][1])
            a.z += .006
            b.z += .006
            rib = sideways * (width * .025 * (1 - j / sections))
            mesh.quad(tuple(a + rib), tuple(a - rib), tuple(b - rib * .8), tuple(b + rib * .8),
                      mix(color, accent, .40))


def _bloom(mesh, center, color, size=.25, petals=6):
    """An open star blossom; geometry stays legible at resource-picking range."""
    for i in range(petals):
        a = i * TAU / petals
        tip = (center[0] + math.cos(a) * size, center[1] + math.sin(a) * size,
               center[2] + size * .14)
        _leaf(mesh, center, tip, size * .24, color, mix(color, (.98, .9, .67), .3),
              arch=size * .28, sections=2, vein=False)
    mesh.sphere((center[0], center[1], center[2] + .055 * size),
                (size * .16, size * .16, size * .12), mix(color, (1, .88, .47), .6),
                6, 3, smooth=True)


def crystal_mesh(color=(.24, .82, .88), seed=0, size=1):
    """A mineral rosette with clear planar cuts and luminous growth seams."""
    rng = random.Random(seed)
    mesh = Mesh()
    matrix = mix(shade(color, .29), (.18, .22, .25), .48)
    mesh.sphere((0, 0, .13), (.92, .73, .25), matrix, 10, 4, .18, seed)
    for i in range(7):
        angle = i * 2.39996 + rng.uniform(-.18, .18)
        offset = 0 if i == 0 else rng.uniform(.3, .67)
        x, y = math.cos(angle) * offset, math.sin(angle) * offset
        h = rng.uniform(2.1, 2.85) if i == 0 else rng.uniform(.74, 1.9)
        radius = rng.uniform(.18, .29) * (1.12 if i == 0 else 1.)
        lean = .09 if i == 0 else .19
        axis = Vec3(math.cos(angle) * lean, math.sin(angle) * lean, 1)
        axis.normalize()
        u = Vec3(-math.sin(angle), math.cos(angle), 0)
        v = axis.cross(u)
        levels = ((0, radius * .84), (.12, radius), (h * .72, radius * .88),
                  (h * .84, radius * .68), (h, .014))
        rings = []
        for z, r in levels:
            center = Vec3(x, y, .09) + axis * z
            rings.append([tuple(center + (u * math.cos(k * TAU / 6) +
                                           v * math.sin(k * TAU / 6)) * r)
                          for k in range(6)])
        tones = (.39, .76, .94, 1.12, 1.24)
        for j in range(len(rings) - 1):
            for k in range(6):
                kk = (k + 1) % 6
                facet = .74 + .22 * (k % 3) / 2
                low = mix(shade(color, tones[j] * facet), (.10, .24, .3), .08)
                high = mix(shade(color, tones[j + 1] * facet), (.85, 1, .97), .07 * j)
                mesh.quad(rings[j][k], rings[j][kk], rings[j + 1][kk], rings[j + 1][k], color,
                          colors=(low, low, high, high))
        # Fine growth lines and polished edge glints, kept on the crystal faces.
        for k in (0, 2, 4):
            edge = Vec3(*rings[1][k])
            end = Vec3(*rings[3][k])
            mesh.tube(tuple(edge), tuple(end), .009, mix(color, (.9, 1, 1), .43),
                      3, end_radius=.003, cap=False)
        for line in (.28, .51):
            center = Vec3(x, y, .09) + axis * (h * line)
            for k in range(6):
                a, b = k * TAU / 6, (k + 1) * TAU / 6
                rad = radius * (1 - .12 * (h * line - .12) / max(h * .72 - .12, .1))
                p = center + (u * math.cos(a) + v * math.sin(a)) * (rad + .002)
                q = center + (u * math.cos(b) + v * math.sin(b)) * (rad + .002)
                mesh.quad(tuple(p), tuple(q), tuple(q + axis * .014), tuple(p + axis * .014),
                          mix(shade(color, .78), (.7, .94, .96), .16))
    if size != 1:
        result = Mesh()
        result.add(mesh, scale=size)
        return result
    return mesh


def rock_mesh(color=(.36, .4, .44), seed=0, size=1):
    """Weathered outcrop: broad cleaved faces, dark bedding and mineral flecks."""
    rng = random.Random(seed)
    mesh = Mesh()

    def mass(center, radii, salt):
        local = random.Random(seed + salt)
        sides = 10
        phase = local.uniform(0, TAU)
        angles = [phase + i * TAU / sides + local.uniform(-.1, .1) for i in range(sides)]
        facets = [local.uniform(.83, 1.08) for _ in range(sides)]
        profiles = ((-.75, .61), (-.37, .97), (.15, 1.0), (.62, .72), (.83, .34))
        rings = []
        for j, (z, radius) in enumerate(profiles):
            ring = []
            for i, angle in enumerate(angles):
                rr = radius * facets[i] * local.uniform(.94, 1.05)
                ring.append((center[0] + math.cos(angle) * rr * radii[0] + z * .12,
                             center[1] + math.sin(angle) * rr * radii[1],
                             center[2] + (z + local.uniform(-.055, .055)) * radii[2]))
            rings.append(ring)
        base = mix(color, (.28, .25, .22), .12)
        for j in range(len(rings) - 1):
            for i in range(sides):
                k = (i + 1) % sides
                tone = (.66, .79, .95, 1.12)[j] * (.89 + facets[i] * .12)
                lower = shade(base, tone)
                upper = mix(shade(base, tone + .055), (.62, .63, .55), .06 if j > 1 else 0)
                mesh.quad(rings[j][i], rings[j][k], rings[j + 1][k], rings[j + 1][i], base,
                          colors=(lower, lower, upper, upper))
        top = tuple(sum(p[k] for p in rings[-1]) / sides for k in range(3))
        for i in range(sides):
            mesh.tri(top, rings[-1][i], rings[-1][(i + 1) % sides], shade(base, 1.12))
        # Broken thin strata follow a real exposed face, instead of floating bars.
        for i in (1, 4, 7):
            a, b = Vec3(*rings[1][i]), Vec3(*rings[1][(i + 1) % sides])
            c, d = Vec3(*rings[2][(i + 1) % sides]), Vec3(*rings[2][i])
            offset = (b - a).cross(d - a)
            offset.normalize()
            offset *= .003
            p, q = a * .48 + d * .52 + offset, b * .48 + c * .52 + offset
            mesh.quad(tuple(p), tuple(q), tuple(q + (c - b) * .04), tuple(p + (d - a) * .04),
                      mix(base, (.67, .65, .5), .42))
    mass((0, 0, .6), (1.02, .79, .86), 0)
    mass((.71, .13, .22), (.44, .49, .34), 19)
    for i in range(4):
        x, y = rng.uniform(-.8, .7), rng.uniform(-.73, .55)
        mesh.sphere((x, y, .06), (.08, .065, .06), shade(color, .72), 5, 3, .16, seed + i)
    if size != 1:
        result = Mesh()
        result.add(mesh, scale=size)
        return result
    return mesh


def flora_mesh(style, color, accent, seed=0, size=1):
    """Four living silhouettes, with arched foliage and authored colour flow."""
    rng = random.Random(seed)
    mesh = Mesh()
    stem = mix(color, (.32, .26, .23), .66)
    leaf = mix(color, (.29, .54, .42), .15)
    if style == "mushroom":
        # Consume the same first three height draws as the collision generator.
        heights = [height * (.85 + rng.random() * .3) for height in (4.3, 2.3, 1.65)]
        for index, (x, y, rad) in enumerate(((0, 0, 2.8), (1.7, .4, 1.4), (-1.1, .65, 1.0))):
            h = heights[index]
            stalk = .24 if not index else .14
            _lathe(mesh, (x, y, 0), ((stalk * 1.35, 0), (stalk, .22),
                   (stalk * .68, h * .62), (stalk * .78, h - .35), (stalk * 1.5, h - .19)),
                   stem, 10, palette=[shade(stem, .62), stem, mix(stem, accent, .11),
                                     mix(stem, accent, .23), mix(stem, accent, .35)])
            cap = mix(color, accent, .22)
            underside = mix(shade(color, .70), accent, .54)
            profile = ((0, h - .36), (rad * .26, h - .36), (rad * .81, h - .21),
                       (rad, h), (rad * .96, h + .13), (rad * .76, h + .44),
                       (rad * .4, h + .66), (0, h + .75))
            _lathe(mesh, (x, y, 0), profile, cap, 20,
                   palette=[shade(underside, .58), shade(underside, .83), underside,
                            mix(cap, accent, .33), shade(cap, 1.05), cap,
                            mix(cap, (.89, .81, .62), .17), mix(cap, (.97, .9, .75), .25)])
            for k in range(16):
                angle = k * TAU / 16
                ca, sa = math.cos(angle), math.sin(angle)
                mesh.tube((x + ca * rad * .26, y + sa * rad * .26, h - .375),
                          (x + ca * rad * .89, y + sa * rad * .89, h - .135),
                          .012 if not index else .008, mix(underside, (.93, .91, .75), .32),
                          3, end_radius=.005, cap=False)
            # Constellations of small pores sit flush against the curved crown.
            for k in range(11 if not index else 6):
                angle = rng.random() * TAU
                fraction = rng.uniform(.24, .72)
                height = h + .75 - fraction * fraction * .71
                rr = rad * fraction
                pore = rng.uniform(.035, .083) * (1 if index == 0 else .7)
                mesh.sphere((x + math.cos(angle) * rr, y + math.sin(angle) * rr, height),
                            (pore, pore, .026), mix(accent, (.98, .92, .79), .52), 6, 3, smooth=True)
            for k in range(3):
                a = k * TAU / 3 + index
                mesh.tube((x, y, .26), (x + math.cos(a) * stalk * 2.2,
                          y + math.sin(a) * stalk * 2.2, .015), stalk * .3,
                          shade(stem, .77), 5, end_radius=.03, smooth=True)
    elif style == "coral":
        _lathe(mesh, (0, 0, 0), ((.43, 0), (.36, .38), (.30, 1.1), (.21, 2.2)),
               color, 10, palette=[shade(color, .5), shade(color, .74), color, mix(color, accent, .25)])
        for i in range(6):
            angle = i * TAU / 6 + rng.uniform(-.16, .16)
            ca, sa = math.cos(angle), math.sin(angle)
            h = 2.4 + rng.random() * 2.3
            points = ((0, 0, .18), (ca * .41, sa * .41, h * .35),
                      (ca * .85, sa * .85, h * .72), (ca * 1.04, sa * 1.04, h))
            for j in range(3):
                mesh.tube(points[j], points[j + 1], (.29, .20, .10)[j],
                          mix(shade(color, .73 + j * .13), accent, j * .13), 8,
                          end_radius=(.20, .10, .022)[j], smooth=True,
                          end_color=mix(color, accent, .18 + j * .22))
            for branch in range(3):
                z = h * (.49 + branch * .13)
                side = -1 if branch % 2 else 1
                root = (ca * (.56 + branch * .15), sa * (.56 + branch * .15), z)
                end = (ca * (1.17 + branch * .13) - sa * side * .45,
                       sa * (1.17 + branch * .13) + ca * side * .45, z + .56)
                mesh.tube(root, end, .09 - branch * .014, mix(color, accent, .36),
                          7, end_radius=.016, smooth=True, end_color=mix(color, accent, .76))
                mesh.sphere(end, (.071, .071, .095), mix(accent, color, .14),
                            6, 4, smooth=True)
            _bloom(mesh, points[-1], accent, .22, 6)
            for j in range(4):
                z = h * (.4 + j * .13)
                mesh.sphere((ca * (.59 + j * .13), sa * (.59 + j * .13), z),
                            (.046, .046, .07), mix(color, accent, .65), 5, 3, smooth=True)
    elif style == "fan":
        trunk = ((0, 0, 0), (.045, .012, 1.35), (.11, .016, 2.8), (.18, 0, 4.2))
        for j in range(3):
            mesh.tube(trunk[j], trunk[j + 1], (.24, .20, .15)[j],
                      shade(stem, .74 + j * .11), 10, end_radius=(.20, .15, .085)[j], smooth=True)
        for j in range(10):
            t = j / 10
            z = .35 + t * 3.2
            r = .24 - z * .035
            _lathe(mesh, (z * .042, 0, z), ((r + .007, -.025), (r + .025, 0), (r + .005, .04)),
                   mix(stem, (.52, .51, .39), .2), 10)
        for i in range(4):
            a = i * TAU / 4 + .35
            mesh.tube((.01, 0, .48), (math.cos(a) * .51, math.sin(a) * .51, .025),
                      .11, shade(stem, .76), 6, end_radius=.035, smooth=True)
        phase = rng.uniform(0, TAU)
        variant = seed % 3
        if variant == 0:
            # A spreading crown with spoon-shaped leaf clusters at branch ends.
            for i in range(7):
                angle = phase + i * 2.39996
                ca, sa = math.cos(angle), math.sin(angle)
                z = 3.08 + (i % 3) * .32
                joint = (ca * 1.15, sa * 1.15, z + .33)
                mesh.tube((.13, 0, z), joint, .067, stem, 7, end_radius=.022, smooth=True)
                for j in range(3):
                    a = angle + (j - 1) * .58
                    length = 1.12 + rng.random() * .32
                    tip = (joint[0] + math.cos(a) * length,
                           joint[1] + math.sin(a) * length, z + .06 + .23 * (j % 2))
                    _leaf(mesh, joint, tip, .40 + .06 * (j % 2),
                          mix(leaf, (.50, .58, .29), .10 + i % 3 * .04),
                          mix(accent, leaf, .60), arch=.38, sections=5)
        elif variant == 2:
            # Fine pinnate fronds give a fern-like silhouette between broad crowns.
            for i in range(8):
                angle = phase + i * 2.39996
                ca, sa = math.cos(angle), math.sin(angle)
                length = 2.38 + rng.random() * .48
                root = (.14, 0, 3.12 + (i % 3) * .25)
                tip = (ca * length, sa * length, root[2] - .48)
                _leaf(mesh, root, tip, .076, leaf, mix(accent, leaf, .6), arch=.95,
                      sections=7, vein=True)
                for j in range(5):
                    t = .26 + j * .13
                    center = (root[0] * (1 - t) + tip[0] * t,
                              root[1] * (1 - t) + tip[1] * t,
                              root[2] * (1 - t) + tip[2] * t + math.sin(math.pi * t) * .95)
                    span = .57 * math.sin(math.pi * t) ** .55
                    for side in (-1, 1):
                        end = (center[0] + ca * .20 - sa * span * side,
                               center[1] + sa * .20 + ca * span * side, center[2] - .22)
                        _leaf(mesh, center, end, .105,
                              mix(leaf, (.24, .48, .38), .16), mix(accent, leaf, .55),
                              arch=.09, sections=3, vein=False)
        else:
            for i in range(12):
                angle = phase + i * 2.39996
                length = 2.25 + rng.random() * .83
                tier = i % 3
                root = (.14, 0, 3.04 + tier * .42)
                tip = (math.cos(angle) * length, math.sin(angle) * length,
                       2.39 + tier * .49 + rng.uniform(-.16, .35))
                tint = mix(leaf, accent, .06 + (i % 4) * .025)
                _leaf(mesh, root, tip, .42 + rng.random() * .13, tint,
                      mix(accent, color, .40), arch=.92 + tier * .14, sections=8)
        # Upright young leaves interrupt the repeated umbrella outline.
        for i in range(3):
            a = phase + i * TAU / 3
            _leaf(mesh, (.17, 0, 3.77), (.65 * math.cos(a), .65 * math.sin(a), 4.79 - i * .13),
                  .16, mix(color, accent, .25), mix(accent, color, .30), arch=.19, sections=5)
        if seed % 3 != 1:
            _bloom(mesh, (.16, 0, 4.26), mix(accent, (.95, .79, .58), .18), .23, 6)
    else:
        # Sculptural ribbed succulents with waxy shoulders and small flower crowns.
        profile = ((.55, 0), (.56, .4), (.51, 1.35), (.46, 2.55), (.35, 3.08), (.12, 3.34), (0, 3.38))
        _lathe(mesh, (0, 0, 0), profile, color, 14,
               palette=[shade(color, .53), shade(color, .81), color, shade(color, 1.04),
                        mix(color, accent, .16), mix(color, accent, .31), accent])
        for i in range(8):
            a = i * TAU / 8
            ca, sa = math.cos(a), math.sin(a)
            points = [(ca * r * 1.016, sa * r * 1.016, z) for r, z in profile[1:-1]]
            for a1, b1 in zip(points, points[1:]):
                mesh.tube(a1, b1, .016, mix(color, (.83, .85, .54), .22), 3,
                          end_radius=.009, cap=False)
        for i in range(3):
            angle = i * TAU / 3 + .3
            ca, sa = math.cos(angle), math.sin(angle)
            root = (ca * .3, sa * .3, 1.02 + i * .24)
            elbow = (ca * 1.13, sa * 1.13, 1.55 + i * .35)
            end = (ca * 1.15, sa * 1.15, 2.51 + i * .35)
            mesh.tube(root, elbow, .26, shade(color, .87), 10, end_radius=.22, smooth=True)
            mesh.sphere(elbow, (.235, .235, .24), color, 10, 5, smooth=True)
            mesh.tube(elbow, end, .22, color, 10, end_radius=.135, smooth=True,
                      end_color=mix(color, accent, .27))
            mesh.sphere(end, (.137, .137, .14), mix(color, accent, .27), 10, 5, smooth=True)
            _bloom(mesh, (end[0], end[1], end[2] + .11), accent, .26, 7)
        _bloom(mesh, (0, 0, 3.37), accent, .40, 8)
    # The lit foliage shader reads this otherwise unused coordinate as a
    # flexibility mask.  Rigid trunks and attachment points stay anchored.
    if not mesh.texcoords:
        for x, y, z in mesh.vertices:
            if style == "fan":
                flexibility = min(1., max(0., (math.hypot(x - .14, y) - .25) / 1.9))
                flexibility *= min(1., max(0., (z - 1.6) / 1.5))
            elif style == "mushroom":
                flexibility = 0.
                for (sx, sy, radius), h in zip(((0, 0, 2.8), (1.7, .4, 1.4),
                                               (-1.1, .65, 1.0)), heights):
                    if h - .42 < z < h + .78:
                        flexibility = max(flexibility, min(.55,
                            max(0., math.hypot(x - sx, y - sy) / radius - .19) * .62))
            elif style == "coral":
                flexibility = min(.66, max(0., (z - 2.1) / 3.0))
            else:
                flexibility = .30 if z > 3.37 or (math.hypot(x, y) > .95 and z > 2.65) else 0.
            mesh.texcoords.append((flexibility, 0.))
    if size != 1:
        result = Mesh()
        result.add(mesh, scale=size)
        return result
    return mesh


def grass_mesh(color, accent, seed=0):
    """An airy tuft of curved blades, with muted roots and a few seed heads."""
    rng = random.Random(seed)
    mesh = Mesh()
    for i in range(13):
        a = rng.random() * TAU
        radius = rng.uniform(.04, .49)
        x, y = math.cos(a) * radius, math.sin(a) * radius
        h = rng.uniform(.34, .94)
        bend = rng.uniform(.17, .49)
        _leaf(mesh, (x, y, .005), (x + math.cos(a) * bend, y + math.sin(a) * bend, h),
              rng.uniform(.027, .067), mix(color, accent, rng.uniform(.02, .18)),
              mix(accent, color, .47), arch=.08, sections=4, vein=False)
    for i in range(2):
        a = rng.random() * TAU
        x, y = math.cos(a) * .19, math.sin(a) * .19
        h = rng.uniform(.87, 1.13)
        mesh.tube((x, y, 0), (x + .06, y, h), .01, mix(color, accent, .24),
                  3, end_radius=.004, cap=False)
        mesh.sphere((x + .06, y, h), (.024, .024, .085), mix(accent, color, .31), 5, 3, smooth=True)
    mesh.texcoords = [(min(1., max(0., p[2] / 1.05)) ** 1.5, 0.) for p in mesh.vertices]
    return mesh


def fauna_mesh(color, accent, seed=0):
    """Three expressive creatures with soft skin, glossy eyes and patterned fins."""
    mesh = Mesh()
    dark = mix(shade(color, .54), (.19, .22, .24), .23)
    belly = mix(color, (.82, .77, .62), .54)
    eye = (.028, .06, .085)

    def ellipsoid(center, size, tint, segments=14, rings=8):
        mesh.sphere(center, size, tint, segments, rings, smooth=True,
                    color_fn=lambda n: mix(shade(tint, .94 + .07 * n[2]), belly,
                                           max(0, -n[2]) * .32))

    def eyes(x, y, z, sx=.07, sy=.105, sz=.09):
        for side in (-1, 1):
            mesh.sphere((side * x, y, z), (sx * 1.25, sy * 1.06, sz * 1.22), dark, 10, 6, smooth=True)
            mesh.sphere((side * (x + sx * .23), y + sy * .28, z), (sx, sy, sz), eye, 12, 7, smooth=True)
            mesh.sphere((side * (x + sx * .66), y + sy * .78, z + sz * .36),
                        (sx * .28, sy * .24, sz * .24), (.91, 1, .94), 7, 4, smooth=True)

    if seed % 3 == 1:
        # A broad, single scalloped membrane replaces disconnected flat triangles.
        ellipsoid((0, -.04, 1.24), (.48, 1.20, .36), color)
        ellipsoid((0, 1.00, 1.30), (.35, .44, .28), mix(color, accent, .10))
        for side in (-1, 1):
            rows = []
            for j in range(9):
                t = j / 8
                y = 1.04 - t * 2.25
                width = .25 + math.sin(math.pi * t) ** .64 * 1.86
                rows.append([(side * (.27 + (width - .27) * k / 4),
                              y - .22 * math.sin(k * math.pi / 4),
                              1.22 + math.sin(k * math.pi / 4) * .28 - t * .08)
                             for k in range(5)])
            for j in range(8):
                for k in range(4):
                    cc = mix(color, accent, .17 + (k / 4) ** 2 * .51)
                    normal = (0, 0, 1)
                    corners = (rows[j][k], rows[j + 1][k], rows[j + 1][k + 1], rows[j][k + 1])
                    if side < 0:
                        corners = tuple(reversed(corners))
                    mesh.quad(*corners, cc,
                              normals=(normal,) * 4,
                              colors=(cc, cc, shade(cc, 1.04), shade(cc, 1.04)))
            for j in (2, 4, 6):
                for k in range(4):
                    mesh.tube(rows[j][k], rows[j][k + 1], .012, mix(color, accent, .56),
                              3, end_radius=.007, cap=False)
            for i in range(4):
                mesh.sphere((side * (.62 + i * .26), -.17 - i * .1, 1.49),
                            (.065, .11, .015), mix(accent, belly, .16), 7, 4, smooth=True)
            mesh.tube((side * .17, 1.30, 1.32), (side * .39, 1.69, 1.44), .023,
                      dark, 6, end_radius=.006, smooth=True)
        eyes(.29, 1.19, 1.39, .057, .092, .075)
        tail = ((0, -.95, 1.22), (0, -1.8, 1.23), (.18, -2.68, 1.47))
        for i in range(2):
            mesh.tube(tail[i], tail[i + 1], (.12, .061)[i], color, 8,
                      end_radius=(.061, .015)[i], smooth=True)
        for side in (-1, 1):
            _leaf(mesh, tail[-1], (side * .37 + .18, -2.66, 1.62), .12, accent, belly,
                  arch=.03, sections=4)
        return mesh
    if seed % 3 == 2:
        ellipsoid((0, 0, .68), (1.04, 1.27, .51), dark)
        # Five overlapping shell plates with inset sutures and a central ridge.
        for i in range(5):
            y = -.89 + i * .43
            width = 1 - abs(i - 2) * .105
            ellipsoid((0, y, 1.005), (width, .36, .51),
                      mix(color, accent, .12 + (i % 2) * .12), 12, 7)
            for side in (-1, 1):
                for j in range(3):
                    t = (j + 1) / 4
                    x = side * width * t
                    z = 1.01 + .52 * math.sqrt(1 - t * t)
                    mesh.sphere((x, y + .035, z), (.039, .092, .014), mix(accent, belly, .17),
                                6, 3, smooth=True)
            mesh.tube((0, y - .17, 1.525), (0, y + .19, 1.525), .025,
                      mix(color, accent, .6), 5, end_radius=.008, smooth=True)
        for side in (-1, 1):
            for i in range(3):
                y = -.72 + i * .70
                knee = (side * 1.27, y - .11, .33)
                mesh.tube((side * .73, y, .76), knee, .13, color, 8, end_radius=.095, smooth=True)
                ellipsoid(knee, (.115, .13, .12), mix(color, accent, .20), 9, 5)
                mesh.tube(knee, (side * 1.44, y + .13, .075), .087, dark,
                          8, end_radius=.047, smooth=True)
                ellipsoid((side * 1.43, y + .12, .074), (.10, .14, .075), dark, 8, 5)
            mesh.tube((side * .29, 1.09, .86), (side * .45, 1.62, 1.23), .099,
                      color, 8, end_radius=.067, smooth=True)
            ellipsoid((side * .45, 1.62, 1.23), (.12, .16, .13), mix(color, accent, .30), 10, 6)
        eyes(.46, 1.72, 1.25, .058, .078, .064)
        ellipsoid((0, 1.2, .71), (.42, .35, .21), belly, 12, 6)
        return mesh
    ellipsoid((0, 0, 1.25), (.72, 1.33, .69), color, 18, 10)
    ellipsoid((0, .99, 1.85), (.52, .64, .53), mix(color, accent, .17), 16, 9)
    ellipsoid((0, 1.49, 1.77), (.41, .40, .28), belly, 14, 8)
    for side in (-1, 1):
        for i in range(3):
            y = -.79 + i * .74
            knee = (side * .86, y - .13, .48)
            mesh.tube((side * .5, y, 1.10), knee, .151, dark, 9, end_radius=.108, smooth=True)
            ellipsoid(knee, (.117, .145, .13), mix(color, accent, .16), 9, 5)
            mesh.tube(knee, (side * .85, y + .08, .13), .105, color,
                      9, end_radius=.073, smooth=True)
            ellipsoid((side * .85, y + .13, .12), (.15, .24, .12), dark, 9, 5)
            for digit in (-1, 1):
                ellipsoid((side * .85 + digit * .045, y + .30, .085), (.037, .078, .042),
                          belly, 7, 4)
        for j in range(6):
            y = -.88 + j * .27
            mesh.sphere((side * .624, y, 1.46 + .07 * math.sin(j)), (.035, .069, .052),
                        mix(accent, belly, .26), 7, 4, smooth=True)
        mesh.tube((side * .24, .92, 2.24), (side * .42, .76, 2.72), .04,
                  dark, 7, end_radius=.021, smooth=True)
        ellipsoid((side * .42, .76, 2.78), (.10, .105, .15), accent, 10, 6)
        _leaf(mesh, (side * .37, .77, 2.19), (side * .89, .60, 2.30), .15,
              color, accent, arch=.15, sections=5)
        mesh.sphere((side * .15, 1.838, 1.84), (.027, .019, .021), dark, 7, 4, smooth=True)
    eyes(.455, 1.20, 2.015, .078, .10, .108)
    mesh.tube((0, -.89, 1.48), (0, -1.63, 1.58), .18, color, 9, end_radius=.072, smooth=True)
    for i in range(5):
        angle = -.9 + i * .45
        _leaf(mesh, (0, -1.47, 1.61), (math.sin(angle) * .74, -2.09 - math.cos(angle) * .10,
              1.80 + .13 * math.cos(angle)), .135, mix(color, accent, .40), accent,
              arch=.10, sections=5)
    for i in range(4):
        _leaf(mesh, (0, -.74 + i * .40, 1.89), (0, -.81 + i * .40, 2.16 - i * .03),
              .14, accent, belly, arch=.03, sections=4, vein=False)
    return mesh


def make_ship(parent):
    """Wayfarer: a chamfered ceramic survey skiff, nose along +Y."""
    root = parent.attachNewNode("wayfarer-survey-craft")
    material = Material("wayfarer-ceramic")
    material.setSpecular((.30, .34, .35, 1))
    material.setShininess(48)
    root.setMaterial(material)
    root.setShaderInput("ae_material", .42, .35, 0., 0.)
    hull, metalwork, glass, glow = Mesh(), Mesh(), Mesh(), Mesh()
    ceramic, shadow = (.79, .84, .82), (.52, .61, .63)
    navy, metal = (.055, .105, .155), (.23, .31, .35)
    amber, cyan = (.97, .48, .15), (.32, .91, 1.)
    # An uninterrupted faceted hull with narrow chines and a long tapered nose.
    sections = []
    for y, w, z, h in ((-3.35, .62, 1.38, .47), (-2.75, .99, 1.52, .63),
                        (-1.8, 1.08, 1.64, .73), (-.55, 1.02, 1.67, .78),
                        (.9, .83, 1.53, .62), (2.35, .51, 1.38, .35), (4.02, .05, 1.23, .08)):
        sections.append([(x, y, zz) for x, zz in ((-w * .65, z - h), (w * .65, z - h),
                            (w, z - h * .31), (w * .91, z + h * .34),
                            (w * .57, z + h * .83), (0, z + h),
                            (-w * .57, z + h * .83), (-w * .91, z + h * .34), (-w, z - h * .31))])
    for j, (a, b) in enumerate(zip(sections, sections[1:])):
        for i in range(9):
            k = (i + 1) % 9
            tint = ceramic if i in (2, 3, 4, 5, 6, 7) else shadow if i in (1, 8) else navy
            hull.quad(a[i], b[i], b[k], a[k], shade(tint, .96 if j % 3 == 0 else 1.))
    for ring, reverse in ((sections[0], False), (sections[-1], True)):
        center = tuple(sum(p[i] for p in ring) / len(ring) for i in range(3))
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            hull.tri(center, b if reverse else a, a if reverse else b, metal)
    for side in (-1, 1):
        points = [(side * .74, -2.68), (side * 3.55, -3.04), (side * 3.30, -1.75),
                  (side * 2.86, -.90), (side * .90, 1.42)]
        if side < 0:
            points.reverse()
        hull.prism(points, 1.10, 1.31, navy)
        deck = [(x * .987, y + .018) for x, y in points]
        hull.prism(deck, 1.29, 1.37, ceramic)
        stripe = [(side * 1.06, -2.54), (side * 3.35, -2.87),
                  (side * 3.23, -2.45), (side * 1.02, -2.02)]
        if side < 0:
            stripe.reverse()
        hull.prism(stripe, 1.371, 1.382, amber)
        # Inlaid access panels and articulated trailing flaps.
        for j in range(3):
            local = Mesh()
            local.bevel_box((0, 0, 0), (.59, .34, .035), shadow, .016)
            hull.add(local, (side * (1.29 + j * .62), -2.98 + j * .018, 1.36), heading=side * 8)
        panel = [(side * 1.04, -.75), (side * 2.54, -1.17), (side * 2.39, -.87), (side * 1.00, .22)]
        if side < 0:
            panel.reverse()
        hull.prism(panel, 1.373, 1.383, shadow)
        for j in range(4):
            hull.bevel_box((side * (1.18 + j * .11), -1.35, 1.391), (.054, .48, .018), navy, .008)
        # Compact turbofan housings with smooth bell profiles and recessed cores.
        x = side * 2.28
        for a, b, r, er, tint in ((-3.55, -3.15, .42, .49, metal),
                                  (-3.15, -1.15, .49, .43, navy),
                                  (-1.15, -.62, .43, .30, ceramic),
                                  (-.62, -.43, .30, .18, shadow)):
            hull.tube((x, a, 1.31), (x, b, 1.31), r, tint, 18, end_radius=er, smooth=True)
        for y in (-3.08, -2.65, -1.48):
            metalwork.tube((x, y - .025, 1.31), (x, y + .025, 1.31), .499, metal, 18,
                           cap=False, smooth=True)
        for k in range(6):
            angle = k * TAU / 6
            dx, dz = math.cos(angle), math.sin(angle)
            metalwork.tube((x + dx * .456, -3.24, 1.31 + dz * .456),
                           (x + dx * .466, -3.57, 1.31 + dz * .466), .021,
                           shadow, 5, end_radius=.018, smooth=True)
        metalwork.tube((x, -3.56, 1.31), (x, -3.73, 1.31), .425, metal, 20,
                       end_radius=.38, cap=False, smooth=True)
        metalwork.tube((x, -3.57, 1.31), (x, -3.67, 1.31), .355, navy, 20,
                       end_radius=.30, smooth=True)
        glow.ring((x, -3.735, 1.31), .294, .328, cyan, 24, tilt=90)
        glow.tube((x, -3.675, 1.31), (x, -3.681, 1.31), .225, shade(cyan, .78), 20)
        for k in range(8):
            a = k * TAU / 8
            metalwork.tube((x + math.cos(a) * .19, -3.688, 1.31 + math.sin(a) * .19),
                           (x + math.cos(a + .12) * .34, -3.707, 1.31 + math.sin(a + .12) * .34),
                           .012, shadow, 4, end_radius=.018)
        # Thick swept fins and a small orange leading edge.
        fin = [(side * 2.61, -2.94, 1.61), (side * 2.79, -3.02, 2.63),
               (side * 2.89, -2.65, 2.47), (side * 2.75, -1.12, 1.55)]
        other = [(x + side * .055, y, z) for x, y, z in fin]
        hull.quad(*fin, navy)
        hull.quad(*reversed(other), shadow)
        for j in range(4):
            hull.quad(fin[j], other[j], other[(j + 1) % 4], fin[(j + 1) % 4], ceramic)
        hull.tri((side * 2.806, -2.91, 2.52), (side * 2.87, -2.66, 2.39),
                 (side * 2.79, -1.67, 1.78), amber)
        # Polished suspension pistons, hydraulic links and chamfered landing pads.
        metalwork.tube((side * 1.7, -1.6, 1.17), (side * 1.91, -1.65, .39),
                       .085, metal, 10, smooth=True)
        metalwork.tube((side * 1.91, -1.65, .42), (side * 1.93, -1.61, .19),
                       .059, (.58, .67, .70), 10, smooth=True)
        metalwork.tube((side * 1.48, -1.16, .99), (side * 1.91, -1.58, .33),
                       .029, metal, 6, smooth=True)
        hull.bevel_box((side * 1.93, -1.52, .105), (.55, .90, .21), navy, .048)
        hull.bevel_box((side * 1.93, -1.53, .198), (.39, .64, .035), shadow, .016)
        for j in range(3):
            hull.box((side * 1.93, -1.77 + j * .21, .219), (.30, .025, .008), navy)
        light = (.97, .30, .11) if side < 0 else (.36, 1., .63)
        glow.bevel_box((side * 3.26, -2.69, 1.422), (.14, .31, .055), light, .025)
        glow.box((side * .46, 2.26, 1.59), (.045, .48, .025), (.96, .91, .68))
        # Side service latches are actual raised, readable mechanical details.
        for y in (-2.11, -1.48):
            metalwork.bevel_box((side * 1.085, y, 1.56), (.045, .25, .10), metal, .015)
    metalwork.tube((0, 2.3, 1.16), (0, 2.23, .16), .072, metal, 10, smooth=True)
    metalwork.tube((0, 2.23, .46), (0, 2.23, .16), .049, (.59, .68, .71), 10, smooth=True)
    hull.bevel_box((0, 2.28, .08), (.38, .68, .16), navy, .035)
    # Deep glazing has its own material, with restrained reflected-sky bands.
    windshield = [(-.575, 1.23, 2.13), (.575, 1.23, 2.13),
                  (.605, -.37, 2.55), (-.605, -.37, 2.55)]
    glass.quad(*reversed(windshield), (.085, .25, .31),
               colors=((.13, .32, .39), (.21, .41, .45), (.06, .19, .24), (.04, .16, .23)))
    glass.quad((-.43, -1.14, 2.47), (.43, -1.14, 2.47), (.605, -.37, 2.55),
               (-.605, -.37, 2.55), (.115, .29, .34))
    for side in (-1, 1):
        glass.quad((side * .59, 1.17, 2.136), (side * .82, .39, 1.98),
                   (side * .88, -.67, 2.16), (side * .60, -.37, 2.55), (.055, .18, .24))
    for a, b in zip(windshield, windshield[1:] + windshield[:1]):
        metalwork.tube(a, b, .038, navy, 7, smooth=True)
    metalwork.tube((0, 1.24, 2.138), (0, -.37, 2.559), .023, shadow, 6, smooth=True)
    glass.quad((-.39, .36, 2.366), (.40, .36, 2.366), (.25, .67, 2.284),
               (-.42, .67, 2.284), (.20, .39, .44))
    hull.bevel_box((0, -2.34, 2.24), (.79, .70, .12), navy, .045)
    for i in range(7):
        metalwork.bevel_box((-.31 + i * .103, -2.34, 2.316), (.045, .52, .028), metal, .01)
    for side in (-1, 1):
        hull.bevel_box((side * .46, -1.60, 2.377), (.24, .49, .07), shadow, .022)
        glow.box((side * .46, -1.60, 2.416), (.034, .31, .014), shade(cyan, .65))
    metalwork.tube((.63, -1.90, 2.12), (.72, -2.04, 2.91), .020, metal, 7,
                   end_radius=.007, smooth=True)
    glow.sphere((.72, -2.04, 2.91), (.038, .038, .044), cyan, 8, 5, smooth=True)
    hull_node = hull.node("wayfarer-hull", root, two_sided=True)
    hull_node.setShaderInput("ae_material", .44, .19, 0., 0.)
    metal_node = metalwork.node("wayfarer-titanium-detail", root, two_sided=True)
    metal_node.setShaderInput("ae_material", .30, .76, 0., 0.)
    glass_node = glass.node("wayfarer-cockpit-glazing", root, two_sided=True)
    glass_node.setShaderInput("ae_material", .16, .67, .025, 0.)
    glow.node("wayfarer-luminous-panels", root, two_sided=True, unlit=True)
    return root


def _solar_array(mesh, glow, center, width, depth, nx, ny):
    """A framed photovoltaic cassette with recessed cells and silver bus lines."""
    x, y, z = center
    dark, frame = (.055, .105, .16), (.36, .43, .45)
    relief = max(1., width / 6.)
    mesh.bevel_box((x, y, z), (width, depth, .16 * relief), frame, .045 * relief)
    mesh.box((x, y, z + .10 * relief), (width - .11, depth - .11, .025 * relief), dark)
    cw, ch = (width - .25) / nx, (depth - .25) / ny
    for ix in range(nx):
        for iy in range(ny):
            cx = x - width * .5 + .125 + (ix + .5) * cw
            cy = y - depth * .5 + .125 + (iy + .5) * ch
            halfx, halfy = cw * .458, ch * .459
            tint = (.095 + (ix % 3) * .008, .225 + (iy % 2) * .012, .31 + (ix % 2) * .012)
            mesh.quad((cx - halfx, cy - halfy, z + .145 * relief), (cx + halfx, cy - halfy, z + .145 * relief),
                      (cx + halfx, cy + halfy, z + .145 * relief), (cx - halfx, cy + halfy, z + .145 * relief), tint,
                      colors=(shade(tint, .74), shade(tint, .9), shade(tint, 1.15), tint))
            for line in (-.23, .23):
                xx = cx + cw * line
                mesh.quad((xx - .0035 * relief, cy - ch * .44, z + .18 * relief),
                          (xx + .0035 * relief, cy - ch * .44, z + .18 * relief),
                          (xx + .0035 * relief, cy + ch * .44, z + .18 * relief),
                          (xx - .0035 * relief, cy + ch * .44, z + .18 * relief), (.32, .43, .47))
    for side in (-1, 1):
        mesh.box((x + side * (width * .5 - .045), y, z + .145 * relief), (.025, depth - .22, .04 * relief), frame)
    glow.box((x + width * .5 - .15, y - depth * .5 + .06, z + .18 * relief), (.12, .025, .025 * relief), (.46, .83, .85))


def outpost_mesh(accent=(.15, .78, .85)):
    """An open survey pavilion with ceramic soffits and layered field equipment."""
    mesh, glow = Mesh(), Mesh()
    pale, dark, steel = (.74, .79, .76), (.08, .14, .18), (.29, .37, .40)
    warm = (.89, .53, .23)
    _lathe(mesh, (0, 0, 0), ((7.05, -.4), (7.4, -.23), (7.4, .28),
                            (7.26, .35), (7.20, .42), (7.03, .54), (0, .54)),
           steel, 36, palette=[shade(steel, .7), steel, steel, dark, pale, pale, shade(pale, .76)], smooth=False)
    # Flush expansion seams and fine navigational marks articulate the large slab.
    for i in range(12):
        a = i * TAU / 12
        ca, sa = math.cos(a), math.sin(a)
        mesh.tube((ca * 1.4, sa * 1.4, .544), (ca * 6.96, sa * 6.96, .544),
                  .017, shade(steel, .74), 3, cap=False)
        if i % 3 == 0:
            glow.tube((ca * 5.6, sa * 5.6, .552), (ca * 6.48, sa * 6.48, .552),
                      .025, mix(accent, (.79, .93, .89), .4), 4, cap=False)
    mesh.ring((0, 0, .546), 6.60, 6.67, steel, 48)
    for i in range(6):
        a = TAU * i / 6
        x, y = 4.25 * math.cos(a), 4.25 * math.sin(a)
        top = (x * .94, y * .94, 4.6)
        mesh.tube((x, y, .5), top, .22, pale, 10, end_radius=.15)
        for z, radius in ((.72, .25), (4.29, .18)):
            t = (z - .5) / 4.1
            mesh.tube((x * (1 - .06 * t), y * (1 - .06 * t), z - .07),
                      (x * (1 - .06 * t), y * (1 - .06 * t), z + .07), radius,
                      steel, 10, end_radius=radius)
        glow.tube((x * 1.008, y * 1.008, 1.9), (x * .965, y * .965, 3.86),
                  .021, mix(accent, (.68, .94, .91), .34), 5)
        mesh.tube((x * .94, y * .94, 4.32), (x * .63, y * .63, 4.57),
                  .074, steel, 7, end_radius=.047)
    # A narrow floating eave, stepped roof panels and a recessed instrument crown.
    _lathe(mesh, (0, 0, 0), ((0, 4.4), (5.45, 4.4), (5.60, 4.49), (5.58, 4.66),
                            (5.40, 4.74), (3.67, 5.28), (3.51, 5.34), (0, 5.34)),
           pale, 36, palette=[dark, dark, steel, pale, pale, pale, steel, steel], smooth=False)
    glow.ring((0, 0, 4.397), 4.88, 4.925, shade(accent, .85), 64)
    for i in range(12):
        a = i * TAU / 12
        ca, sa = math.cos(a), math.sin(a)
        mesh.tube((ca * 3.66, sa * 3.66, 5.293), (ca * 5.4, sa * 5.4, 4.758),
                  .013, steel, 4, cap=False)
        if i % 3 == 0:
            glow.tube((ca * 5.19, sa * 5.19, 4.46), (ca * 4.15, sa * 4.15, 4.46),
                      .021, (.9, .88, .71), 4, cap=False)
    _lathe(mesh, (0, 0, 0), ((1.38, 5.34), (1.41, 5.53), (1.19, 6.15),
                            (1.03, 6.37), (0, 6.37)), dark, 20,
           palette=[steel, dark, dark, pale, pale], smooth=False)
    for i in range(10):
        a = i * TAU / 10
        b = a + .27
        glow.quad((1.39 * math.cos(a), 1.39 * math.sin(a), 5.62),
                  (1.39 * math.cos(b), 1.39 * math.sin(b), 5.62),
                  (1.25 * math.cos(b), 1.25 * math.sin(b), 6.02),
                  (1.25 * math.cos(a), 1.25 * math.sin(a), 6.02), shade(accent, .62))
    # A chamfered survey table and an etched, non-solid holographic globe.
    _lathe(mesh, (0, 0, 0), ((.82, .54), (.90, .68), (.81, 1.42),
                            (1.18, 1.73), (1.18, 1.84), (0, 1.84)), dark, 12,
           palette=[steel, dark, dark, pale, pale, steel], smooth=False)
    glow.ring((0, 0, 1.85), .92, .95, accent, 40)
    mesh.tube((0, 0, 1.84), (0, 0, 2.37), .065, steel, 10, smooth=True)
    glow.ring((0, 0, 2.80), .41, .423, shade(accent, .75), 36)
    glow.ring((0, 0, 2.80), .41, .423, shade(accent, .70), 36, tilt=64)
    glow.ring((0, 0, 2.80), .41, .423, shade(accent, .70), 36, tilt=-64)
    glow.sphere((0, 0, 2.80), (.28, .28, .28), mix(accent, (.69, .96, .87), .28),
                14, 9, smooth=True)
    for side in (-1, 1):
        mesh.bevel_box((side * 3., .5, .9), (.65, 3., .75), dark, .075)
        mesh.bevel_box((side * 3., .5, 1.3), (.72, 3.08, .12), pale, .048)
        for j in range(4):
            mesh.bevel_box((side * 3., -.43 + j * .6, 1.37), (.45, .45, .016), steel, .007)
            glow.box((side * 3., -.43 + j * .6, 1.383), (.28, .016, .008), shade(accent, .73))
        mesh.box((side * 3.33, -.62, .94), (.014, .19, .3), warm)
    # Offset antenna: a smoothly segmented reflector and structural back ribs.
    mesh.tube((6, 2, 0), (6, 2, 7.8), .16, steel, 12, end_radius=.085, smooth=True)
    mesh.tube((6, 2, .10), (6, 2, .66), .34, dark, 10, end_radius=.26)
    _lathe(mesh, (6, 2, 0), ((.15, 7.36), (.47, 7.49), (.91, 7.67),
                            (1.43, 7.97), (1.90, 8.35)), pale, 24,
           palette=[steel, steel, pale, pale, pale], smooth=True)
    for i in range(8):
        a = i * TAU / 8
        mesh.tube((6 + .28 * math.cos(a), 2 + .28 * math.sin(a), 7.39),
                  (6 + 1.88 * math.cos(a), 2 + 1.88 * math.sin(a), 8.32),
                  .025, steel, 5, end_radius=.014)
    mesh.tube((6, 2, 7.4), (6, 2, 9.1), .035, dark, 8, smooth=True)
    glow.sphere((6, 2, 9.1), (.095, .095, .095), accent, 10, 6, smooth=True)
    for side in (-1, 1):
        mesh.tube((side * 5, -3, 0), (side * 8, -3, 2.1), .16, steel, 9, smooth=True)
        _solar_array(mesh, glow, (side * 8, -3, 2.1), 3.4, 4.2, 4, 5)
    return mesh, glow


def _halo_slab(mesh, a, b, color):
    """A stone arc block with chamfered radial and front/back edges."""
    profile = ((5.45, -.43), (5.59, -.6), (6.35, -.6), (6.5, -.43),
               (6.5, .43), (6.35, .6), (5.59, .6), (5.45, .43))
    rings = [[(r * math.cos(angle), y, 6.4 + r * math.sin(angle)) for r, y in profile]
             for angle in (a, (a + b) * .5, b)]
    for j in range(2):
        for k in range(8):
            kk = (k + 1) % 8
            mesh.quad(rings[j][k], rings[j + 1][k], rings[j + 1][kk], rings[j][kk],
                      shade(color, (1.03, .99, 1.10, .79, .91, 1.04, .96, .74)[k]))
    for ring, reverse in ((rings[0], False), (rings[-1], True)):
        center = tuple(sum(p[k] for p in ring) / len(ring) for k in range(3))
        for i in range(8):
            a1, b1 = ring[i], ring[(i + 1) % 8]
            mesh.tri(center, b1 if reverse else a1, a1 if reverse else b1, shade(color, .77))


def ruin_mesh(accent=(.22, .95, .88)):
    """An ancient segmented halo: cleaved basalt, inlaid bronze, fine light script."""
    mesh, glow = Mesh(), Mesh()
    stone, inlay = (.27, .33, .34), (.52, .47, .33)
    _lathe(mesh, (0, 0, 0), ((7.73, -1), (8.2, -.73), (8.2, .12), (7.8, .35), (0, .35)),
           stone, 20, palette=[shade(stone, .62), shade(stone, .68), shade(stone, .78), stone, stone], smooth=False)
    _lathe(mesh, (0, 0, 0), ((6.55, .35), (6.55, .46), (6.30, .55), (0, .55)),
           stone, 20, palette=[shade(stone, .79), stone, shade(stone, 1.11), shade(stone, 1.08)], smooth=False)
    for radius in (3.55, 4.95, 5.85):
        mesh.ring((0, 0, .556), radius, radius + .045, shade(stone, .61), 64)
    for i in range(12):
        a = i * TAU / 12
        mesh.tube((math.cos(a) * 5.02, math.sin(a) * 5.02, .561),
                  (math.cos(a) * 5.71, math.sin(a) * 5.71, .561),
                  .025, inlay, 3, cap=False)
    # The missing voussoirs and different stone tones make the ruin readable far away.
    for i in range(18):
        if i in (9, 10):
            continue
        a = -math.pi * .16 + i * TAU / 22
        b = a + TAU / 24
        tint = mix(stone, (.43, .46, .39), (i % 4) * .038)
        _halo_slab(mesh, a, b, tint)
        for inner, outer, tint2 in ((5.76, 5.80, inlay), (6.17, 6.21, inlay)):
            mesh.quad((inner * math.cos(a + .018), -.606, 6.4 + inner * math.sin(a + .018)),
                      (outer * math.cos(a + .018), -.606, 6.4 + outer * math.sin(a + .018)),
                      (outer * math.cos(b - .018), -.606, 6.4 + outer * math.sin(b - .018)),
                      (inner * math.cos(b - .018), -.606, 6.4 + inner * math.sin(b - .018)), tint2)
        # Different short glyphs within each block; no giant emissive stripe.
        for j in range(3):
            angle = a + .058 + j * .063
            rr = 5.94 + .035 * ((i + j) % 2)
            ca, sa = math.cos(angle), math.sin(angle)
            tangent = Vec3(-sa, 0, ca)
            radial = Vec3(ca, 0, sa)
            center = Vec3(ca * rr, -.612, 6.4 + sa * rr)
            for start, end in ((-.054, .044),):
                p, q = center + radial * start, center + radial * end
                glow.quad(tuple(p - tangent * .014), tuple(p + tangent * .014),
                          tuple(q + tangent * .014), tuple(q - tangent * .014),
                          shade(accent, .68 + .11 * ((i + j) % 3)))
            if (i + j) % 2 == 0:
                p = center + radial * .038
                glow.quad(tuple(p), tuple(p + tangent * .076),
                          tuple(p + tangent * .076 + radial * .017), tuple(p + radial * .017), shade(accent, .78))
    for side, h in ((-1, 8.4), (1, 6.8)):
        x = side * 8
        _lathe(mesh, (x, 1, 0), ((.98, -.6), (1., .21), (.77, .56), (.69, h - .6),
                                (.58, h), (0, h + .015)), stone, 5,
               palette=[shade(stone, .62), stone, shade(stone, .94), stone,
                        mix(stone, (.42, .47, .4), .3), shade(stone, .9)], smooth=False, phase=math.pi * .1)
        for j in range(6):
            z = 1.4 + j * .69
            mesh.bevel_box((x, .27, z), (.47, .035, .45), shade(stone, .55), .014)
            glow.box((x - .11, .244, z), (.029, .016, .28), shade(accent, .70))
            glow.box((x + .014, .244, z + (.07 if j % 2 else -.07)), (.20, .016, .028), accent)
    _lathe(mesh, (0, -3, 0), ((1.07, .55), (1.10, .73), (.86, 1.08), (.72, 1.4), (0, 1.4)),
           stone, 10, palette=[shade(stone, .67), stone, stone, inlay, shade(stone, .73)], smooth=False)
    glow.ring((0, -3, 1.412), .49, .53, accent, 32)
    # A clear crystalline artifact rather than a featureless bright blob.
    for k in range(6):
        a, b = k * TAU / 6, (k + 1) * TAU / 6
        p = (.56 * math.cos(a), -3 + .56 * math.sin(a), 2.38)
        q = (.56 * math.cos(b), -3 + .56 * math.sin(b), 2.38)
        glow.tri((0, -3, 3.19), p, q, shade(accent, .68 + .05 * (k % 3)))
        glow.tri((0, -3, 1.63), q, p, shade(accent, .42 + .08 * (k % 3)))
    glow.ring((0, -3, 2.36), .82, .835, shade(accent, .72), 40, tilt=18)
    return mesh, glow


def building_mesh(kind, accent=(.21, .9, .9)):
    mesh, glow = Mesh(), Mesh()
    dark, pale, steel = (.10, .17, .22), (.74, .80, .77), (.29, .38, .41)
    amber = (.91, .53, .23)
    if kind == "habitat":
        # The exact walk-in shell also defines the compound collision walls.
        # All ornament stays flush and the 2.28 x 2.32 m doorway stays open.
        mesh.box((0, 0, -.05), (6.9, 6.6, .5), dark)
        mesh.box((0, 0, 3.16), (6.8, 6.4, .32), dark)
        for x in (-3.1, 3.1):
            mesh.box((x, 0, 1.6), (.24, 6, 2.8), pale)
        mesh.box((0, 2.9, 1.6), (6.2, .24, 2.8), pale)
        for x in (-2.12, 2.12):
            mesh.box((x, -2.9, 1.6), (1.96, .24, 2.8), pale)
        mesh.box((0, -2.9, 2.76), (2.28, .24, .48), pale)
        for side in (-1, 1):
            # Deep coloured glazing is framed with ceramic at the wall's surface.
            mesh.box((side * 3.226, 0, 1.85), (.012, 4.1, .80), dark)
            for j in range(3):
                y = -1.34 + j * 1.34
                mesh.box((side * 3.235, y, 1.85), (.009, 1.25, .64), mix(accent, (.08, .15, .23), .73))
                glow.box((side * 3.241, y, 2.106), (.004, 1.18, .028), shade(accent, .59))
                mesh.box((side * 3.243, y, 1.80), (.004, 1.19, .014), (.33, .43, .46))
            mesh.box((side * 2.12, -3.026, 1.85), (1.38, .012, .78), dark)
            mesh.box((side * 2.12, -3.037, 1.85), (1.23, .006, .63), mix(accent, (.08, .15, .23), .73))
            glow.box((side * 2.12, -3.042, 2.107), (1.17, .004, .027), shade(accent, .62))
            mesh.box((side * 1.13, -3.028, 1.36), (.075, .012, 2.22), steel)
            glow.box((side * 1.089, -3.038, 1.32), (.028, .009, 2.10), shade(accent, .92))
            # Cladding joints, corner strips and small service inspection covers.
            for y in (-2.47, 2.47):
                mesh.box((side * 3.233, y, 1.56), (.018, .09, 2.64), steel)
            mesh.box((side * 3.232, 0, .61), (.018, 5.84, .22), dark)
            for j in range(8):
                mesh.box((side * 3.243, -.98 + j * .28, .62), (.004, .11, .095), steel)
            mesh.box((side * 2.12, -3.027, .63), (1.78, .012, .22), dark)
            mesh.box((side * 2.70, -3.038, .63), (.13, .009, .17), amber)
            # Roof cassettes stay away from the standing passage and centre ray.
            mesh.bevel_box((side * 2.08, .08, 3.325), (1.71, 5.75, .014), pale, .006)
            glow.box((side * 2.84, 0, 3.337), (.035, 5.54, .006), shade(accent, .76))
            mesh.bevel_box((side * 2.13, 1.75, 3.332), (1.07, 1.05, .016), steel, .006)
            for j in range(6):
                mesh.box((side * 2.13, 1.39 + j * .14, 3.343), (.82, .04, .004), dark)
            mesh.box((side * 2.60, 0, .204), (.035, 5.36, .008), steel)
            glow.box((side * 2.83, 0, 2.992), (.035, 4.81, .01), mix(accent, (.89, .96, .88), .60))
            # Fasteners and equipment marks are shallow enough to remain collider-consistent.
            for y in (-2.60, 2.60):
                for z in (.35, 2.89):
                    mesh.box((side * 3.232, y, z), (.012, .075, .075), steel)
        mesh.box((0, -3.027, 2.67), (2.20, .012, .21), dark)
        glow.box((0, -3.038, 2.63), (2.15, .008, .037), accent)
        for j in range(5):
            glow.box((-.28 + j * .14, -3.039, 2.735), (.058, .006, .034), shade(accent, .61))
        glow.box((0, -2.9, .207), (1.8, .25, .014), shade(accent, .66))
        glow.box((0, 2.775, 2.55), (3.5, .012, .054), mix(accent, (.88, .93, .79), .40))
        mesh.box((0, 2.773, 1.96), (2.12, .012, .68), steel)
        for j in range(4):
            glow.box((-.77 + j * .51, 2.762, 1.96), (.33, .005, .40), shade(accent, .27 + j * .06))
    elif kind == "solar":
        mesh.bevel_box((0, 0, .35), (1.2, 1.1, .7), pale, .09)
        mesh.box((0, -.553, .36), (.76, .01, .22), dark)
        for j in range(5):
            mesh.box((-.28 + j * .14, -.562, .36), (.045, .005, .14), steel)
        mesh.tube((0, 0, .70), (0, 0, 2.3), .18, steel, 12, smooth=True)
        mesh.tube((-.41, 0, 1.78), (.41, 0, 1.78), .19, dark, 12, smooth=True)
        mesh.tube((0, 0, 1.43), (-2.31, 0, 2.17), .08, pale, 8, smooth=True)
        mesh.tube((0, 0, 1.43), (2.31, 0, 2.17), .08, pale, 8, smooth=True)
        _solar_array(mesh, glow, (0, 0, 2.3), 6, 3.8, 7, 4)
        glow.box((.43, -.563, .37), (.08, .008, .08), shade(accent, .77))
    elif kind == "extractor":
        _lathe(mesh, (0, 0, 0), ((1.82, -.1), (2., .06), (2., .25), (1.7, .4), (0, .4)),
               dark, 16, palette=[dark, steel, steel, dark, dark], smooth=False)
        _lathe(mesh, (0, 0, 0), ((1.02, .4), (1.1, .67), (1.02, 1.30),
                                (1.02, 1.82), (.84, 2.57), (.8, 2.7)),
               pale, 16, palette=[steel, pale, pale, dark, pale, pale], smooth=False)
        _lathe(mesh, (0, 0, 0), ((1.26, 2.70), (1.30, 2.80), (1.25, 3.01), (1., 3.2), (0, 3.2)),
               dark, 16, palette=[steel, dark, dark, pale, steel], smooth=False)
        for i in range(8):
            a = i * TAU / 8
            ca, sa = math.cos(a), math.sin(a)
            if i % 2 == 0:
                mesh.tube((ca, sa, 2.), (ca * 2.3, sa * 2.3, .2), .105, pale, 8, end_radius=.075, smooth=True)
                mesh.tube((ca * 1.27, sa * 1.27, 1.61), (ca * 2.0, sa * 2.0, .66), .071,
                          steel, 8, smooth=True)
            glow.tube((ca * 1.04, sa * 1.04, 1.39), (ca * 1.04, sa * 1.04, 1.71),
                      .035, accent, 6)
            mesh.tube((ca * .92, sa * .92, 1.88), (ca * .79, sa * .79, 2.50),
                      .028, steel, 6, smooth=True)
        mesh.ring((0, 0, 3.215), .49, .79, dark, 24)
        for i in range(12):
            a = i * TAU / 12
            mesh.tube((.18 * math.cos(a), .18 * math.sin(a), 3.22),
                      (.75 * math.cos(a + .15), .75 * math.sin(a + .15), 3.22),
                      .036, steel, 4, end_radius=.068)
    else:
        _lathe(mesh, (0, 0, 0), ((1.24, -.15), (1.4, -.04), (1.39, .08), (1.20, .2), (0, .2)),
               dark, 12, palette=[dark, steel, steel, dark, dark], smooth=False)
        mesh.tube((0, 0, .2), (0, 0, 4.8), .17, pale, 10, end_radius=.07, smooth=True)
        for z in (.42, 1.0, 2.9, 3.57):
            mesh.tube((0, 0, z), (0, 0, z + .12), .181 - z * .017, steel, 10)
        for side in (-1, 1):
            mesh.tube((0, 0, 3.5), (side * .9, 0, 4.3), .059, pale, 8, end_radius=.025, smooth=True)
            mesh.bevel_box((side * .82, 0, 4.23), (.17, .29, .44), dark, .045)
            glow.box((side * .82, -.153, 4.23), (.065, .009, .24), shade(accent, .8))
        mesh.tube((0, 0, 4.61), (0, 0, 5.15), .18, steel, 10, end_radius=.07)
        glow.sphere((0, 0, 4.91), (.23, .23, .29), accent, 14, 8, smooth=True)
        glow.ring((0, 0, 4.57), .56, .59, shade(accent, .79), 48)
        mesh.bevel_box((0, -.18, 1.4), (.55, .3, .55), dark, .056)
        mesh.box((0, -.336, 1.42), (.42, .014, .38), steel)
        glow.box((0, -.347, 1.45), (.33, .009, .25), shade(accent, .39))
        for j in range(3):
            glow.box((-.10 + j * .10, -.355, 1.25), (.04, .005, .03), accent)
    return mesh, glow


def station_mesh():
    """A finely articulated orbital survey exchange with a inhabited ring."""
    mesh, glow = Mesh(), Mesh()
    pale, dark, metal = (.68, .75, .76), (.065, .115, .17), (.27, .35, .41)
    cyan, amber = (.29, .82, .9), (.94, .63, .28)
    _lathe(mesh, (0, 0, 0), ((0, -50), (7, -50), (10, -46), (18.2, -33), (19, -31),
                            (19, -13), (23, -11), (23, 11), (19, 13), (19, 31),
                            (18.2, 33), (10, 46), (7, 50), (0, 50)),
           pale, 32, palette=[metal, metal, pale, pale, metal, metal, pale, pale,
                             metal, metal, pale, pale, metal, metal], smooth=False)
    # Repeated flush pressure panels subdivide the central drum at believable scale.
    for i in range(32):
        a, b = i * TAU / 32 + .025, (i + 1) * TAU / 32 - .025
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
        for low, high in ((-10, -3), (3, 10)):
            mesh.quad((ca * 23.08, sa * 23.08, low), (cb * 23.08, sb * 23.08, low),
                      (cb * 23.08, sb * 23.08, high), (ca * 23.08, sa * 23.08, high),
                      shade(pale, .92 if i % 2 else 1.03))
        for z in (-24, 21):
            glow.quad((ca * 19.13, sa * 19.13, z), (cb * 19.13, sb * 19.13, z),
                      (cb * 19.13, sb * 19.13, z + 1.12), (ca * 19.13, sa * 19.13, z + 1.12),
                      shade(cyan, .73))
        for z in (-38, 36):
            radius = 15.9
            mesh.tube((radius * ca, radius * sa, z), (radius * ca * .89, radius * sa * .89, z + 3),
                      .30, metal, 5)
    for z in (-28, -16, 16, 28):
        mesh.tube((0, 0, z), (0, 0, z + .7), 19.28, dark, 32, cap=False)
    for i in range(8):
        a = i * TAU / 8
        x, y = math.cos(a), math.sin(a)
        # Triangular trusses sit inside the established strut envelope.
        mesh.tube((x * 18, y * 18, 0), (x * 83, y * 83, 0), 2.10, metal, 10, smooth=True)
        tangent = Vec3(-y, x, 0)
        for side in (-1, 1):
            aa = Vec3(x * 24, y * 24, 1.98) + tangent * (side * 1.65)
            bb = Vec3(x * 74, y * 74, 1.98) + tangent * (side * 1.65)
            mesh.tube(tuple(aa), tuple(bb), .21, pale, 6, smooth=True)
        for j in range(6):
            rr = 27 + j * 8
            left = Vec3(x * rr, y * rr, 2.00) + tangent * (1.65 if j % 2 else -1.65)
            right = Vec3(x * (rr + 7.4), y * (rr + 7.4), 2.00) - tangent * (1.65 if j % 2 else -1.65)
            mesh.tube(tuple(left), tuple(right), .16, metal, 5)
        center = (x * 83, y * 83, 0)
        _lathe(mesh, center, ((8.4, -6), (10, -4.7), (10, 4.7), (8.4, 6), (0, 6)),
               pale, 16, palette=[metal, pale, pale, metal, metal], smooth=False)
        for k in range(12):
            aa, bb = k * TAU / 12 + .075, (k + 1) * TAU / 12 - .075
            for low, high in ((-2.6, -1.45), (.9, 2.05)):
                glow.quad((center[0] + math.cos(aa) * 10.7, center[1] + math.sin(aa) * 10.7, low),
                          (center[0] + math.cos(bb) * 10.7, center[1] + math.sin(bb) * 10.7, low),
                          (center[0] + math.cos(bb) * 10.7, center[1] + math.sin(bb) * 10.7, high),
                          (center[0] + math.cos(aa) * 10.7, center[1] + math.sin(aa) * 10.7, high),
                          shade(cyan, .62 + .08 * (k % 3)))
        mesh.tube((center[0], center[1], 6), (center[0], center[1], 14.8), .34,
                  metal, 8, end_radius=.15, smooth=True)
        glow.sphere((center[0], center[1], 15), (.58, .58, .68), cyan, 10, 6, smooth=True)
        mesh.ring((center[0], center[1], 6.04), 5.45, 6.01, pale, 32)
    # A rounded pressure hull with broad ceramic strips and longitudinal windows.
    major, minor = 83, 5.7
    for i in range(96):
        a, b = TAU * i / 96, TAU * (i + 1) / 96
        for j in range(12):
            c, d = TAU * j / 12, TAU * (j + 1) / 12
            def point(t, u, offset=0):
                radius = major + (minor + offset) * math.cos(u)
                return (radius * math.cos(t), radius * math.sin(t), (minor + offset) * math.sin(u))
            def normal(t, u):
                return (math.cos(t) * math.cos(u), math.sin(t) * math.cos(u), math.sin(u))
            tint = pale if j in (0, 1, 4, 5, 6, 7, 10, 11) else metal
            tint = shade(tint, .91 if i % 12 == 0 else 1.)
            mesh.quad(point(a, c), point(b, c), point(b, d), point(a, d), tint,
                      normals=(normal(a, c), normal(b, c), normal(b, d), normal(a, d)))
        if i % 12 != 0:
            aa, bb = a + .008, b - .008
            for sign in (-1, 1):
                # Cabin windows are short discrete panes around the outer quarter.
                c, d = sign * .19, sign * .30
                glow.quad(point(aa, c, .22), point(bb, c, .22),
                          point(bb, d, .22), point(aa, d, .22), shade(cyan, .60))
    for z in (-5.74, 5.74):
        glow.ring((0, 0, z), 82.55, 82.83, shade(cyan, .68), 128)
    # Docking spine with recessed lanes, hold marks and chamfered pressure doors.
    mesh.bevel_box((0, -70, -12), (18, 100, 5), dark, .75)
    mesh.bevel_box((0, -116, -8), (35, 30, 4), metal, .70)
    mesh.box((0, -71, -9.475), (11.8, 95, .04), metal)
    for side in (-1, 1):
        for j in range(9):
            glow.box((side * 7, -28 - j * 10, -9.43), (.48, 3.6, .06), amber)
            mesh.box((side * 5.45, -28 - j * 10, -9.432), (.16, 7.4, .026), pale)
        mesh.box((side * 14.8, -116, -5.78), (.20, 23, .12), pale)
        for j in range(5):
            glow.box((side * 15.7, -125 + j * 4.5, -5.72), (.31, 1.1, .12), cyan)
        # Broad radiator/solar assemblies inherit the station's silhouette.
        mesh.tube((side * 13, 12, 10), (side * 45, 25, 25), 1.35, metal, 10, smooth=True)
        mesh.bevel_box((side * 45, 25, 25), (44, 27, .8), dark, .26)
        _solar_array(mesh, glow, (side * 45, 25, 25.36), 43.6, 26.6, 10, 6)
        for j in range(3):
            mesh.box((side * 45, 17 + j * 8, 25.52), (43.2, .14, .08), metal)
    # Central approach target and a small communications crown.
    mesh.box((0, -116, -5.72), (9, .22, .12), pale)
    mesh.box((0, -111.6, -5.72), (.22, 8.8, .12), pale)
    for z in (-50, 50):
        mesh.tube((0, 0, z), (0, 0, z + (5 if z > 0 else -5)), .29, metal, 8, smooth=True)
        glow.sphere((0, 0, z + (5 if z > 0 else -5)), (.72, .72, .72), cyan, 10, 6, smooth=True)
    return mesh, glow
