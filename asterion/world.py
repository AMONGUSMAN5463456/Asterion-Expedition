"""Streaming surface worlds and orbital scenes, drawn entirely from code.

WorldRenderer owns every node it creates.  Resource identifiers depend only on
the world seed and chunk coordinates, so a harvested deposit stays harvested
when its chunk unloads or a saved expedition is resumed.
"""
from __future__ import annotations

from functools import lru_cache
import math
import random
import time

from panda3d.core import (
    AmbientLight, DirectionalLight, Fog, Material, NodePath, TransparencyAttrib, Vec3,
)

from .content import ITEMS
from .collision import CollisionWorld
from .geometry import (
    Mesh, building_mesh, crystal_mesh, fauna_mesh, flora_mesh, grass_mesh,
    mix, outpost_mesh, rgba, rock_mesh, ruin_mesh, shade, station_mesh,
)
from .universe import terrain_height
from .planet_visuals import atmosphere_mesh, planet_mesh, planet_texture, terrain_detail_texture


CHUNK_SIZE = 64
TERRAIN_STEP = 4
STYLES = {"verdant":"fan", "desert":"succulent", "frozen":"coral",
          "volcanic":"succulent", "toxic":"coral", "oceanic":"fan",
          "crystalline":"coral", "fungal":"mushroom"}
RESOURCE_NAMES = {"carbon":"Carbon-bearing flora", "oxygen":"Oxygen bloom",
                  "sodium":"Sodium lantern", "ferrite":"Ferrite outcrop",
                  "copper":"Copper seam", "crystal":"Resonant crystal",
                  "cobalt":"Cobalt cluster", "gold":"Gold-bearing stone",
                  "silicon":"Silicon shard"}


def _seed(seed, cx, cy, salt=0):
    """Portable integer mixer; never use Python's process-randomised hash."""
    return (int(seed)*1664525 + int(cx)*73856093 + int(cy)*19349663 + salt*83492791) & 0xFFFFFFFF


def _xyz(position):
    try:
        value = tuple(float(position[i]) for i in range(3))
        if all(math.isfinite(c) for c in value):
            return value
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    return (0.0,0.0,22.0)


def _box(name,center,size,heading=0):
    return dict(id=name,type="box",center=center,half=tuple(v*.5 for v in size),heading=heading)


def _cylinder(name,center,radius,height):
    return dict(id=name,type="cylinder",center=center,radius=radius,height=height)


def _placed_shapes(shapes,owner,position,heading=0,scale=1):
    """Transform local colliders with exactly the same convention as Mesh.add."""
    ca,sa=math.cos(math.radians(heading)),math.sin(math.radians(heading))
    result=[]
    for original in shapes:
        shape=dict(original)
        x,y,z=(v*scale for v in shape["center"])
        shape["center"]=(position[0]+x*ca-y*sa,position[1]+x*sa+y*ca,position[2]+z)
        shape["id"]=owner+":"+shape["id"]
        if shape["type"]=="box":
            shape["half"]=tuple(v*scale for v in shape["half"])
            shape["heading"]=heading+shape.get("heading",0)
        else:
            shape["radius"]*=scale
            if "height" in shape:
                shape["height"]*=scale
        result.append(shape)
    return result


def _flora_shapes(style,seed):
    if style=="mushroom":
        rng=random.Random(seed)
        shapes=[]
        for i,(x,y,h) in enumerate(((0,0,4.3),(1.7,.4,2.3),(-1.1,.65,1.65))):
            h*=.85+rng.random()*.3
            shapes.append(_cylinder(f"stem-{i}",(x+.06,y,h*.5),.25 if i==0 else .15,h))
        return shapes
    if style=="fan":
        return [_cylinder("trunk",(.08,0,2.1),.24,4.2)]
    if style=="coral":
        return [_cylinder("trunk",(0,0,1.1),.40,2.2)]
    return [_cylinder("trunk",(0,0,1.65),.53,3.3)]


def _structure_shapes(kind):
    """Deliberately compound: an open pavilion or doorway stays traversable."""
    shapes=[]
    if kind=="outpost":
        shapes=[_cylinder("plinth",(0,0,-.025),7.4,.75),
                _cylinder("floor",(0,0,.445),7.05,.19),
                _cylinder("roof",(0,0,4.87),5.55,.94),
                _cylinder("roof-cap",(0,0,5.8),1.4,1.2),
                _cylinder("kiosk",(0,0,1.2),1.12,1.4),
                _cylinder("antenna",(6,2,3.9),.18,7.8)]
        for i in range(6):
            a=i*math.tau/6
            shapes.append(_cylinder(f"pillar-{i}",(4.12*math.cos(a),4.12*math.sin(a),2.55),.29,4.1))
        for side in (-1,1):
            shapes.extend((_box(f"bench-{side}",(side*3,.5,.955),(.72,3.08,.81)),
                           _box(f"panel-{side}",(side*8,-3,2.1),(3.4,4.2,.18))))
    elif kind=="ruin":
        shapes=[_cylinder("plinth",(0,0,-.325),8.0,1.35),
                _cylinder("floor",(0,0,.45),6.45,.2),
                _cylinder("relic-base",(0,-3,.97),1.0,.84)]
        for side,h in ((-1,8.4),(1,6.8)):
            shapes.append(_cylinder(f"pillar-{side}",(side*8,1,(h-.6)*.5),.88,h+.6))
        for i in range(18):
            if i in (9,10):
                continue
            a=-math.pi*.16+i*math.tau/22
            b=a+math.tau/24
            xs=[r*math.cos(t) for r in (5.45,6.5) for t in (a,b)]
            zs=[6.4+r*math.sin(t) for r in (5.45,6.5) for t in (a,b)]
            shapes.append(_box(f"halo-{i}",((min(xs)+max(xs))*.5,0,(min(zs)+max(zs))*.5),
                               (max(xs)-min(xs),1.2,max(zs)-min(zs))))
    elif kind=="habitat":
        shapes=[_box("floor",(0,0,-.05),(6.9,6.6,.5)),
                _box("roof",(0,0,3.16),(6.8,6.4,.32)),
                _box("left-wall",(-3.1,0,1.6),(.24,6,2.8)),
                _box("right-wall",(3.1,0,1.6),(.24,6,2.8)),
                _box("back-wall",(0,2.9,1.6),(6.2,.24,2.8)),
                _box("front-left",(-2.12,-2.9,1.6),(1.96,.24,2.8)),
                _box("front-right",(2.12,-2.9,1.6),(1.96,.24,2.8)),
                _box("door-lintel",(0,-2.9,2.76),(2.28,.24,.48))]
    elif kind=="solar":
        shapes=[_box("base",(0,0,.35),(1.2,1.1,.7)),
                _cylinder("mast",(0,0,1.15),.21,2.3),
                _box("panel",(0,0,2.3),(6,3.8,.2))]
    elif kind=="extractor":
        shapes=[_cylinder("base",(0,0,.15),1.85,.5),
                _cylinder("body",(0,0,1.8),1.12,2.8)]
    else:
        shapes=[_cylinder("base",(0,0,.025),1.3,.35),
                _cylinder("mast",(0,0,2.5),.18,4.6),
                _box("console",(0,-.18,1.4),(.55,.3,.55))]
    return shapes


