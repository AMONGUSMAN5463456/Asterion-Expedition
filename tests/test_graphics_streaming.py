"""Scenery streaming must reuse art while preserving the saved-world layout."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from panda3d.core import NodePath

from asterion.planetary import PlanetField, PlanetFrame
from asterion.seamless_world import SeamlessWorld, _plan_chunk
from asterion.state import GameState
from asterion.universe import generate_system


class GraphicsStreamingTests(unittest.TestCase):
    def setUp(self):
        self.render=NodePath("streaming-test-render")
        self.addCleanup(self.render.removeNode)
        self.planet=generate_system(0)["planets"][0]
        self.world=SeamlessWorld(SimpleNamespace(render=self.render))
        self.addCleanup(self.world.destroy)
        self.world.planet=self.planet
        self.world.state=GameState()
        field=PlanetField(self.planet)
        self.world.fields[field.planet_id]=field
        self.world.body_roots[field.planet_id]=self.render.attachNewNode("body")
        self.world.frame=PlanetFrame.from_planet(self.planet)
        self.world._models(self.planet)

    def test_warm_streaming_reuses_art_and_keeps_original_resource_geography(self):
        # Captured from the v1.3 geographic generator before art was changed.
        reference=(
            ("r0","crystal",28,(90.0295,-39.7924,23.4948)),
            ("r1","sodium",14,(112.7308,-39.6173,26.1498)),
            ("r2","copper",19,(89.9336,-12.7774,22.8517)),
            ("r3","crystal",24,(76.8796,-54.9547,22.9429)),
            ("r4","ferrite",22,(79.8730,-29.4279,22.2254)),
            ("r5","carbon",29,(75.1569,-13.8530,21.5349)),
            ("r6","crystal",12,(102.2049,-31.4227,24.5925)),
        )
        def unexpected(*args,**kwargs):
            self.fail("A warm chunk rebuilt immutable art in Python")
        with patch.multiple("asterion.seamless_world",flora_mesh=unexpected,
                            grass_mesh=unexpected,rock_mesh=unexpected,
                            crystal_mesh=unexpected,fauna_mesh=unexpected), \
             patch.object(self.world,"_append_decor",side_effect=unexpected):
            self.world._make_chunk((0,1,-1))
            self.world._make_chunk((0,-1,-1))
        for suffix,resource,amount,geo in reference:
            entity=self.world._entities["s0-p0:c1,-1:"+suffix]
            self.assertEqual((entity["resource"],entity["amount"]),(resource,amount))
            for actual,expected in zip(entity["geo"],geo):
                self.assertAlmostEqual(actual,expected,delta=.0002)
        self.assertIn("s0-p0:c1,-1:fauna",self.world._entities)
        for chunk in self.world.chunks.values():
            # Native flattening retains small draw batches, not one node for
            # each leaf, tuft or pebble. Interactables remain separate nodes.
            for name in ("batched-local-flora-and-stones","batched-low-ground-cover"):
                batch=chunk["root"].find("**/"+name)
                self.assertFalse(batch.isEmpty())
                self.assertLessEqual(batch.findAllMatches("**/+GeomNode").getNumPaths(),3)

    def test_stationary_stream_drains_once_without_replacing_loaded_chunks(self):
        built=[]
        def make(key):
            self.assertNotIn(key,self.world.chunks)
            built.append(key)
            self.world.chunks[key]={"marker":len(built)}
        self.world._radius=2
        with patch.object(self.world,"_make_chunk",side_effect=make):
            for _ in range(65):
                self.world._stream((0,-16,21.8))
            first=dict(self.world.chunks)
            for _ in range(20):
                self.world._stream((0,-16,21.8))
        self.assertEqual(len(built),49)
        self.assertFalse(self.world._chunk_queue)
        self.assertEqual(first,self.world.chunks)
        self.world.chunks.clear()

    def test_distant_ground_cover_builds_once_when_approached(self):
        key = (0, 3, 2)
        self.world._make_chunk(key)
        chunk = self.world.chunks[key]
        self.assertFalse(chunk["cover_ready"])
        self.world._populate_cover(key)
        self.assertTrue(chunk["cover_ready"])
        cover = chunk["cover"]
        count = cover.findAllMatches("**/+GeomNode").getNumPaths()
        self.assertGreater(count, 0)
        self.world._populate_cover(key)
        self.assertEqual(cover.findAllMatches("**/+GeomNode").getNumPaths(), count)

    def test_prefetched_chunk_does_not_restore_depleted_resources(self):
        key = (0, 1, -1)
        self.world._make_chunk(key)
        resource = next(identifier for identifier in self.world.chunks[key]["ids"]
                        if ":r" in identifier)
        node = self.world._entities[resource]["node"]
        self.world._deactivate_chunk(key)
        self.assertNotIn(resource, self.world._entities)
        self.world.set_depleted(resource)
        self.world._activate_chunk(key)
        self.assertNotIn(resource, self.world._entities)
        self.assertTrue(node.isEmpty())

    def test_staged_chunk_has_the_same_scenery_and_entities(self):
        key = (0, 1, -1)
        self.world._make_chunk(key)
        original = self.world.chunks[key]
        original_ids = tuple(original["ids"])
        original_bounds = original["root"].getTightBounds()
        original_entities = {identifier: self.world._entities[identifier]["geo"]
                             for identifier in original_ids if identifier in self.world._entities}
        self.world._remove_chunk(key)

        self.world._make_chunk(key, staged=True)
        self.assertNotIn(key, self.world.chunks)
        self.assertTrue(self.world._chunk_prefetch_pending[2]["root"].isHidden())
        self.world._finish_prefetch_chunk(visible=True)
        staged = self.world.chunks[key]
        self.assertIsNone(self.world._chunk_prefetch_pending)
        self.assertFalse(staged["root"].isHidden())
        self.assertEqual(tuple(staged["ids"]), original_ids)
        self.assertEqual(staged["root"].getTightBounds(), original_bounds)
        self.assertEqual({identifier: self.world._entities[identifier]["geo"]
                          for identifier in staged["ids"] if identifier in self.world._entities},
                         original_entities)

        self.world._remove_chunk(key)
        self.world._make_chunk(key, staged=True)
        self.world._finish_prefetch_chunk()
        self.assertTrue(self.world.chunks[key]["inactive"])
        self.assertTrue(self.world.chunks[key]["root"].isHidden())
        self.world._activate_chunk(key)
        self.assertFalse(self.world.chunks[key]["root"].isHidden())
        self.assertEqual(tuple(self.world.chunks[key]["ids"]), original_ids)

    def test_staged_ground_cover_matches_a_full_build(self):
        key = (0, 3, 2)
        self.world._make_chunk(key)
        chunk = self.world.chunks[key]
        self.world._populate_cover(key, budget=20)
        self.assertFalse(chunk["cover_ready"])
        while not chunk["cover_ready"]:
            self.world._populate_cover(key, budget=20)
        staged_bounds = chunk["cover"].getTightBounds()
        staged_count = chunk["cover"].findAllMatches("**/+GeomNode").getNumPaths()
        self.world._remove_chunk(key)
        self.world._make_chunk(key)
        self.world._populate_cover(key)
        cover = self.world.chunks[key]["cover"]
        self.assertEqual(cover.findAllMatches("**/+GeomNode").getNumPaths(), staged_count)
        self.assertEqual(cover.getTightBounds(), staged_bounds)
        self.world._remove_chunk(key)
        field = self.world.fields[self.planet["id"]]
        self.world._chunk_plans[key] = _plan_chunk(
            field, key, "medium", False, with_cover=True)
        self.world._make_chunk(key)
        while not self.world.chunks[key]["cover_ready"]:
            self.world._populate_cover(key, budget=20)
        cover = self.world.chunks[key]["cover"]
        self.assertEqual(cover.findAllMatches("**/+GeomNode").getNumPaths(), staged_count)
        self.assertEqual(cover.getTightBounds(), staged_bounds)


if __name__ == "__main__":
    unittest.main()
