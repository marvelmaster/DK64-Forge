"""Two independent pose previews sharing a normalized timeline."""
import copy, time
from dataclasses import replace
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QWidget,QComboBox,QCheckBox,QLabel,QSlider
from .background import Loader
from .viewport import ModelViewport
from .debug_view import ViewMode
from .dropdown_search import add_dropdown_search
from . import static_model

class CompareDialog(QDialog):
    def __init__(self,parent,choices,make_clip,current=None):
        super().__init__(parent);self.setWindowTitle("Compare animation clips");self.resize(1300,850)
        self.make_clip=make_clip;self.panes=[];self.phase=0.;self.last=time.monotonic()
        layout=QVBoxLayout(self);layout.addWidget(QLabel("Compare two clips at the same point in their animation cycle."))
        row=QHBoxLayout();layout.addLayout(row)
        for slot in range(2):
            host=QWidget();column=QVBoxLayout(host);combo=QComboBox()
            for label,key in choices:combo.addItem(label,key)
            column.addWidget(combo)
            status=QLabel("Select a clip");column.addWidget(status)
            viewport=ModelViewport(parent.viewport._data if hasattr(parent,"viewport") and parent.viewport is not None else parent.models_tab.viewport._data,static_model.marker_skeleton(parent.viewport._data if hasattr(parent,"viewport") and parent.viewport is not None else parent.models_tab.viewport._data))
            viewport.set_view_mode(ViewMode.MESH);column.addWidget(viewport,1);row.addWidget(host)
            loader=Loader(self);loader.failed.connect(status.setText)
            pane=dict(combo=combo,view=viewport,status=status,loader=loader,clip=None);self.panes.append(pane)
            combo.currentIndexChanged.connect(lambda index,pane=pane:self.load(pane))
            add_dropdown_search((combo,))
        controls=QHBoxLayout();layout.addLayout(controls)
        self.play=QCheckBox("Play together");self.play.setChecked(True);controls.addWidget(self.play)
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,1000);controls.addWidget(self.slider,1)
        self.slider.valueChanged.connect(self.scrub)
        self.timer=QTimer(self);self.timer.setInterval(16);self.timer.timeout.connect(self.tick);self.timer.start()
        for i,pane in enumerate(self.panes):
            index=pane["combo"].findData(current)
            pane["combo"].setCurrentIndex(index if i==0 and index>=0 else min(i,pane["combo"].count()-1))
            self.load(pane)
    def load(self,pane):
        key=pane["combo"].currentData()
        if key is None:return
        pane["clip"]=None
        pane["status"].setText("Loading clip…")
        pane["loader"].submit(lambda progress:self.make_clip(key),lambda clip:self.loaded(pane,clip))
    def loaded(self,pane,clip):
        pane["clip"]=clip;render,skeleton=clip["frame"](0)
        pane["view"].set_model_data(render,skeleton);pane["view"].set_view_mode(ViewMode.MESH if render.batches else ViewMode.SKELETON)
        if not render.batches:pane["view"].focus_points(skeleton.joint_positions)
        pane["status"].setText(f'{clip["count"]} stored samples / ticks')
        self.draw()
    def scrub(self,value):
        self.phase=value/1000.;self.draw()
    def tick(self):
        now=time.monotonic();elapsed=now-self.last;self.last=now
        if not self.play.isChecked():return
        count=next((p["clip"]["count"] for p in self.panes if p["clip"]),30)
        self.phase=(self.phase+elapsed*30/max(1,count))%1
        self.slider.blockSignals(True);self.slider.setValue(round(self.phase*1000));self.slider.blockSignals(False)
        self.draw()
    def draw(self):
        for pane in self.panes:
            if pane["clip"]:
                tick=min(pane["clip"]["count"]-1,int(self.phase*pane["clip"]["count"]))
                render,skeleton=pane["clip"]["frame"](tick)
                pane["view"].set_dynamic_render(render)
                pane["view"].set_scene_data(render.positions,skeleton)
    def done(self,result):
        self.timer.stop()
        for p in self.panes:p["loader"].cancel()
        super().done(result)

    def closeEvent(self,event):
        self.timer.stop()
        for p in self.panes:p["loader"].cancel()
        super().closeEvent(event)

def character_factory(window):
    source=window.source
    def make(key):
        from .preview_data import PreviewScene
        scene=PreviewScene.from_animation(source,key)
        def frame(tick):
            positions,skeleton=scene.pose(scene.safe_first+tick)
            return replace(scene.render_data,positions=positions,colors=scene.shade_colors()),skeleton
        return dict(count=scene.safe_last-scene.safe_first+1,frame=frame)
    return make

def model_factory(tab):
    entry,model=tab._current;rom=tab._rom
    if entry.kind=="actor":
        original=tab._actor_animations
        def make(key):
            animation=copy.copy(original);animation.samples=();animation._matrix_cache={};animation.select(key)
            def frame(tick):return replace(model.render,positions=animation.pose(tick),colors=animation.colors(tick)),animation.skeleton_debug(tick)
            return dict(count=len(animation.samples),frame=frame)
        return make
    def make(key):
        from .core import prop_animation,texture_bank
        from .prop_export import cycle_ticks
        rig=prop_animation.parse(texture_bank.table_entry(rom,4,entry.index))
        track=next(t for t in rig.tracks if t.index==key);cache=static_model.TextureCache(rom)
        def frame(tick):
            render=static_model.prop_model(rom,entry.index,cache,tick=tick,track=key).render
            return render,static_model.marker_skeleton(render)
        return dict(count=cycle_ticks(track),frame=frame)
    return make
