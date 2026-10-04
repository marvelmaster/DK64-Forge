"""Search fields for dropdowns without changing source indices or item data."""
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget, QListWidget, QListWidgetItem


def add_dropdown_search(combos, minimum_items=10):
    """Opt in useful lists; hide search automatically when the list is short."""
    for combo in combos:
        if combo.isHidden() or hasattr(combo, "search_edit"):
            continue
        parent = combo.parentWidget()
        if parent is None or parent.layout() is None:
            continue
        wrapper = QWidget(parent)
        replaced = parent.layout().replaceWidget(combo, wrapper)
        if replaced is None:
            wrapper.deleteLater()
            continue
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        search = QLineEdit(wrapper)
        search.setPlaceholderText("Search options…")
        search.setClearButtonEnabled(True)
        layout.addWidget(search)
        layout.addWidget(combo)
        combo.search_edit = search
        results = QListWidget(wrapper)
        results.setMaximumHeight(170)
        layout.insertWidget(1, results)
        combo.search_results = results

        def select(item, combo=combo):
            if item is not None:
                combo.setCurrentIndex(item.data(Qt.ItemDataRole.UserRole))

        def sync(row, combo=combo, results=results):
            with QSignalBlocker(results):
                for i in range(results.count()):
                    if results.item(i).data(Qt.ItemDataRole.UserRole) == row:
                        results.setCurrentRow(i)
                        return
                results.setCurrentRow(-1)

        results.currentItemChanged.connect(lambda item, previous, select=select: select(item))
        combo.currentIndexChanged.connect(sync)

        def refresh(*_args, combo=combo, search=search, results=results, sync=sync):
            useful = combo.count() >= minimum_items
            search.setVisible(useful)
            results.setVisible(useful)
            combo.setVisible(not useful)
            if not useful and search.text():
                search.clear()
            needle = search.text().strip().casefold()
            with QSignalBlocker(results):
                results.clear()
                for row in range(combo.count()):
                    hidden = needle not in combo.itemText(row).casefold() or not getattr(combo, "entry_visible", lambda r: True)(row) or not getattr(combo, "library_visible", lambda r: True)(row)
                    combo.view().setRowHidden(row, hidden)
                    if not hidden:
                        item = QListWidgetItem(combo.itemText(row))
                        item.setData(Qt.ItemDataRole.UserRole, row)
                        item.setToolTip(combo.itemData(row, Qt.ItemDataRole.ToolTipRole) or "")
                        results.addItem(item)
            sync(combo.currentIndex())

        combo.refresh_search = refresh
        search.textChanged.connect(refresh)
        combo.model().rowsInserted.connect(refresh)
        combo.model().rowsRemoved.connect(refresh)
        combo.model().modelReset.connect(refresh)
        combo.model().dataChanged.connect(refresh)
        refresh()
