"""Placement-cache regressions, including the Japes 007 -> Minecart 006 return."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from dk64_forge.core.mesh_decoder import DrawTexture
from dk64_forge.core.texture_bank import TextureUsage
from dk64_forge.core.texture_animation import TextureAnimation
from dk64_forge.level_playback import content_playback
from dk64_forge.render_data import PreparedTexture


class SharedBindingTests(unittest.TestCase):
    def prepare(self, *, different_frames=False, prop_entries=(312,16)):
        texture=PreparedTexture(0,1,1,b'\xff\xff\xff\xff','CLAMP','CLAMP')
        render=SimpleNamespace(textures=(texture,),batches=())
        first=DrawTexture(41,TextureUsage(0,3,1,1,False,None,'draw'),2,2,7)
        second=replace(first,usage=replace(first.usage,user='prop quad'))
        animation=TextureAnimation(41,(41,42),2,7,0,False)
        other=replace(animation,key=99,group=3,frames=(41,43) if different_frames else animation.frames)
        a,b=SimpleNamespace(render=render),SimpleNamespace(render=render)
        models={('prop',312,0):a,('prop',16,0):b}
        def bindings(model,_animations):
            return {0:(first,animation) if model is a else (second,other)}
        with patch('dk64_forge.level_playback.texture_bank.table_entry',return_value=b''), \
             patch('dk64_forge.level_playback.texture_animation.prop_animations',return_value=()), \
             patch('dk64_forge.level_playback.bindings_for',side_effect=bindings):
            return content_playback(render,models,SimpleNamespace(rom=b''),prop_entries=set(prop_entries))

    def test_equivalent_animation_ignores_provenance_labels(self):
        self.assertEqual(len(self.prepare().bindings),1)

    def test_unused_cached_prop_cannot_introduce_an_animation_conflict(self):
        self.assertEqual(len(self.prepare(different_frames=True,prop_entries=(312,)).bindings),1)

    def test_real_conflicting_sequences_are_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'Ambiguous shared prop texture binding'):
            self.prepare(different_frames=True)


@unittest.skipUnless(Path('local/roms/dk64_us.n64').is_file(),'local supported ROM unavailable')
class LevelReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_all_maps_with_shared_cache_and_independent_identical_start_frames(self):
        from dk64_forge.core.rom_model import normalize_rom
        from dk64_forge.static_model import TextureCache
        from dk64_forge.level_content import content_render
        from dk64_forge.model_browser import catalog,KIND_MAP
        rom=normalize_rom(Path('local/roms/dk64_us.n64').read_bytes())[0]
        cache=TextureCache(rom)
        models={}
        entries=catalog(rom,(KIND_MAP,))
        self.assertEqual(len(entries),136)
        for entry in entries:
            with self.subTest(map=entry.index):
                render,rows,_=content_render(rom,entry.index,cache,models=models)
                if render is not None:
                    playback=content_playback(render,models,cache,
                        prop_entries={r.type_id for r in rows if r.kind=='prop'})
                    self.assertEqual(len(playback.render(30).positions),len(render.positions))
        # Lighthouse prop 13D has two equal starting images, but separate clips.
        render,rows,_=content_render(rom,49,cache,models=models)
        playback=content_playback(render,models,cache,
            prop_entries={r.type_id for r in rows if r.kind=='prop'})
        eye_bindings={source.image:(index,source,animation)
                      for index,(source,animation) in playback.bindings.items()
                      if source.image in (633,634)}
        self.assertEqual(set(eye_bindings),{633,634})
        self.assertNotEqual(eye_bindings[633][0],eye_bindings[634][0])
        frame=playback.render(2)
        for index,source,animation in eye_bindings.values():
            self.assertEqual(frame.textures[index].rgba,
                             cache.pixels(replace(source,image=animation.image(1))))

    def test_japes_minecart_return_toggle_and_rapid_selection(self):
        from dk64_forge.session import load_rom
        from dk64_forge.model_browser import ModelBrowserTab,BrowserEntry,KIND_MAP
        rom=load_rom(Path('local/roms/dk64_us.n64')).normalized
        tab=ModelBrowserTab(rom,(KIND_MAP,),'Levels')
        errors=[]
        tab._content_loader.failed.connect(errors.append)
        tab._entries=[BrowserEntry(KIND_MAP,7,'Japes','ROM'),BrowserEntry(KIND_MAP,6,'Japes Minecart','ROM')]
        tab.loaded=True
        tab._refresh_list()
        def wait():
            deadline=time.monotonic()+40
            while tab._loader.pending or tab._content_loader.pending:
                self.app.processEvents()
                if time.monotonic()>deadline:
                    self.fail('Placement loading timed out')
                time.sleep(.005)
            self.app.processEvents()
        def check_japes():
            self.assertEqual(tab._current[0].index,7)
            self.assertTrue(tab.content_check.isChecked())
            self.assertEqual(tab.objects_list.count(),507)
            self.assertEqual(tab._visible_objects,original_keys)
            self.assertIsNotNone(tab.viewport._attachment_data)
            self.assertEqual(len(tab.viewport._attachment_data.positions),original_vertices)
            self.assertEqual(errors,[])
        try:
            tab.list_widget.setCurrentRow(0);wait()
            tab.content_check.setChecked(True);wait()
            original_keys=tab._visible_objects.copy()
            original_vertices=len(tab.viewport._attachment_data.positions)
            tab.list_widget.setCurrentRow(1);wait()
            self.assertIsNotNone(tab.viewport._attachment_data)
            tab.list_widget.setCurrentRow(0);wait();check_japes()
            tab.content_check.setChecked(False)
            self.assertIsNone(tab.viewport._attachment_data)
            tab.content_check.setChecked(True);wait();check_japes()
            # Supersede a map request before its placement work finishes.
            tab.list_widget.setCurrentRow(1)
            tab.content_check.setChecked(False)
            tab.list_widget.setCurrentRow(0)
            tab.content_check.setChecked(True)
            wait();check_japes()
        finally:
            for loader in (tab._loader,tab._content_loader,tab._clip_loader):
                loader.cancel();loader.pool.waitForDone()
            tab.close()


if __name__=='__main__': unittest.main()
