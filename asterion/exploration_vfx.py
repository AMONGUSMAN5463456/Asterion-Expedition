"""Bounded survey pulses and extraction light, tied to visible interactions."""
from __future__ import annotations

import math

from panda3d.core import (BitMask32, ColorBlendAttrib, TransparencyAttrib,
                          Vec3)

from .geometry import Mesh


def _luminous(node):
    node.setTransparency(TransparencyAttrib.MAlpha)
    node.setDepthWrite(False)
    node.setLightOff(10)
    node.setShaderOff(10)
    node.setFogOff(10)
    node.hide(BitMask32.bit(1))
    node.setAttrib(ColorBlendAttrib.make(ColorBlendAttrib.MAdd,
        ColorBlendAttrib.OIncomingAlpha, ColorBlendAttrib.OOne))
    return node


def mining_beam(parent, start, end, elapsed=0.):
    """Layered optical filament and a small sparkling contact corona."""
    root=parent.attachNewNode("extraction-beam")
    delta=end-start
    if delta.lengthSquared()<.001:
        return root
    axis=delta.normalized()
    tangent=axis.cross(Vec3(0,0,1))
    if tangent.lengthSquared()<.001:
        tangent=Vec3(1,0,0)
    tangent.normalize()
    other=axis.cross(tangent)
    # Physical widths avoid a beam becoming a screen-wide ribbon in orbit.
    radius=min(.09,.018+delta.length()*.00016)
    core=Mesh()
    core.tube(tuple(start),tuple(end),radius,(.56,1.,1.,.96),8,smooth=True)
    _luminous(core.node("extraction-hot-core",root,unlit=True))
    glow=Mesh()
    glow.tube(tuple(start),tuple(end),radius*3.2,(.055,.55,.8,.10),8,smooth=True)
    glow.sphere(tuple(end),(radius*7,)*3,(.17,.85,1.,.16),16,8,smooth=True)
    for i in range(10):
        phase=(elapsed*2.1+i*.61803398875)%1
        angle=i*2.39996323+elapsed*.17
        direction=tangent*math.cos(angle)+other*math.sin(angle)
        length=.12+phase*.37
        origin=end-axis*.08+direction*length
        tip=origin+direction*.065-axis*(.02+.05*phase)
        glow.tube(tuple(origin),tuple(tip),.008,(.70,.95,1.,(1-phase)*.8),4)
    _luminous(glow.node("extraction-contact-radiance",root,unlit=True))
    return root


class ExplorationVFX:
    def __init__(self,app):
        self.app=app
        self.pulse=None
        self.age=0.
        self._destroyed=False

    def scan(self):
        app=self.app
        if self._destroyed or app.world.root is None:
            return
        if self.pulse is not None and not self.pulse.isEmpty():
            self.pulse.removeNode()
        self.pulse=app.world.root.attachNewNode("expanding-survey-wave")
        point=Vec3(app.controller.position)
        if app.controller.mode=="surface":
            point.z=app.world.height(point.x,point.y)+.15
        self.pulse.setPos(app.world.root.getRelativePoint(app.render,point))
        self.pulse.setQuat(app.render.getQuat(app.world.root))
        mesh=Mesh()
        for i in range(160):
            a,b=i*math.tau/160,(i+1)*math.tau/160
            def p(r,angle):return (r*math.cos(angle),r*math.sin(angle),0)
            for lo,hi,alpha in ((.94,.975,.08),(.975,1.,.32),(1.,1.025,.06)):
                mesh.quad(p(lo,a),p(hi,a),p(hi,b),p(lo,b),(.17,.86,.92,alpha))
            if i%8==0:
                mesh.quad(p(.85,a),p(.90,a),p(.90,a+.007),p(.85,a+.007),(.76,.99,1.,.45))
        _luminous(mesh.node("survey-interference-ring",self.pulse,two_sided=True,unlit=True))
        self.age=0.
        self.pulse.setScale(.1)

    def update(self,dt):
        if self._destroyed or self.pulse is None or self.pulse.isEmpty():
            return
        self.age+=max(0.,min(float(dt),.1))
        progress=self.age/2.8
        if progress>=1:
            self.pulse.removeNode()
            self.pulse=None
            return
        self.pulse.setScale(.1+progress*105)
        self.pulse.setColorScale(1,1,1,(1-progress)**1.7)

    def destroy(self):
        if self.pulse is not None and not self.pulse.isEmpty():
            self.pulse.removeNode()
        self._destroyed=True
