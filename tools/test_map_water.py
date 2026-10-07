"""Water parser, ROM surface playback and current-pose export regression checks."""
from dataclasses import replace
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from dk64_forge.core import map_water, texture_bank
from dk64_forge.core.rom_model import normalize_rom
from dk64_forge.static_model import TextureCache, map_model, export_glb
from dk64_forge.level_playback import map_playback


def fixture():
    data = bytearray(0x78+4+0x6C)
    struct.pack_into('>I',data,0x4C,0x78)
    struct.pack_into('>I',data,0x78,1)
    at = 0x7C
    struct.pack_into('>f',data,at,2.)
    struct.pack_into('>6h',data,at+0x44,100,100,200,350,450,60)
    data[at+0x61:at+0x68] = bytes((255,255,150,180,50,0,1))
    return data


class WaterParserTests(unittest.TestCase):
    def test_grid_ends_at_record_bounds_and_faces_up(self):
        surface, = map_water.records(fixture())
        points = map_water.grid(surface)
        self.assertEqual((tuple(points.min(axis=0)),tuple(points.max(axis=0))),((100,200),(350,450)))
        self.assertEqual(len(points),54)
        xyz = np.column_stack((points[:,0],np.zeros(len(points)),points[:,1])).reshape(-1,3,3)
        normals = np.cross(xyz[:,1]-xyz[:,0],xyz[:,2]-xyz[:,0])
        self.assertTrue(np.all(normals[:,1] > 0))

    def test_rejects_truncation_invalid_bounds_spacing_and_chunk_count(self):
        for name,alter in (
            ('offset',lambda d: struct.pack_into('>I',d,0x4C,len(d))),
            ('count',lambda d: struct.pack_into('>I',d,0x78,2)),
            ('step',lambda d: struct.pack_into('>h',d,0x7C+0x44,0)),
            ('bounds',lambda d: struct.pack_into('>h',d,0x7C+0x4A,100)),
            ('chunks',lambda d: d.__setitem__(0x7C+0x67,9)),
            ('nonfinite',lambda d: struct.pack_into('>f',d,0x7C,float('nan'))),
        ):
            with self.subTest(name=name):
                data=fixture();alter(data)
                with self.assertRaises(ValueError): map_water.records(data)
        with self.assertRaises(ValueError): map_water.records(b'')

    def test_fractional_scroll_matches_float32_subtract_and_reset(self):
        value = np.float32(0.)
        for tick in range(1800):
            self.assertEqual(map_water.scroll_origin(0.,.3,tick),int(value))
            value = np.float32(value-np.float32(.3))
            if value < 0: value=np.float32(255.)


ROM = Path('local/roms/dk64_us.n64')


@unittest.skipUnless(ROM.is_file(),'local supported ROM unavailable')
class WaterRomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rom = normalize_rom(ROM.read_bytes())[0]
        cls.cache = TextureCache(cls.rom)
        cls.model = map_model(cls.rom,7,cls.cache)

    def test_japes_original_five_surfaces_material_texture_and_height(self):
        self.assertEqual(len(self.model.water_bindings),5)
        self.assertEqual([s.height for s,_,_ in self.model.water_bindings],[260,260,260,260,204])
        self.assertEqual(self.model.triangles,6194+278)
        self.assertEqual(self.model.missing_textures,0)
        batches = self.model.render.batches[-5:]
        self.assertTrue(all(b.alpha_mode=='BLEND' and not b.depth_write and b.double_sided for b in batches))
        self.assertTrue(all(self.model.texture_sources[b.texture_index].table==7 and
                            self.model.texture_sources[b.texture_index].image==0x3C5 for b in batches))
        self.assertTrue(all(b.material.cycle==1 and b.texture1_index==b.texture_index for b in batches))
        table=map_water.wave_table(self.rom)
        np.testing.assert_allclose(map_water.sine([0,1024,2048,3072],table),[0,1,0,-1],atol=1e-7)

    def test_cached_playback_matches_full_decode_without_touching_terrain(self):
        playback=map_playback(self.rom,7,self.model,self.cache)
        water=set(i for _,indices,_ in self.model.water_bindings for i in indices)
        terrain=[i for i in range(len(self.model.render.positions)) if i not in water]
        for tick in (0,1,30,255,853,1800):
            with self.subTest(tick=tick):
                frame=playback.render(tick)
                full=map_model(self.rom,7,self.cache,tick=tick).render
                for field in ('positions','colors','uvs','uvs1'):
                    np.testing.assert_array_equal(getattr(frame,field),getattr(full,field))
                    np.testing.assert_array_equal(np.asarray(getattr(frame,field))[terrain],
                                                  np.asarray(getattr(self.model.render,field))[terrain])
                self.assertEqual(frame.batches,full.batches)
                self.assertEqual(frame.textures,full.textures)
                self.assertIs(playback.render(tick),frame)
        self.assertNotEqual(playback.render(30).positions,self.model.render.positions)
        self.assertNotEqual(playback.render(30).uvs,self.model.render.uvs)
        self.assertNotEqual(playback.render(30).uvs1,self.model.render.uvs1)

    def test_other_levels_and_chunk_filter(self):
        for map_id,expected in ((4,0),(48,13),(72,7),(108,3)):
            with self.subTest(map=map_id):
                model=map_model(self.rom,map_id,self.cache)
                self.assertEqual(len(model.water_bindings),expected)
                self.assertEqual(model.missing_textures,0)
                frame=map_playback(self.rom,map_id,model,self.cache).render(30)
                full=map_model(self.rom,map_id,self.cache,tick=30).render
                np.testing.assert_array_equal(frame.positions,full.positions)
                np.testing.assert_array_equal(frame.uvs,full.uvs)
        for chunk in (0,1,2):
            model=map_model(self.rom,7,self.cache,chunks={chunk})
            self.assertTrue(all(chunk in s.chunks for s,_,_ in model.water_bindings))

    def test_current_pose_glb_contains_water_vertices_and_transparency(self):
        import json
        frame=map_playback(self.rom,7,self.model,self.cache).render(30)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'japes.glb'
            export_glb(replace(self.model,render=frame),path,'Japes')
            raw=path.read_bytes()
            size,kind=struct.unpack_from('<2I',raw,12)
            document=json.loads(raw[20:20+size])
            self.assertEqual(kind,0x4E4F534A)
            primitives=document['meshes'][0]['primitives']
            self.assertEqual(sum(document['accessors'][p['attributes']['POSITION']]['count'] for p in primitives),
                             len(frame.positions))
            self.assertTrue(any(m.get('alphaMode')=='BLEND' for m in document['materials']))

    def test_viewport_partial_update_matches_full_upload_and_retains_terrain(self):
        from PySide6.QtWidgets import QApplication
        from dk64_forge.viewport import ModelViewport, _vertex_array
        from dk64_forge.static_model import marker_skeleton
        app=QApplication.instance() or QApplication([])
        widget=ModelViewport(self.model.render,marker_skeleton(self.model.render))
        original=widget._vertex_data.copy()
        playback=map_playback(self.rom,7,self.model,self.cache)
        frame=playback.render(30)
        widget.set_dynamic_render(frame,vertex_spans=playback.vertex_spans)
        np.testing.assert_array_equal(widget._vertex_data,_vertex_array(frame))
        first=playback.vertex_spans[0][0]
        np.testing.assert_array_equal(widget._vertex_data[:first],original[:first])
        self.assertIs(widget._data,frame)
        with self.assertRaises(ValueError):
            widget.set_dynamic_render(frame,vertex_spans=((0,len(frame.positions)+1),))
        widget.close()


if __name__=='__main__': unittest.main()
