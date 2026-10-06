"""Versioned, ROM-bound viewer sessions; no ROM data is written to JSON."""
import json, math, os
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from .background import Loader

def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(data,indent=2,allow_nan=False),encoding="utf-8")
    os.replace(temporary,path)

def read_session(path,sha):
    path=Path(path)
    if path.stat().st_size>2_000_000:raise ValueError("Session file is too large")
    data=json.loads(path.read_text(encoding="utf-8"),parse_constant=lambda x:(_ for _ in ()).throw(ValueError("Nonfinite session value")))
    if not isinstance(data,dict) or data.get("version")!=1 or data.get("rom")!=sha:
        raise ValueError("Session version or ROM fingerprint does not match")
    return data

def camera_state(view):
    if view is None:return None
    c=view._camera
    return {k:getattr(c,k) for k in ("target","yaw","pitch","distance","scene_radius")}

def restore_camera(view,data):
    if view is None or not isinstance(data,dict):return
    target=data.get("target")
    if not isinstance(target,(list,tuple)) or len(target)!=3:return
    values=[*target,*[data.get(k) for k in ("yaw","pitch","distance","scene_radius")]]
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and abs(v)<1e9 for v in values):return
    if values[-2]<=0 or values[-1]<=0:return
    view._camera.target=tuple(target)
    for k in ("yaw","pitch","distance","scene_radius"):setattr(view._camera,k,data[k])
    view.update()

def browser_state(tab):
    current=tab._current[0] if tab._current else None
    return dict(entry=[current.kind,current.index] if current else None,search=tab.search_edit.text(),
        clip_search=tab.clip_combo.search_edit.text() if hasattr(tab.clip_combo,"search_edit") else "",
        kind=tab.kind_combo.currentData(),objects=tab.object_search.text(),category=tab.object_kind.currentData(),
        content=tab.content_check.isChecked(),night=tab.night_check.isChecked(),fog=tab.fog_check.isChecked(),
        selected=[list(i.data(Qt.ItemDataRole.UserRole)) for i in tab.objects_list.selectedItems()],
        clip=tab.clip_combo.currentData(),frame=tab.clip_frame.value(),tick=tab._scene_tick,
        interpolate=tab.interpolate_check.isChecked(),unassigned=tab.unassigned_check.isChecked(),
        camera=camera_state(tab.viewport))

def capture(window):
    w=window;t=w.texture_tab
    return dict(version=1,rom=w.source.sha256,tab=w.tabs.currentIndex(),model_tab=w.model_tabs.currentIndex(),favorites=[c["only"].isChecked() for c in w.clip_tools.controls],
        character=dict(key=w.source.character.key,variant=w.source.character.variant,clip=w._selected_animation_id,
            frame=w._current_frame,mouth=w.mouth_slider.value(),eye=w.eye_combo.currentIndex(),
            clothing=w.texture_frame_spin.value(),blink=w.auto_blink_check.isChecked(),
            interpolate=w.interpolate_check.isChecked(),owned=w.dk_only_check.isChecked(),
            timing=w.timing_mode_combo.currentData(),speed=w.speed_slider.value(),search=w.animation_combo.search_edit.text(),camera=camera_state(w.viewport)),
        models=browser_state(w.models_tab),levels=browser_state(w.levels_tab),
        textures=dict(connected=dict(tab=t.view_tabs.currentIndex(),browse=t.assembly_panel.browse.isChecked(),
            search=t.assembly_panel.combo.search_edit.text(),key=t.assembly_panel.combo.currentData(),zoom=t.assembly_panel.zoom,
            rotate_preview=t.assembly_panel.rotate_preview_check.isChecked()),bank=t.bank_combo.currentData(),search=t.search_edit.text(),show=t.show_combo.currentData(),
            rotate_preview=t.rotate_preview_check.isChecked(),
            zoom=t._preview_zoom,sort=t.sort_combo.currentData(),entry=t._current.index if t._current else None))

def combo_value(combo,value):
    index=combo.findData(value)
    if index>=0:combo.setCurrentIndex(index)

