"""Search fields for dropdowns without changing source indices or item data."""
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget


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

        def refresh(*_args, combo=combo, search=search):
            useful = combo.count() >= minimum_items
            search.setVisible(useful)
            if not useful and search.text():
                search.clear()
            needle = search.text().strip().casefold()
            for row in range(combo.count()):
                combo.view().setRowHidden(row, needle not in combo.itemText(row).casefold())

        search.textChanged.connect(refresh)
        combo.model().rowsInserted.connect(refresh)
        combo.model().rowsRemoved.connect(refresh)
        combo.model().modelReset.connect(refresh)
        combo.model().dataChanged.connect(refresh)
        refresh()