def _station_shapes():
    shapes=[_cylinder("core",(0,0,0),23,64),
            _cylinder("top",(0,0,41),15,18),_cylinder("bottom",(0,0,-41),15,18),
            _box("dock-spine",(0,-70,-12),(18,100,5)),
            _box("dock-apron",(0,-116,-8),(35,30,4))]
    for i in range(32):
        angle=(i+.5)*math.tau/32
        shapes.append(_box(f"ring-{i}",(83*math.cos(angle),83*math.sin(angle),0),
                           (16.8,11.4,11.4),math.degrees(angle)+90))
    for i in range(8):
        angle=i*math.tau/8
        x,y=math.cos(angle),math.sin(angle)
        shapes.extend((_cylinder(f"module-{i}",(x*83,y*83,0),10,12),
                       _box(f"strut-{i}",(x*50,y*50,0),(66,6,6),math.degrees(angle))))
    for side in (-1,1):
        shapes.append(_box(f"solar-{side}",(side*45,25,25),(44,27,1)))
    return shapes


class WorldRenderer:
    def __init__(self, app):
        self.app = app
        self.root = None
        self.sky_root = None
        self.mode = None
        self.planet = None
        self.system = None
        self.state = None
        self.chunks = {}
        self._entities = {}
        self._depleted = set()
        self._lights = []
        self._fauna = {}
        self._spinners = []
        self._buildings = {}
        self._chunk_queue = []
        self._chunk_center = None
        self._chunk_job = None
        self._chunk_key = None
        self._last_light = -100
        self._far_center = None
        self._far_terrain = None
        self._far_job = None
        self._weather = None
        self._stars = None
        self._sky_dome = None
        self._water = None
        self._sample = lambda x,y: 20.0
        self._radius = 3
        self._last_position = (0,0,22)
        self.collisions = CollisionWorld()

    def _start(self, mode):
        self.destroy()
        self.mode = mode
        self.root = self.app.render.attachNewNode("asterion-" + mode)
        material=Material("matte-survey-material")
        # Setting diffuse or ambient to white replaces vertex colour under
        # real fixed-function lighting. Unset colours follow the vertex data.
        material.setShininess(0)
        material.setSpecular((0,0,0,1))
        self.root.setMaterial(material)
        self.sky_root = self.root.attachNewNode("celestial-sky")
        self.sky_root.setFogOff(1)
        self._ambient = AmbientLight("world-ambient")
        self._ambient.setColor((.24,.29,.36,1))
        ambient_path = self.root.attachNewNode(self._ambient)
        self.app.render.setLight(ambient_path)
        self._sun = DirectionalLight("world-sun")
        self._sun.setColor((.68,.61,.51,1))
        sun_path = self.root.attachNewNode(self._sun)
        sun_path.setHpr(-38,-42,0)
        self.app.render.setLight(sun_path)
        self._lights = [ambient_path,sun_path]
        # Fixed-function lighting honours vertex colours on both desktop and
        # software renderers.  The app is free to enable optional shaders.
        self.root.setTwoSided(False)
        self._last_light = -100

    def load_surface(self, planet, state):
        self._start("surface")
        self.planet, self.state = dict(planet), state
        self.system = None
        self._seed_value = int(planet["seed"])
        self._sample = lru_cache(maxsize=120000)(lambda x,y: terrain_height(self._seed_value,x,y))
        quality = getattr(state,"settings",{}).get("quality","medium")
        self._radius = {"low":2,"medium":3,"high":4,"ultra":4}.get(quality,3)
        self._depleted = set(getattr(state,"depleted",{}).get(planet["id"],[]))
        self._style = STYLES.get(planet["biome"],"mushroom")
        self._ground_detail=terrain_detail_texture(self._seed_value)
        self._tree_models = [flora_mesh(self._style,planet["flora"],planet["accent"],self._seed_value+i)
                             for i in range(3)]
        self._grass_models = [grass_mesh(planet["flora"],planet["accent"],self._seed_value+i) for i in range(3)]
        self._rock_models = [rock_mesh(mix(planet["ground"],(.31,.37,.42),.5),self._seed_value+i)
                             for i in range(3)]
        self._setup_surface_sky()
        self._setup_water()
        self._setup_weather()
        self._fog = Fog("planet-atmosphere")
        self._fog.setColor(*planet["sky"])
        self._fog.setMode(Fog.MLinear)
        self._fog.setLinearRange(135, self._radius*CHUNK_SIZE+95)
        self.root.setFog(self._fog)
        # A few deliberately placed points of interest make the first minutes
        # legible, while the same chunk generator continues across the world.
        self._make_landmarks()
        position = _xyz(getattr(state,"position",(0,0,22)))
        self._last_position = position
        self._stream(position,initial=True)
        self._make_far_terrain(position,initial=True)
        for record in getattr(state,"bases",{}).get(planet["id"],[]):
            self.add_building(record)
        self.update(0,position,getattr(state,"elapsed",0),0)

    def height(self, x, y):
        """Exact height of the rendered four-metre terrain triangles."""
        try:
            x,y = float(x),float(y)
            if not math.isfinite(x) or not math.isfinite(y):
                return 20.0
        except (ValueError,TypeError,OverflowError):
            return 20.0
        x0,y0 = math.floor(x/TERRAIN_STEP)*TERRAIN_STEP,math.floor(y/TERRAIN_STEP)*TERRAIN_STEP
        fx,fy = (x-x0)/TERRAIN_STEP,(y-y0)/TERRAIN_STEP
        a = self._sample(x0,y0)
        b = self._sample(x0+TERRAIN_STEP,y0)
        c = self._sample(x0+TERRAIN_STEP,y0+TERRAIN_STEP)
        d = self._sample(x0,y0+TERRAIN_STEP)
        return a+(b-a)*fx+(c-b)*fy if fy <= fx else a+(c-d)*fx+(d-a)*fy

    def _normal(self,x,y):
        step=TERRAIN_STEP
        n=Vec3(self._sample(x-step,y)-self._sample(x+step,y),
               self._sample(x,y-step)-self._sample(x,y+step),2*step)
        n.normalize()
        return tuple(n)

    def _ground_color(self,x,y,z):
        base = self.planet["ground"]
        wave = math.sin(x*.047+y*.022+self._seed_value*.01)*math.cos(y*.051-x*.013)
        fine = math.sin(x*.31+y*.23)*math.cos(y*.17)
        col = shade(base,1.12 + .29*wave + .095*fine)
        # Broad irregular patches, pale shores, and rock-coloured steep slopes.
        patch = math.sin(x*.023+self._seed_value)*math.sin(y*.028+2.4)
        if patch > .25:
            col=mix(col,shade(self.planet["flora"],.74),(patch-.25)*.52)
        slope=abs(self._sample(x+4,y)-self._sample(x-4,y))+abs(self._sample(x,y+4)-self._sample(x,y-4))
        col=mix(col,mix(base,(.33,.31,.29),.63),max(0,slope-2.2)*.16)
        if z < self.planet["water_level"]+2.5:
            col=mix(col,(.72,.71,.53),.4)
        if self.planet["biome"] == "frozen" and z > 19:
            col=mix(col,(.86,.94,.97),min(.5,(z-19)*.04))
        if 5<y<65:
            trail_x=32*y/62+math.sin(y*.065)*2.5
            trail=max(0,1-abs(x-trail_x)/3.8)
            col=mix(col,mix(base,(.67,.6,.4),.55),trail*.8)
        return col

    def _terrain_mesh(self, ox, oy, size=CHUNK_SIZE, step=TERRAIN_STEP):
        steps=self._terrain_steps(ox,oy,size,step)
        while True:
            try:
                next(steps)
            except StopIteration as result:
                return result.value

    def _terrain_steps(self, ox, oy, size=CHUNK_SIZE, step=TERRAIN_STEP):
        mesh=Mesh()
        n=int(size/step)
        rows=[]
        for j in range(n+1):
            row=[]
            for i in range(n+1):
                x,y=ox+i*step,oy+j*step
                z=self._sample(x,y)
                row.append(((x,y,z),self._normal(x,y),self._ground_color(x,y,z)))
            rows.append(row)
            yield
        for j in range(n):
            for i in range(n):
                a,b,c,d=rows[j][i],rows[j][i+1],rows[j+1][i+1],rows[j+1][i]
                for triangle in ((a,b,c),(a,c,d)):
                    mesh.tri(*(p[0] for p in triangle), (1,1,1),
                             normals=tuple(p[1] for p in triangle),colors=tuple(p[2] for p in triangle),
                             texcoords=tuple((p[0][0]/12,p[0][1]/12) for p in triangle))
            yield
        return mesh

    def _make_far_terrain(self, position, initial=False):
        center=(math.floor(position[0]/256)*256,math.floor(position[1]/256)*256)
        if center != self._far_center:
            if self._far_job is not None:
                self._far_job.close()
            self._far_center=center
            self._far_job=self._build_far_terrain(center)
        deadline=time.perf_counter()+.002
        while self._far_job is not None:
            try:
                next(self._far_job)
            except StopIteration:
                self._far_job=None
            if not initial and time.perf_counter()>=deadline:
                break

    def _build_far_terrain(self, center):
        # Keep the old horizon visible until its replacement is ready.
        mesh=yield from self._terrain_steps(center[0]-1024,center[1]-1024,2048,32)
        node=yield from mesh._node_steps("distant-landscape",self.root)
        node.setTexture(self._ground_detail)
        node.setZ(-4.0)
        if self._far_terrain:
            self._far_terrain.removeNode()
        self._far_terrain=node

    def _stream(self, position, initial=False):
        center=(math.floor(position[0]/CHUNK_SIZE),math.floor(position[1]/CHUNK_SIZE))
        if center != self._chunk_center:
            self._chunk_center=center
            cx,cy=center
            wanted={(cx+dx,cy+dy) for dx in range(-self._radius,self._radius+1)
                    for dy in range(-self._radius,self._radius+1)
                    if dx*dx+dy*dy <= (self._radius+.5)**2}
            if self._chunk_job is not None and self._chunk_key not in wanted:
                self._chunk_job.close()
                self._chunk_job=None
                self._chunk_key=None
            for key in list(self.chunks):
                if key not in wanted:
                    chunk=self.chunks.pop(key)
                    for entity_id in chunk["ids"]:
                        self._entities.pop(entity_id,None)
                        self._fauna.pop(entity_id,None)
                        self.collisions.remove_group(entity_id)
                    self.collisions.remove_group(chunk["collision_group"])
                    chunk["root"].removeNode()
            self._chunk_queue=sorted(wanted-set(self.chunks),
                                     key=lambda p:(p[0]-cx)**2+(p[1]-cy)**2)
        # A chunk spans multiple frames; each yield leaves visible geometry and
        # its collision together. Initial loading still finishes synchronously.
        deadline=time.perf_counter()+.004
        while self._chunk_job is not None or self._chunk_queue:
            if self._chunk_job is None:
                self._chunk_key=self._chunk_queue.pop(0)
                self._chunk_job=self._make_chunk(*self._chunk_key)
            try:
                next(self._chunk_job)
            except StopIteration:
                self._chunk_job=None
                self._chunk_key=None
            if not initial and time.perf_counter()>=deadline:
                break

    def _in_clearance(self,x,y,padding=0):
        # Keep the landing pad, kiosk and the ancient halo approachable.
        return (math.hypot(x,y)<9+padding or
                math.hypot(x-32,y-62)<13+padding or
                math.hypot(x+86,y-108)<14+padding)

    def _make_chunk(self,cx,cy):
        key=(cx,cy)
        node=self.root.attachNewNode(f"surface-chunk-{cx}-{cy}")
        ids=[]
        collision_group=f"chunk:{cx},{cy}"
        collision_shapes=[]
        self.chunks[key]={"root":node,"ids":ids,"collision_group":collision_group}
        mesh=yield from self._terrain_steps(cx*CHUNK_SIZE,cy*CHUNK_SIZE)
        ground=yield from mesh._node_steps("ground",node)
        ground.setTexture(self._ground_detail)
        rng=random.Random(_seed(self._seed_value,cx,cy))
        remote=(cx%7==3 and cy%7==4 and abs(cx)+abs(cy)>4)
        rx,ry=(cx+.5)*CHUNK_SIZE,(cy+.5)*CHUNK_SIZE
        remote=remote and self.height(rx,ry)>self.planet["water_level"]+1
        def clearance(x,y,padding=0):
            return self._in_clearance(x,y,padding) or (remote and math.hypot(x-rx,y-ry)<14+padding)
        decor=Mesh()
        # Merged trees, grass, stones and ground leaves: two drawables per tile.
        abundance=.6 if self.planet["biome"] in ("desert","volcanic","frozen") else 1.0
        for i in range(int(12*abundance)):
            x,y=(cx+rng.random())*CHUNK_SIZE,(cy+rng.random())*CHUNK_SIZE
            z=self.height(x,y)
            if clearance(x,y,1) or z<self.planet["water_level"]+.5:
                continue
            scale=rng.uniform(.75,1.8)
            if self._style=="mushroom":
                scale*=1.25
            heading=rng.uniform(0,360)
            decor.add(self._tree_models[i%3],(x,y,z-.05),scale,heading)
            collision_shapes.extend(_placed_shapes(_flora_shapes(self._style,self._seed_value+i%3),
                f"{self.planet['id']}:c{cx},{cy}:tree{i}",(x,y,z-.05),heading,scale))
            yield
        for i in range(int(34*abundance)):
            x,y=(cx+rng.random())*CHUNK_SIZE,(cy+rng.random())*CHUNK_SIZE
            z=self.height(x,y)
            if clearance(x,y) or z<self.planet["water_level"]:
                continue
            decor.add(self._grass_models[i%3],(x,y,z),rng.uniform(.65,1.7),rng.uniform(0,360))
            yield
        for i in range(9):
            x,y=(cx+rng.random())*CHUNK_SIZE,(cy+rng.random())*CHUNK_SIZE
            z=self.height(x,y)
            if clearance(x,y):
                continue
            scale,heading=rng.uniform(.4,1.7),rng.uniform(0,360)
            decor.add(self._rock_models[i%3],(x,y,z-.15),scale,heading)
            if scale>.62:
                collision_shapes.extend(_placed_shapes([_cylinder("stone",(.12,0,.53),.84,1.35)],
                    f"{self.planet['id']}:c{cx},{cy}:rock{i}",(x,y,z-.15),heading,scale))
            yield
        yield from decor._node_steps("batched-flora-and-stones",node,two_sided=True)
        self.collisions.set_group(collision_group,collision_shapes)
        yield
        resources=self.planet["resources"]
        for i in range(7):
            x,y=(cx+.13+rng.random()*.74)*CHUNK_SIZE,(cy+.13+rng.random()*.74)*CHUNK_SIZE
            if clearance(x,y,2) or self.height(x,y)<self.planet["water_level"]+.3:
                continue
            resource=resources[rng.randrange(len(resources))]
            entity_id=f"{self.planet['id']}:c{cx},{cy}:r{i}"
            if self._resource(entity_id,resource,x,y,_seed(self._seed_value,cx,cy,i+1),node):
                ids.append(entity_id)
            yield
        if rng.random()<.34*float(self.planet.get("fauna_density",1)):
            x,y=(cx+.5)*CHUNK_SIZE,(cy+.5)*CHUNK_SIZE
            if not clearance(x,y,5) and self.height(x,y)>self.planet["water_level"]+1:
                entity_id=f"{self.planet['id']}:c{cx},{cy}:fauna"
                self._make_fauna(entity_id,x,y,_seed(self._seed_value,cx,cy,49),node)
                ids.append(entity_id)
                yield
        if remote:
            kind="outpost" if _seed(self._seed_value,cx,cy)%3==0 else "ruin"
            meshes=outpost_mesh(self.planet["accent"]) if kind=="outpost" else ruin_mesh(self.planet["accent"])
            landmark=NodePath("remote-"+kind)
            yield from meshes[0]._node_steps("structure",landmark,two_sided=True)
            yield from meshes[1]._node_steps("signal-lights",landmark,two_sided=True,unlit=True)
            z=self.height(rx,ry)
            landmark.setPos(rx,ry,z)
            landmark.reparentTo(node)
            landmark.setH((_seed(self._seed_value,cx,cy)%4)*90)
            entity_id=f"{self.planet['id']}:c{cx},{cy}:{kind}"
            self._entities[entity_id]=dict(id=entity_id,kind=kind,
                name="Remote survey exchange" if kind=="outpost" else "Meridian listening halo",
                pos=(rx,ry,z+1.4),radius=11,node=landmark)
            self.collisions.set_group(entity_id,_placed_shapes(_structure_shapes(kind),entity_id,
                (rx,ry,z),landmark.getH()))
            ids.append(entity_id)

    def _resource(self,entity_id,resource,x,y,seed,parent):
        if entity_id in self._depleted:
            return False
        rng=random.Random(seed)
        col=ITEMS.get(resource,{}).get("color",self.planet["accent"])
        is_plant=resource in ("carbon","oxygen","sodium")
        if resource == "carbon":
            mesh=flora_mesh(self._style,self.planet["flora"],self.planet["accent"],seed,.6)
            shapes=_flora_shapes(self._style,seed)
            shape_scale=.6
            radius,amount,hardness=1.7,18+rng.randrange(15),1.1
        elif resource in ("oxygen","sodium"):
            mesh=flora_mesh("succulent",mix(col,self.planet["flora"],.3),col,seed,.4)
            shapes=_flora_shapes("succulent",seed)
            shape_scale=.4
            radius,amount,hardness=1.0,10+rng.randrange(9),.75
        elif resource in ("crystal","cobalt","silicon","copper"):
            shape_scale=.6+rng.random()*.35
            mesh=crystal_mesh(col,seed,shape_scale)
            shapes=[_cylinder("deposit",(0,0,1.15),.73,2.3)]
            radius,amount,hardness=1.7,12+rng.randrange(17),1.6
        else:
            shape_scale=1.0+rng.random()*.3
            mesh=rock_mesh(mix(col,self.planet["ground"],.4),seed,shape_scale)
            shapes=[_cylinder("deposit",(.12,0,.56),.91,1.40)]
            radius,amount,hardness=1.65,20+rng.randrange(16),1.25
        node=mesh.node("resource-"+resource,parent,two_sided=True)
        z=self.height(x,y)
        node.setPos(x,y,z)
        node.setH(rng.uniform(0,360))
        self._entities[entity_id]=dict(id=entity_id,kind="flora" if is_plant else "mineral",
            name=RESOURCE_NAMES.get(resource,resource.title()+" deposit"),pos=(x,y,z+.6),
            radius=radius,node=node,resource=resource,amount=amount,hardness=hardness)
        placed=_placed_shapes(shapes,entity_id,(x,y,z),node.getH(),shape_scale)
        # A one-piece harvestable uses its entity ID directly for interaction
        # occlusion and ignore lists; multi-stem flora retain compound IDs.
        if len(placed)==1:
            placed[0]["id"]=entity_id
        self.collisions.set_group(entity_id,placed)
        return True

    def _make_fauna(self,entity_id,x,y,seed,parent):
        rng=random.Random(seed)
        size=rng.uniform(.75,1.3)
        node=fauna_mesh(mix(self.planet["flora"],(.7,.66,.47),.45),
                        self.planet["accent"],seed).node("lantern-grazer",parent,two_sided=True)
        node.setScale(size)
        z=self.height(x,y)
        node.setPos(x,y,z)
        node.setH(rng.uniform(0,360))
        names=("Lantern grazer","Ribbon drifter","Prism shellback")
        descriptions=("A peaceful six-legged browser carrying bioluminescent antennae.",
                      "A gentle filter feeder riding warm currents on layered ribbon fins.",
                      "A quiet forager whose overlapping shell plates collect trace minerals.")
        entity=dict(id=entity_id,kind="fauna",name=names[seed%3],pos=(x,y,z+1.1),
                    radius=1.8*size,node=node,description=descriptions[seed%3])
        self._entities[entity_id]=entity
        self._fauna[entity_id]=dict(origin=(x,y),phase=rng.random()*math.tau,size=size,variant=seed%3)

    def _make_landmarks(self):
        # Raised, open pavilion and broken halo deliberately flank the vista.
        for kind,name,x,y,meshes,radius in (
            ("outpost","Wayfarer survey exchange",32,62,outpost_mesh(self.planet["accent"]),10),
            ("ruin","The Unfinished Halo",-86,108,ruin_mesh(self.planet["accent"]),11),
        ):
            node=self.root.attachNewNode(kind)
            meshes[0].node(kind+"-structure",node,two_sided=True)
            meshes[1].node(kind+"-light",node,two_sided=True,unlit=True)
            z=self.height(x,y)
            node.setPos(x,y,z)
            entity_id=f"{self.planet['id']}:{kind}:landing"
            self._entities[entity_id]=dict(id=entity_id,kind=kind,name=name,pos=(x,y,z+1.4),radius=radius,node=node)
            self.collisions.set_group(entity_id,_placed_shapes(_structure_shapes(kind),entity_id,(x,y,z)))
        for i,(resource,x,y) in enumerate((("ferrite",-7,14),("carbon",10,20),
                ("oxygen",-13,27),("sodium",16,31),("copper",-22,39),("crystal",23,46))):
            self._resource(f"{self.planet['id']}:landing:r{i}",resource,x,y,
                           self._seed_value+i*97,self.root)
        self._make_fauna(f"{self.planet['id']}:landing:fauna",-17,51,self._seed_value+622,self.root)
        # Navigable landing marker with dashed radial inlays.
        marker=Mesh()
        marker.ring((0,0,20.015),5.3,5.4,mix(self.planet["ground"],(.84,.9,.86),.45),40)
        for i in range(12):
            a=i*math.tau/12
            marker.box((6.2*math.cos(a),6.2*math.sin(a),20.022),(.22,.22,.025),(.75,.86,.82))
        marker.node("landing-site-markers",self.root)

    def _setup_water(self):
        mesh=Mesh()
        col=rgba(self.planet["water"])
        mesh.quad((-6000,-6000,0),(6000,-6000,0),(6000,6000,0),(-6000,6000,0),col)
        self._water=mesh.node("stillwater",self.root)
        self._water.setZ(self.planet["water_level"])
        ripples=Mesh()
        # Thin surface bands catch sky colour without reflective shaders.
        for i in range(28):
            y=-620+i*49
            ripples.quad((-850,y,.018),(850,y+21,.018),(850,y+21.12,.018),(-850,y+.12,.018),
                         (*mix(col,self.planet["sky"],.6)[:3],.26))
        self._ripples=ripples.node("water-wind-ripples",self._water,unlit=True)
        self._ripples.setTransparency(TransparencyAttrib.MAlpha)
        self._ripples.setDepthWrite(False)

    def _sky_sphere(self):
        mesh=Mesh()
        sky=self.planet["sky"]
        def color(n):
            x,y,z=n
            h=max(0,z)
            base=mix(mix(sky,(.86,.71,.51),.26),mix(shade(sky,.42),(.025,.07,.18),.22),h**.55)
            front=math.sin(x*12+y*7+1.4*math.sin(z*11))*math.cos(z*18+y*3)
            streak=max(0,front-.51)*max(0,1-abs(h-.35)*2.1)
            return mix(base,(.80,.85,.89),streak*.29)
        mesh.sphere((0,0,0),(18000,18000,18000),sky,96,48,color_fn=color,smooth=True)
        node=mesh.node("gradient-atmosphere",self.sky_root,two_sided=True,unlit=True)
        node.setBin("background",0)
        node.setDepthWrite(False)
        node.setDepthTest(False)
        return node

    def _starfield(self,seed,count=700):
        rng=random.Random(seed)
        mesh=Mesh()
        for _ in range(count):
            z=rng.uniform(-1,1)
            angle=rng.random()*math.tau
            r=math.sqrt(max(0,1-z*z))
            direction=Vec3(r*math.cos(angle),r*math.sin(angle),z)
            center=direction*rng.uniform(10500,15000)
            side=direction.cross(Vec3(0,0,1))
            if side.lengthSquared()<.001:
                side=Vec3(1,0,0)
            side.normalize()
            up=side.cross(direction)
            size=rng.uniform(2,8)*(1.8 if rng.random()<.05 else 1)
            c=mix((.6,.78,1),(1,.82,.53),rng.random())
            mesh.quad(tuple(center-side*size-up*size),tuple(center+side*size-up*size),
                      tuple(center+side*size+up*size),tuple(center-side*size+up*size),c)
        node=mesh.node("stars",self.sky_root,two_sided=True,unlit=True)
        node.setBin("background",1)
        node.setDepthWrite(False)
        node.setDepthTest(False)
        node.setTransparency(TransparencyAttrib.MAlpha)
        return node

    def _disc(self,name,pos,radius,color,parent=None):
        mesh=Mesh()
        for i in range(64):
            a,b=i*math.tau/64,(i+1)*math.tau/64
            mesh.tri((0,0,0),(radius*math.cos(a),0,radius*math.sin(a)),
                     (radius*math.cos(b),0,radius*math.sin(b)),color)
        node=mesh.node(name,parent or self.sky_root,two_sided=True,unlit=True)
        node.setPos(*pos)
        node.setBillboardPointEye()
        node.setDepthWrite(False)
        node.setTransparency(TransparencyAttrib.MAlpha)
        return node

    def _setup_surface_sky(self):
        self._sky_dome=self._sky_sphere()
        self._stars=self._starfield(self._seed_value,500)
        self._sun_visual=self._disc("warm-distant-sun",(2500,4000,2400),180,(1,.93,.74,1))
        for scale,alpha in ((1.25,.10),(1.65,.045),(2.4,.022)):
            self._disc("sun-corona",(2500,4002,2400),180*scale,(1,.78,.45,alpha))
        p=dict(self.planet)
        p["ground"]=mix(self.planet["accent"],(.5,.55,.72),.45)[:3]
        p["water"]=mix(self.planet["sky"],(.06,.11,.23),.65)[:3]
        planet_node=self._planet_model(p,self.sky_root,radius=590,clouds=True,rings=True)
        planet_node.setPos(-1700,3900,1600)
        planet_node.setHpr(-14,13,21)
        moon=dict(p,ground=(.6,.63,.66),water=(.28,.31,.36),seed=p["seed"]+127,biome="desert")
        moon_node=self._planet_model(moon,self.sky_root,radius=125,clouds=False)
        moon_node.setPos(2100,5200,1800)

    def _planet_model(self,planet,parent,radius,clouds=True,rings=False):
        root=parent.attachNewNode("planet-"+planet.get("name","distant"))
        root.setLightOff(20)
        root.setMaterialOff(20)
        root.setShaderOff(20)
        # Explicit texture modulation + prelit vertices prevents white worlds
        # on fixed-function OpenGL, Metal compatibility layers and TinyDisplay.
        quality=getattr(self.state,"settings",{}).get("quality","medium")
        # Generated RAM textures bypass Panda's file-loader resizing. Keep
        # every quality level power-of-two for TinyDisplay and older GPUs.
        width=256 if self.mode=="surface" or quality=="low" else 512
        if radius<150:
            width=128
        surface=planet_mesh(radius).node("continental-surface",root,unlit=True)
        surface.setTexture(planet_texture(planet,clouds,width,width//2),20)
        self._spinners.append((root,.035))
        atmosphere=mix(planet.get("sky",planet["water"]),(.12,.52,.95),.45)
        halo=atmosphere_mesh(radius,atmosphere).node("atmospheric-limb",root,two_sided=True,unlit=True)
        halo.setBillboardPointEye()
        halo.setTransparency(TransparencyAttrib.MAlpha)
        halo.setDepthWrite(False)
        if rings:
            ring=Mesh()
            # Fine concentric dust lanes with a real dark division between the
            # inner and outer belts. Each band remains richly coloured unlit.
            for i in range(22):
                inner=1.28+i*.034
                if i in (7,8,16):
                    continue
                outer=inner+.018+(i%3)*.005
                tint=mix(planet["accent"],(.66,.58,.43),.55+.14*math.sin(i*1.7))
                tint=shade(tint,.60+.23*math.sin(i*1.21)**2)
                ring.ring((0,0,0),radius*inner,radius*outer,(*tint[:3],.58+(i%3)*.12),128,24)
            ringnode=ring.node("mineral-rings",root,two_sided=True,unlit=True)
            ringnode.setTransparency(TransparencyAttrib.MAlpha)
            ringnode.setDepthWrite(False)
        return root

    def _setup_weather(self):
        rng=random.Random(self._seed_value+551)
        mesh=Mesh()
        snowy=self.planet["biome"]=="frozen"
        for _ in range(90):
            x,y,z=rng.uniform(-34,34),rng.uniform(-34,34),rng.uniform(-12,28)
            w=.07 if snowy else .025
            length=.12 if snowy else .75
            mesh.quad((x-w,y,z),(x+w,y,z),(x+w+.2,y,z+length),(x-w+.2,y,z+length),
                      (.8,.88,.94,.5 if snowy else .27))
        self._weather=mesh.node("atmospheric-particles",self.root,two_sided=True,unlit=True)
        self._weather.setTransparency(TransparencyAttrib.MAlpha)
        self._weather.setDepthWrite(False)
        self._weather.hide()

    def load_orbit(self, system, state):
        self._start("orbit")
        self.system,self.state=dict(system),state
        self.planet=None
        self._depleted=set(getattr(state,"depleted",{}).get(f"orbit:{system['id']}",[]))
        self._ambient.setColor((.22,.27,.36,1))
        self._sun.setColor((.72,.64,.53,1))
        self.app.setBackgroundColor(.006,.012,.032,1)
        self._stars=self._starfield(int(system["id"])*773+47,1300)
        # Translucent, asymmetrical nebula ribbons give space a sense of depth.
        nebula=Mesh()
        for band in range(4):
            for i in range(50):
                x=-16000+i*640
                z=2600*math.sin(i*.075+band*.2)+band*540-500
                width=800+250*math.sin(i*.18)
                col=((.10,.18,.34,.075) if band%2 else (.24,.11,.29,.05))
                nebula.quad((x,17000,z-width),(x+650,17000,z-width+150),
                            (x+650,17000,z+width+150),(x,17000,z+width),col)
        nebnode=nebula.node("nebula-ribbons",self.sky_root,two_sided=True,unlit=True)
        nebnode.setTransparency(TransparencyAttrib.MAlpha)
        nebnode.setBin("background",0)
        nebnode.setDepthWrite(False)
        nebnode.setDepthTest(False)
        for i,planet in enumerate(system["planets"]):
            node=self._planet_model(planet,self.root,planet["size"],clouds=True,rings=i in (0,2))
            node.setPos(*planet["position"])
            node.setHpr(i*31,-8+i*6,13)
        self._disc("system-star",(-6000,14500,5700),400,rgba(system["star_color"]))
        for factor,alpha in ((1.4,.1),(2.2,.04),(3.5,.018)):
            self._disc("stellar-corona",(-6000,14510,5700),400*factor,(*system["star_color"],alpha))
        solid,glow=station_mesh()
        station=self.root.attachNewNode("orbital-survey-exchange")
        solid.node("station-hull",station,two_sided=True)
        glow.node("station-lights",station,two_sided=True,unlit=True)
        station.setPos(*system["station"])
        station.setH(-12)
        entity_id=f"s{system['id']}:station"
        self._entities[entity_id]=dict(id=entity_id,kind="station",name=system["name"]+" orbital exchange",
                                     pos=tuple(system["station"]),radius=145,node=station)
        self.collisions.set_group(entity_id,_placed_shapes(_station_shapes(),entity_id,
            system["station"],station.getH()))
        self._make_asteroids(system)
        self.update(0,_xyz(getattr(state,"position",(0,0,0))),getattr(state,"elapsed",0))

    def _make_asteroids(self,system):
        rng=random.Random(8011+int(system["id"])*1009)
        # A broad field, plus a visible nearby pocket of fuel-bearing stones.
        for i in range(95):
            entity_id=f"s{system['id']}:asteroid:{i}"
            a=rng.uniform(0,math.tau)
            distance=rng.uniform(400,1950)
            x,y=math.sin(a)*distance,math.cos(a)*distance
            z=rng.uniform(-320,440)
            if i<7:
                x,y,z=rng.uniform(-120,180),rng.uniform(210,520),rng.uniform(-90,110)
            if (Vec3(x,y,z)-Vec3(*system["station"])).length()<210:
                continue
            radius=rng.uniform(6,22)
            resource=("ferrite","gold","cobalt","crystal")[i%4]
            mesh=rock_mesh(mix((.29,.32,.39),ITEMS[resource]["color"],.16),i+int(system["id"])*999,radius)
            node=mesh.node("asteroid",self.root)
            node.setPos(x,y,z)
            node.setHpr(rng.uniform(0,360),rng.uniform(0,360),rng.uniform(0,360))
            self._entities[entity_id]=dict(id=entity_id,kind="asteroid",name="Drifting "+resource+" asteroid",
                pos=(x,y,z),radius=radius*1.4,node=node,resource=resource,amount=25+rng.randrange(30),hardness=1.0)
            # The mesh's asymmetric stone is centered slightly above its node.
            # A conservative enclosing sphere also covers its slow rotation.
            self.collisions.set_group(entity_id,[dict(id=entity_id,type="sphere",
                center=(x,y,z),radius=radius*1.42)])
            self._spinners.append((node,rng.uniform(-1.1,1.1)))
            if entity_id in self._depleted:
                self.set_depleted(entity_id)

    def interactables(self):
        return list(self._entities.values())

    def set_depleted(self,entity_id):
        self._depleted.add(entity_id)
        entity=self._entities.pop(entity_id,None)
        self._fauna.pop(entity_id,None)
        self._buildings.pop(entity_id,None)
        self.collisions.remove_group(entity_id)
        if entity and not entity["node"].isEmpty():
            entity["node"].removeNode()

    def add_building(self,record):
        if not self.root or self.mode!="surface" or not isinstance(record,dict):
            return
        entity_id=record.get("id")
        if not entity_id or entity_id in self._buildings:
            return
        kind=record.get("kind","beacon")
        solid,glow=building_mesh(kind,self.planet["accent"])
        node=self.root.attachNewNode("constructed-"+kind)
        solid.node(kind+"-structure",node,two_sided=True)
        glow.node(kind+"-indicators",node,two_sided=True,unlit=True)
        pos=_xyz(record.get("pos",(0,0,20)))
        node.setPos(pos[0],pos[1],self.height(pos[0],pos[1]))
        node.setH(float(record.get("heading",0)))
        self._buildings[entity_id]=node
        self._entities[entity_id]=dict(id=entity_id,kind="beacon",building_kind=kind,
            name={"beacon":"Expedition beacon","habitat":"Field habitat","solar":"Solar array", "extractor":"Mineral extractor"}.get(kind,kind),
            pos=(pos[0],pos[1],self.height(pos[0],pos[1])+1.3),radius=3 if kind!="habitat" else 4,node=node)
        self.collisions.set_group(entity_id,_placed_shapes(_structure_shapes(kind),entity_id,
            (pos[0],pos[1],node.getZ()),node.getH()))

    def update(self,dt,position,elapsed,storm=0):
        if not self.root or self.root.isEmpty():
            return
        dt=max(0,min(.2,float(dt)))
        elapsed=float(elapsed)
        pos=_xyz(position)
        self._last_position=pos
        self.sky_root.setPos(*pos)
        if self.mode=="surface":
            self._stream(pos)
            self._make_far_terrain(pos)
            if self._water:
                self._water.setPos(pos[0],pos[1],self.planet["water_level"])
                self._ripples.setY(math.sin(elapsed*.055)*11)
            for entity_id,data in list(self._fauna.items()):
                entity=self._entities.get(entity_id)
                if not entity:
                    continue
                x0,y0=data["origin"]
                if (x0-pos[0])**2+(y0-pos[1])**2>180**2:
                    continue
                t=elapsed*.105+data["phase"]
                x,y=x0+math.cos(t)*4.2,y0+math.sin(t)*3.5
                hover=.9+math.sin(elapsed*1.4+data["phase"])*.28 if data["variant"]==1 else 0
                z=self.height(x,y)+math.sin(elapsed*3+data["phase"])*.045+hover
                entity["node"].setPos(x,y,z)
                entity["node"].setH(-math.degrees(t))
                entity["node"].setR(math.sin(elapsed*2.8+data["phase"])*1.5)
                entity["pos"]=(x,y,z+1.1*data["size"])
            intensity=max(0,min(1,float(storm)))
            if intensity>.01:
                self._weather.show()
                self._weather.setPos(pos[0]+math.sin(elapsed*.15)*7,pos[1],pos[2]-(elapsed*8)%16)
                self._weather.setColorScale(1,1,1,intensity)
            else:
                self._weather.hide()
            if elapsed-self._last_light>.2 or elapsed<self._last_light:
                # Start in a generous morning; a full cycle is several minutes.
                phase=elapsed/max(60,self.planet["day_length"])*math.tau+.78
                altitude=math.sin(phase)
                daylight=max(.12,min(1,(altitude+.17)*1.15))
                twilight=max(0,1-abs(altitude)*3.5)
                storm_dark=1-intensity*.42
                self._ambient.setColor(((.12+daylight*.19)*storm_dark,
                    (.17+daylight*.20)*storm_dark,(.26+daylight*.18)*storm_dark,1))
                self._sun.setColor((.66*daylight*storm_dark,.59*daylight*storm_dark,.47*daylight*storm_dark,1))
                self._lights[1].setHpr(-38+math.degrees(phase)*.35,-max(8,altitude*64),0)
                tone=mix((.12,.19,.34),(1,1,1),daylight)
                self._sky_dome.setColorScale(tone[0]*storm_dark,tone[1]*storm_dark,tone[2]*storm_dark,1)
                horizon=mix(self.planet["sky"],(.9,.47,.32),twilight*.3)
                horizon=shade(horizon,(.2+.8*daylight)*storm_dark)
                self._fog.setColor(*horizon[:3])
                self._fog.setLinearRange(95-intensity*45,self._radius*CHUNK_SIZE+95-intensity*115)
                self._stars.setColorScale(1,1,1,max(0,.92-daylight))
                self._sun_visual.setColorScale(1,.8+daylight*.2,.65+daylight*.35,daylight)
                self.app.setBackgroundColor(*horizon)
                self._last_light=elapsed
        for node,speed in self._spinners:
            if not node.isEmpty():
                node.setH(node.getH()+dt*speed)

    def destroy(self):
        if self._chunk_job is not None:
            self._chunk_job.close()
            self._chunk_job=None
        self._chunk_key=None
        if self._far_job is not None:
            self._far_job.close()
            self._far_job=None
        self.collisions.clear()
        for light in self._lights:
            if not light.isEmpty():
                self.app.render.clearLight(light)
        self._lights=[]
        if self.root and not self.root.isEmpty():
            self.root.removeNode()
        self.root=None
        self.sky_root=None
        self.mode=None
        self.chunks={}
        self._entities={}
        self._fauna={}
        self._spinners=[]
        self._buildings={}
        self._chunk_queue=[]
        self._chunk_center=None
        self._far_center=None
        self._far_terrain=None
        self._weather=None
        self._water=None
        self._stars=None
        self._sky_dome=None
        self._sample=lambda x,y:20.0