class Restore:
    """Stage restoration around asynchronous loaders; stale sessions cannot race them."""
    def __init__(self,window,data):
        self.w=window;self.data=data;self.stage=0;self.polls=0
        self.timer=QTimer(window);self.timer.setInterval(30);self.timer.timeout.connect(self.step)
        window._pause();window.models_tab.pause();window.levels_tab.pause()
        self.timer.start()
    def step(self):
        w=self.w;self.polls+=1
        if self.polls>4000:self.stop("Session restore timed out");return
        if any(l.pending for l in w.findChildren(Loader)):return
        try:self.advance()
        except (KeyError,ValueError,TypeError,AttributeError,IndexError,OverflowError,OSError) as exc:self.stop(f"Session could not be restored: {exc}")
    def advance(self):
        w=self.w;d=self.data
        if self.stage==0:
            from .characters import CHARACTERS
            c=d.get("character",{})
            if c.get("key") in CHARACTERS:w.character_combo.setCurrentIndex(list(CHARACTERS).index(c["key"]))
            combo_value(w.variant_combo,c.get("variant"));w.dk_only_check.setChecked(bool(c.get("owned")))
            combo_value(w.animation_combo,c.get("clip"));self.stage=1;return
        if self.stage in (1,3):
            if self.stage==1:
                c=d.get("character",{})
                w._pause();w.time_slider.setValue(int(c.get("frame",0)));w.mouth_slider.setValue(int(c.get("mouth",0)))
                w.texture_frame_spin.setValue(int(c.get("clothing",0)));w.auto_blink_check.setChecked(bool(c.get("blink")))
                w.eye_combo.setCurrentIndex(int(c.get("eye",0)));w.interpolate_check.setChecked(bool(c.get("interpolate")))
                combo_value(w.timing_mode_combo,c.get("timing"))
                w.speed_slider.setValue(int(c.get("speed",10)))
                w.animation_combo.search_edit.setText(str(c.get("search","")));restore_camera(w.viewport,c.get("camera"))
                tab=w.models_tab;state=d.get("models",{})
            else:tab=w.levels_tab;state=d.get("levels",{})
            if state.get("entry"):
                from .model_browser import BrowserEntry
                tab.ensure_loaded();kind,index=state["entry"]
                if kind not in tab._kinds or not 0<=int(index)<2000:raise ValueError("Invalid saved model entry")
                entry = next((e for e in tab._entries if e.kind == kind and e.index == int(index)), None)
                if entry is None:raise ValueError("Saved model entry is unavailable")
                tab._content_loader.cancel();tab._loader.submit(lambda progress:tab._prepare_entry(entry,progress),lambda result:tab.show_entry(entry,prepared=result))
            self.stage+=1;return
        if self.stage in (2,4):
            tab=w.models_tab if self.stage==2 else w.levels_tab;state=d.get("models" if self.stage==2 else "levels",{})
            tab.pause();tab.interpolate_check.setChecked(bool(state.get("interpolate")));tab.unassigned_check.setChecked(bool(state.get("unassigned",True)))
            combo_value(tab.clip_combo,state.get("clip"))
            if hasattr(tab.clip_combo,"search_edit"):tab.clip_combo.search_edit.setText(str(state.get("clip_search","")))
            tab.clip_frame.setValue(int(state.get("frame",0)))
            combo_value(tab.kind_combo,state.get("kind"));tab.search_edit.setText(str(state.get("search","")))
            tab.night_check.setChecked(bool(state.get("night")));tab.fog_check.setChecked(bool(state.get("fog")))
            tab.content_check.setChecked(bool(state.get("content")))
            self.stage+=1
            return
        if self.stage==5:
            t=w.texture_tab;state=d.get("textures",{})
            if state.get("entry") is not None or d.get("tab")==3:
                combo_value(t.bank_combo,state.get("bank",25));t.ensure_loaded()
            self.stage=6;return
        if self.stage==6:
            for name,tab in (("models",w.models_tab),("levels",w.levels_tab)):
                state=d.get(name,{})
                if tab._current and state.get("tick",0) and tab._current[0].kind=="map":
                    tab._advance_scene(max(0,min(int(state["tick"]),1_000_000)))
                tab.object_search.setText(str(state.get("objects","")));combo_value(tab.object_kind,state.get("category","all"))
                selected={tuple(k) for k in state.get("selected",[])}
                for i in range(tab.objects_list.count()):
                    item=tab.objects_list.item(i);item.setSelected(not item.isHidden() and item.data(Qt.ItemDataRole.UserRole) in selected)
                restore_camera(tab.viewport,state.get("camera"));tab.pause()
            t=w.texture_tab;state=d.get("textures",{})
            combo_value(t.show_combo,state.get("show"));combo_value(t.sort_combo,state.get("sort"));t.search_edit.setText(str(state.get("search","")))
            for i in range(t.list_widget.count()):
                item=t.list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole)==state.get("entry"):t.list_widget.setCurrentItem(item);break
            zoom=state.get("zoom",1.)
            if isinstance(zoom,(int,float)) and math.isfinite(zoom):
                t._preview_zoom=max(1/16,min(float(zoom),8.));t._render_preview()
            t.rotate_preview_check.setChecked(bool(state.get("rotate_preview",False)))
            connected=state.get("connected",{})
            t.assembly_panel.browse.setChecked(bool(connected.get("browse")))
            combo_value(t.assembly_panel.combo,connected.get("key"))
            t.assembly_panel.combo.search_edit.setText(str(connected.get("search","")))
            assembly_zoom=connected.get("zoom",1.)
            if isinstance(assembly_zoom,(int,float)) and math.isfinite(assembly_zoom):
                t.assembly_panel.zoom=max(1/16,min(float(assembly_zoom),8.));t.assembly_panel.render()
            t.assembly_panel.rotate_preview_check.setChecked(bool(connected.get("rotate_preview",False)))
            t.view_tabs.setCurrentIndex(max(0,min(int(connected.get("tab",0)),1)))
            for c,value in zip(w.clip_tools.controls,d.get("favorites",[])):c["only"].setChecked(bool(value))
            w.clip_tools.refresh()
            w.model_tabs.setCurrentIndex(max(0,min(int(d.get("model_tab",0)),1)))
            w.tabs.setCurrentIndex(max(0,min(int(d.get("tab",0)),3)))
            w._pause();w.models_tab.pause();w.levels_tab.pause();self.stop("Session restored (paused)")
    def stop(self,message):
        self.timer.stop();self.timer.deleteLater();self.w.statusBar().showMessage(message,15000)
        if getattr(self.w,"_session_restore",None) is self:self.w._session_restore=None
