"""Personal animation controls for both model browsers."""
from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import QPushButton, QCheckBox, QDialog, QVBoxLayout, QLineEdit, QDialogButtonBox, QLabel, QMessageBox
from .clip_library import ClipLibrary
from .clip_compare import CompareDialog, character_factory, model_factory

BASE_ROLE = Qt.ItemDataRole.UserRole + 10

class ClipTools:
    def __init__(self, window, path):
        self.window = window
        self.library = ClipLibrary(path)
        self.dialogs = []
        self.controls = []
        self.timer = QTimer(window)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        for combo in (window.animation_combo, window.models_tab.clip_combo):
            layout = combo.parentWidget().layout()
            compare = QPushButton("Compare clips…")
            edit = QPushButton("Name / associate selected clip…")
            favorite = QCheckBox("Favorite")
            only = QCheckBox("Only favorites")
            for widget in (compare, edit, favorite, only):layout.addWidget(widget)
            control = dict(combo=combo, compare=compare, edit=edit, favorite=favorite, only=only)
            self.controls.append(control)
            compare.clicked.connect(lambda checked=False, c=control:self.compare(c))
            edit.clicked.connect(lambda checked=False, c=control:self.edit(c))
            favorite.toggled.connect(lambda value, c=control:self.favorite(c,value))
            only.toggled.connect(lambda value:self.refresh())
            combo.currentIndexChanged.connect(lambda *args:self.timer.start(0))
            combo.model().rowsInserted.connect(lambda *args:self.timer.start(0))
            combo.model().modelReset.connect(lambda *args:self.timer.start(0))
        self.refresh()

    def scope(self, control):
        w=self.window
        if control['combo'] is w.animation_combo:
            return f"character:{w.source.character.key}:{w.source.character.variant}"
        if w.models_tab._current:
            entry=w.models_tab._current[0]
            return f"{entry.kind}:{entry.index}"
        return "none"

    def refresh(self):
        for c in self.controls:
            combo=c['combo'];scope=self.scope(c);associations=set();favorites=set()
            with QSignalBlocker(combo), QSignalBlocker(combo.model()):
                for row in range(combo.count()):
                    key=combo.itemData(row)
                    if key is None:continue
                    base=combo.itemData(row,BASE_ROLE)
                    if base is None:
                        base=combo.itemText(row);combo.setItemData(row,base,BASE_ROLE)
                    item=self.library.get(scope,key)
                    if item.get('associated'):associations.add(key)
                    if item.get('favorite'):favorites.add(key)
                    label=('User association: ' if item.get('associated') else 'User name: ')+item['name'] if item.get('name') else base
                    if item.get('associated') and not item.get('name'):label='User association: '+base
                    combo.setItemText(row,('★ ' if key in favorites else '')+label)
            combo.update()
            combo.user_associations=associations
            combo.library_visible=lambda row,combo=combo,c=c,favorites=favorites:not c['only'].isChecked() or combo.itemData(row) in favorites
            if combo is self.window.models_tab.clip_combo:self.window.models_tab._filter_clips()
            if hasattr(combo,'refresh_search'):combo.refresh_search()
            key=combo.currentData();enabled=key is not None and scope!='none'
            c['edit'].setEnabled(enabled);c['favorite'].setEnabled(enabled)
            with QSignalBlocker(c['favorite']):c['favorite'].setChecked(key in favorites)
            c['compare'].setEnabled(enabled and combo.count()>1)

    def save(self, c, **values):
        key=c['combo'].currentData()
        if key is None:return
        try:self.library.set(self.scope(c),key,**values)
        except OSError as exc:QMessageBox.warning(self.window,'Could not save clip library',str(exc))
        if c['combo'] is self.window.animation_combo and self.window.dk_only_check.isChecked():
            self.window._filter_animations(True)
        self.refresh()

    def favorite(self,c,value):self.save(c,favorite=value)

    def edit(self,c):
        item=self.library.get(self.scope(c),c['combo'].currentData())
        dialog=QDialog(self.window);dialog.setWindowTitle('Personal clip label')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Saved for this model and ROM. Personal associations do not establish original game ownership.'))
        name=QLineEdit(item.get('name',''));name.setMaxLength(120);name.setPlaceholderText('Personal name');layout.addWidget(name)
        associated=QCheckBox('Associate this clip with this model');associated.setChecked(bool(item.get('associated')));layout.addWidget(associated)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec():self.save(c,name=name.text().strip(),associated=associated.isChecked())
        dialog.deleteLater()

    def compare(self,c):
        combo=c['combo']
        choices=[(combo.itemText(i),combo.itemData(i)) for i in range(combo.count()) if combo.itemData(i) is not None and getattr(combo,'entry_visible',lambda r:True)(i)]
        if not choices:return
        factory=character_factory(self.window) if combo is self.window.animation_combo else model_factory(self.window.models_tab)
        dialog=CompareDialog(self.window,choices,factory,combo.currentData());self.dialogs.append(dialog)
        dialog.finished.connect(lambda result:self.dialogs.remove(dialog) if dialog in self.dialogs else None)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose);dialog.show()

    def close(self):
        self.timer.stop()
        for dialog in self.dialogs[:]:dialog.reject()
