"""Physical ocean/land datum and orbit-to-touchdown collision regressions."""

import math
import unittest

from panda3d.core import Vec3

from asterion.planetary import PlanetField, PlanetFrame, chart_direction
from asterion.universe import get_planet


def directions(count=128):
    """Even deterministic coverage, without a longitude or polar singularity."""
    angle = math.pi * (3 - math.sqrt(5))
    for index in range(count):
        y = 1 - 2 * (index + .5) / count
        radius = math.sqrt(1 - y*y)
        yield Vec3(math.cos(angle*index)*radius, y,
                   math.sin(angle*index)*radius)


class PhysicalOceanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A distant 56 km diameter body exercises the float-vector boundary at
        # the larger physical scale independently of star-chart generation.
        planet = get_planet(0, 0)
        planet.update(size=28000., position=(350000., -310000., 40000.))
        cls.field = PlanetField(planet)
        samples = list(directions())
        cls.ocean = min(samples, key=cls.field.seabed_elevation)
        cls.land = max(samples, key=cls.field.seabed_elevation)

    def test_visible_and_collision_elevations_share_one_datum(self):
        field = self.field
        self.assertLess(field.seabed_elevation(self.ocean), field.water_level-10)
        self.assertGreater(field.seabed_elevation(self.land), field.water_level+10)
        for direction in directions():
            raw = field.seabed_elevation(direction)
            self.assertEqual(field.elevation(direction), max(raw, field.water_level))
        self.assertEqual(field.elevation((0, 0, 1)), 20.)
        self.assertEqual(field.seabed_elevation((0, 0, 1)), 20.)

    def test_ocean_normal_height_point_and_atmosphere_match_water_top(self):
        field, normal = self.field, self.ocean
        frame = PlanetFrame.from_planet(field.planet, normal)
        self.assertLess((field.normal(normal)-normal).length(), .000002)
        for x, y in ((0, 0), (73, -55), (-390, 470)):
            height = field.local_height(frame, x, y)
            world = frame.to_world((x, y, height))
            self.assertAlmostEqual(field.altitude(world), 0, delta=.035)
            relative = tuple(float(world[i])-field._center[i] for i in range(3))
            expected = math.sqrt(sum(value*value for value in relative))
            self.assertAlmostEqual(expected, field.radius+field.water_level, delta=.035)
        world = frame.to_world((0, 0, field.water_level+500))
        atmosphere = field.atmosphere(world, -normal*800)
        self.assertAlmostEqual(atmosphere['altitude'], 500, delta=.04)
        self.assertGreater(atmosphere['cloud'], .95)
        self.assertGreater(atmosphere['heat'], .1)

    def test_ocean_color_retains_seabed_depth_after_physical_clamp(self):
        field = self.field
        ocean = [n for n in directions() if field.seabed_elevation(n) < field.water_level-2]
        self.assertTrue(all(field.elevation(n) == field.water_level for n in ocean))
        colors = {tuple(round(component, 4) for component in field.color(n)) for n in ocean}
        self.assertGreater(len(colors), 8)

    def test_stepped_orbit_descents_reach_land_ocean_poles_and_antipode(self):
        field = self.field
        targets = [self.land, self.ocean, Vec3(0, 1, 0), Vec3(0, -1, 0),
                   Vec3(0, 0, -1), Vec3(0, 0, 1)]
        for normal in targets:
            with self.subTest(direction=tuple(normal)):
                position = field.center+normal*(field.radius+field.elevation(normal)+2600)
                cloud_crossed, previous = False, field.altitude(position)
                for step in range(260):
                    # The actual terrain sphere sweep used by flight, at its
                    # 120 Hz physics interval and a boosted re-entry speed.
                    result = field.sweep(position, -normal*(1600/120), 3)
                    position = result.position
                    altitude = field.altitude(position)
                    self.assertTrue(all(math.isfinite(value) for value in position))
                    self.assertGreaterEqual(altitude, 2.97)
                    self.assertLessEqual(altitude, previous+.08)
                    previous = altitude
                    cloud_crossed |= 300 < altitude < 650
                    if result.hit_ids:
                        self.assertGreater(step, 185)
                        self.assertLess(altitude, 3.25)
                        self.assertTrue(result.grounded)
                        self.assertTrue(cloud_crossed)
                        break
                else:
                    self.fail('An orbit descent failed to reach the rendered surface')

    def test_ocean_initial_penetration_recovers_to_water_not_seabed(self):
        field, normal = self.field, self.ocean
        start = field.center+normal*(field.radius+field.water_level-8)
        result = field.sweep(start, (0, 0, 0), 3)
        self.assertTrue(result.hit_ids)
        self.assertAlmostEqual(field.altitude(result.position), 3.025, delta=.05)
        self.assertGreater(field.altitude(result.position), 2.98)

    def test_antipodal_seam_and_shoreline_have_no_collision_gap(self):
        field = self.field
        radius = field.radius
        for latitude in (-1.45, -.5, 0, .8, 1.45):
            sides = [chart_direction(sign*(math.pi*radius-.002), latitude*radius, radius)
                     for sign in (-1, 1)]
            contacts = []
            for normal in sides:
                start = field.center+normal*(radius+field.elevation(normal)+50)
                contact = field.sweep(start, -normal*100, 3)
                self.assertTrue(contact.hit_ids)
                self.assertGreaterEqual(field.altitude(contact.position), 2.97)
                contacts.append(contact.position)
            self.assertLess((contacts[0]-contacts[1]).length(), .12)

        # Bisect the continuous raw field across a coast, then test both sides
        # of the land/water join and the join itself with a swept hull.
        low, high = Vec3(self.ocean), Vec3(self.land)
        for _ in range(45):
            middle = (low+high).normalized()
            if field.seabed_elevation(middle) < field.water_level:
                low = middle
            else:
                high = middle
        coast = (low+high).normalized()
        tangent = (self.land-coast*self.land.dot(coast)).normalized()
        for offset in (-3, -.1, 0, .1, 3):
            normal = (coast+tangent*(offset/radius)).normalized()
            start = field.center+normal*(radius+field.elevation(normal)+25)
            result = field.sweep(start, -normal*40, 3)
            self.assertTrue(result.hit_ids)
            self.assertGreaterEqual(field.altitude(result.position), 2.97)
            self.assertLess(field.altitude(result.position), 3.4)

    def test_hull_over_water_contacts_a_bank_beyond_its_radial_sample(self):
        class CoastalField(PlanetField):
            def seabed_elevation(self, direction):
                x, _, z = direction
                along = self.radius*math.atan2(x, z)
                return self.water_level+max(-100., min(100., .8*(along-.8)))

        planet = dict(self.field.planet, position=(0, 0, 0))
        field = CoastalField(planet)
        datum = field.radius+field.water_level
        moves = [((0, 0, datum+80), (0, 0, -100)),
                 ((-8, 0, datum+3.05), (20, 0, 0))]
        for start, displacement in moves:
            with self.subTest(displacement=displacement):
                result = field.sweep(start, displacement, 3)
                self.assertTrue(result.hit_ids)
                # Independent geometric clearance against a dense bank
                # section catches a hull intersecting land next to the flat
                # water sample directly beneath its centre.
                for index in range(-120, 121):
                    along = result.position.x+index*.05
                    normal = (math.sin(along/field.radius), 0,
                              math.cos(along/field.radius))
                    radius = field.radius+field.elevation(normal)
                    point = Vec3(*(component*radius for component in normal))
                    self.assertGreaterEqual((result.position-point).length(), 2.99)


if __name__ == '__main__':
    unittest.main()
