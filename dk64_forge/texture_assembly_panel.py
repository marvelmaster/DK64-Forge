"""Connected-texture viewer: reviewed contiguous flat images."""
import json,re
from pathlib import Path
from PySide6.QtCore import Qt,QEvent,QSignalBlocker,Signal
from PySide6.QtGui import QImage,QPixmap
from PySide6.QtWidgets import QWidget,QVBoxLayout,QCheckBox,QComboBox,QLabel,QScrollArea,QListWidget,QListWidgetItem,QPushButton,QFileDialog,QMessageBox
from . import texture_assemblies as assemblies
from .core.texture_bank import rgba_png
from .dropdown_search import add_dropdown_search

class AssemblyPanel(QWidget):
    open_piece=Signal(int,int)
    open_user=Signal(str,int)
    def __init__(self,rom):
        super().__init__();self.rom=rom;self.sets=();self.lookup={};self.image=None;self.current=None;self.rgba=None;self.zoom=1.;self.missing=()
        layout=QVBoxLayout(self)
        self.browse=QCheckBox('Browse all connected images in this bank');layout.addWidget(self.browse)
        self.combo=QComboBox();layout.addWidget(self.combo);add_dropdown_search((self.combo,))
        self.description=QLabel();self.description.setWordWrap(True);layout.addWidget(self.description)
        self.open_model=QPushButton("Open source model / level");self.open_model.setEnabled(False);layout.addWidget(self.open_model)
        self.open_model.clicked.connect(self.open_source)
        self.preview=QLabel('Select a texture to see its connected parts.');self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setWidget(self.preview);self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll,1)
        self.parts=QListWidget();self.parts.setMaximumHeight(120);self.parts.setToolTip('Double-click a piece to open its original texture.');layout.addWidget(self.parts)
        self.export=QPushButton('Export PNG + layout…');self.export.setEnabled(False);layout.addWidget(self.export)
        self.browse.toggled.connect(self.refresh);self.combo.currentIndexChanged.connect(self.select)
        self.parts.itemDoubleClicked.connect(lambda item:self.open_piece.emit(*item.data(Qt.ItemDataRole.UserRole)))
        self.export.clicked.connect(self.export_current)
    def configure(self,items,table):
        self.lookup={i.index:i for i in items};self.sets=assemblies.catalog(items,table) if items else ();self.table=table;self.image=None;self.refresh()
    def set_image(self,image):
        self.image=image;self.refresh()
    def refresh(self,*args):
        rows=self.sets if self.browse.isChecked() else tuple(a for a in self.sets if any(p.table==self.table and p.image==self.image for p in a.pieces))
        keep=self.combo.currentData()
        with QSignalBlocker(self.combo),QSignalBlocker(self.combo.model()):
            self.combo.clear()
            for spec in rows:self.combo.addItem(spec.name,spec.key)
            index=self.combo.findData(keep);self.combo.setCurrentIndex(index if index>=0 else (0 if rows else -1))
        self.combo.refresh_search();self.select()
    def select(self,*args):
        key=self.combo.currentData();self.current=next((s for s in self.sets if s.key==key),None)
        self.rgba=None;self.zoom=1.;self.parts.clear();self.export.setEnabled(False);self.open_model.setEnabled(False)
        self.preview.clear();self.preview.setMinimumSize(0,0)
        if self.current is None:
            self.description.setText('No reviewed connected image for this texture. Use Browse all to explore verified artwork.')
            return
        s=self.current
        self.open_model.setEnabled(bool(s.users))
        kind='Verified connected image'
        self.description.setText(f'{kind} · {s.width} × {s.height} · {len(s.pieces)} parts')
        self.description.setToolTip(s.evidence)
        for p in s.pieces:
            item=QListWidgetItem(f'T{p.table} · {p.image:04X} · {p.width} × {p.height} · position ({p.x}, {p.y})')
            item.setData(Qt.ItemDataRole.UserRole,(p.table,p.image));self.parts.addItem(item)
        if self.isVisible():self.decode()
    def decode(self):
        if self.current is None:return
        try:
            w,h,pixels,self.missing=assemblies.compose(self.rom,self.current,self.lookup)
            self.rgba=(w,h,pixels);self.export.setEnabled(True);self.render()
            if self.missing:self.description.setText(self.description.text()+f' · {len(self.missing)} undecoded parts shown in magenta')
        except ValueError as exc:self.preview.setText(str(exc))
    def showEvent(self,event):
        super().showEvent(event)
        if self.rgba is None:self.decode()
    def render(self):
        if self.rgba is None:return
        w,h,pixels=self.rgba;image=QImage(pixels,w,h,w*4,QImage.Format.Format_RGBA8888).copy()
        scale=max(1,384//max(w,h))*self.zoom
        pix=QPixmap.fromImage(image).scaled(max(1,min(4096,round(w*scale))),max(1,min(4096,round(h*scale))),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation)
        self.preview.setPixmap(pix);self.preview.setMinimumSize(pix.size())
    def eventFilter(self,watched,event):
        if watched is self.scroll.viewport() and event.type()==QEvent.Type.Wheel and self.rgba:
            delta=event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.zoom=max(1/16,min(8.,self.zoom*1.25**max(-4,min(4,delta/120))));self.render();event.accept();return True
        return super().eventFilter(watched,event)
    def open_source(self):
        if self.current and self.current.users:
            kind,index=self.current.users[0].split()
            self.open_user.emit(kind,int(index))

    def write_export(self,path):
        if self.rgba is None or self.current is None:raise ValueError('No assembled image selected')
        path=Path(path);w,h,pixels=self.rgba
        path.write_bytes(rgba_png(w,h,pixels))
        metadata=assemblies.layout(self.current,self.missing)
        path.with_suffix('.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    def export_current(self):
        if self.current is None:return
        stem=re.sub(r'[^A-Za-z0-9_-]+','_',self.current.name).strip('_')
        path,_=QFileDialog.getSaveFileName(self,'Export connected texture',f'T{self.current.table}_{stem}.png','PNG (*.png)')
        if path:
            try:self.write_export(path)
            except (ValueError,OSError) as exc:QMessageBox.warning(self,'Texture export failed',str(exc))
