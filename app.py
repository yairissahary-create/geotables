import html
import sys
import json
import re
from pathlib import Path
from pypdf import PdfReader, PdfWriter, Transformation
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QScrollArea, QTextBrowser, QWidget,
    QVBoxLayout, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QColorDialog, QStyledItemDelegate,
    QStyleOptionViewItem, QStyle, QInputDialog, QCompleter, QComboBox,
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QAbstractItemView,
    QFileDialog, QGridLayout, QPushButton
)
from PySide6.QtCore import QStringListModel
from PySide6.QtGui import (
    QFont, QPainter, QTextDocument, QColor, QPdfWriter, QPageSize, QPageLayout, QIcon
)
from PySide6.QtCore import Qt, QEvent, QPoint, QPointF, QRectF, QSizeF, QTimer, QMarginsF
from backend import Backend, special_chars

# winreg only exists on Windows — guard the import so macOS/Linux don't crash.
try:
    import winreg
except ImportError:
    winreg = None

# When PyInstaller freezes the app, __file__ resolves inside a temporary
# extraction folder (especially with --onefile), not next to the actual
# built executable. Use sys.executable's folder in that case instead, so
# config.json / justifications.json are found and saved next to the app
# the user actually double-clicked, not a folder that vanishes on exit.
if getattr(sys, "frozen", False):
    APP_BASE_DIR = Path(sys.executable).resolve().parent
else:
    APP_BASE_DIR = Path(__file__).resolve().parent


class ReadOnlyDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        return None


class TemplateFillDialog(QDialog):
    """Collect all template placeholders in one compact form."""

    def __init__(self, parent, backend, template):
        super().__init__(parent)
        self.backend = backend
        self.template = template
        self.fields = {}

        self.setWindowTitle("Fill template")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        for variable in sorted(backend.extract_variables(template)):
            field = QLineEdit(self)
            field.textChanged.connect(self.update_preview)
            self.fields[variable] = field
            form.addRow(f"{variable}:", field)

        layout.addLayout(form)
        self.template_label = QLabel(template, self)
        self.template_label.setWordWrap(True)
        layout.addWidget(self.template_label)
        self.preview = QLabel(self)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.update_preview()

    def update_preview(self):
        values = {variable: field.text() for variable, field in self.fields.items()}
        self.preview.setText(self.backend.substitute_template(self.template, values))

    def values(self):
        return {variable: field.text() for variable, field in self.fields.items()}


class TemplateDropdownDelegate(QStyledItemDelegate):
    """Show the selected justification's templates while editing a statement."""

    def __init__(self, parent, owner):
        super().__init__(parent)
        self.table = parent
        self.owner = owner

    def createEditor(self, parent, option, index):
        justification_item = self.table.item(index.row(), 1)
        justification = justification_item.text().strip() if justification_item else ""
        templates = self.owner.backend.get_templates_for_justification(justification)

        editor = QComboBox(parent)
        editor.setEditable(True)
        editor.lineEdit().setAlignment(Qt.AlignCenter)
        editor.setStyleSheet(
            "QComboBox, QComboBox QLineEdit "
            f"{{ color: {self.owner.text_color}; background: {self.owner.background_color}; }}"
        )
        editor.addItems(templates)
        editor.setInsertPolicy(QComboBox.NoInsert)
        editor.installEventFilter(self)
        editor.lineEdit().installEventFilter(self)
        editor.lineEdit().setProperty("template_editor", editor)
        editor.activated.connect(
            lambda _index, row=index.row(), combo=editor:
            self.on_template_activated(
                row,
                combo.currentText(),
                combo.property("original_text") or "",
                combo
            )
        )
        return editor

    def setEditorData(self, editor, index):
        current_text = index.data(Qt.EditRole) or ""
        editor.setEditText(current_text)
        editor.setProperty("original_text", current_text)
        editor.lineEdit().setFocus()
        popup_timer = QTimer(editor)
        popup_timer.setSingleShot(True)
        popup_timer.timeout.connect(editor.showPopup)
        # Keep the Python wrapper alive until the editor is visible.
        editor.popup_timer = popup_timer
        popup_timer.start(0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            editor = obj.property("template_editor") or obj
            current_row = self.table.currentRow()
            self.commitData.emit(editor)
            self.closeEditor.emit(editor)

            justification_item = self.table.item(current_row, 1)
            justification_text = justification_item.text().strip() if justification_item else ""
            if justification_item and not justification_text:
                justification_item.setText("נתון")
                self.owner.update_item_font(justification_item)
                justification_text = "נתון"

            # If this justification is a triangle/quadrilateral category and
            # מצולע (col 0) is still empty, jump there and seed the matching
            # polygon symbol instead of making a new row.
            symbol = self.owner.backend.justification_shape_symbol(justification_text)
            if symbol:
                polygon_item = self.table.item(current_row, 0)
                if polygon_item is None or not polygon_item.text().strip():
                    if polygon_item is None:
                        polygon_item = self.owner.create_text_item("")
                        self.table.setItem(current_row, 0, polygon_item)
                    polygon_item.setText(symbol)
                    self.owner.update_item_font(polygon_item)
                    self.table.setCurrentCell(current_row, 0)
                    self.table.editItem(polygon_item)
                    return True

            self.owner.add_row("", after_row=current_row)
            new_row = current_row + 1
            self.table.setCurrentCell(new_row, 2)
            item = self.table.item(new_row, 2)
            if item:
                self.table.editItem(item)
            return True

        return super().eventFilter(obj, event)

    def on_template_activated(self, row, template, original_text, editor):
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)
        QTimer.singleShot(
            0,
            lambda: self.complete_selected_template(row, template, original_text)
        )

    def complete_selected_template(self, row, template, original_text):
        variables = self.owner.backend.extract_variables(template)
        if variables:
            dialog = TemplateFillDialog(self.owner, self.owner.backend, template)
            if dialog.exec() != QDialog.Accepted:
                self.set_cell_text(row, original_text)
                return
            values = dialog.values()
        else:
            values = {}

        self.set_cell_text(
            row, self.owner.backend.substitute_template(template, values)
        )

    def set_cell_text(self, row, text):
        item = self.table.item(row, 2)
        if item is None:
            item = self.owner.create_text_item("")
            self.table.setItem(row, 2, item)
        item.setText(text)
        self.owner.update_item_font(item)


class EnterKeyDelegate(QStyledItemDelegate):
    def __init__(self, parent, owner):
        super().__init__(parent)

        self.table = parent
        self.owner = owner

        self.specials_non_italic = False
        self.all_non_italic = False

    def set_specials_non_italic(self, value):
        self.specials_non_italic = value
        self.table.viewport().update()

    def set_all_non_italic(self, value):
        self.all_non_italic = value
        self.table.viewport().update()

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)

        if editor:
            editor.installEventFilter(self)

            # remember which row is being edited so number-clicks can target it
            try:
                self.owner._active_row = index.row()
            except Exception:
                pass

            # If this is the `נימוק` column (column 1), attach an inline completer
            if index.column() == 1:
                model = QStringListModel()
                completer = QCompleter(model, editor)
                completer.setCaseSensitivity(Qt.CaseInsensitive)
                completer.setCompletionMode(QCompleter.PopupCompletion)

                # Update suggestions as the user types
                def on_text_changed(text):
                    try:
                        suggestions = self.owner.backend.get_autofill_suggestions(text) or []
                    except Exception:
                        suggestions = []
                    model.setStringList(suggestions)

                # If editor is a QLineEdit-like widget, add completer
                try:
                    editor.setCompleter(completer)
                except Exception:
                    completer.setWidget(editor)

                if hasattr(editor, "textChanged"):
                    editor.textChanged.connect(on_text_changed)

        return editor

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            current = self.table.currentIndex()

            if (
                current.isValid()
                and current.column() != self.table.columnCount() - 1
            ):
                col = current.column()
                row = current.row()

                self.commitData.emit(obj)
                self.closeEditor.emit(obj)

                justification_item = self.table.item(row, 1)
                if justification_item and not justification_item.text().strip():
                    justification_item.setText("נתון")
                    self.owner.update_item_font(justification_item)

                # If we just finished editing נימוק (col 1) and טענה (col 2)
                # is still empty in this row, jump there instead of making
                # a new row.
                if col == 1:
                    statement_item = self.table.item(row, 2)
                    if statement_item is None or not statement_item.text().strip():
                        self.table.setCurrentCell(row, 2)
                        target_item = self.table.item(row, 2)
                        if target_item:
                            self.table.editItem(target_item)
                        return True

                self.owner.add_row("", after_row=row)

                new_row = row + 1

                self.table.setCurrentCell(new_row, col)

                item = self.table.item(new_row, col)

                if item:
                    self.table.editItem(item)

                return True

        return super().eventFilter(obj, event)

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole) or ""

        painter.save()

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # Prevent Qt from drawing its own text.
        opt.text = ""

        style = (
            opt.widget.style()
            if opt.widget
            else QApplication.style()
        )

        style.drawControl(
            QStyle.CE_ItemViewItem,
            opt,
            painter,
            opt.widget
        )

        if text:
            doc = QTextDocument()

            doc.setDefaultFont(QFont("CMU Classical Serif", 14))
            doc.setDocumentMargin(0)

            rendered_text = self.html_for_text(text)
            if index.column() == 2:
                rendered_text = f"<div style='text-align:center'>{rendered_text}</div>"
            doc.setHtml(rendered_text)
            doc.setTextWidth(option.rect.width())

            painter.translate(option.rect.topLeft())

            doc.drawContents(painter)

        painter.restore()

    def html_for_text(self, text):
        runs = []

        for run in self.owner.backend.get_display_runs(text):
            current = run["text"]
            escaped = (
                html.escape(current)
                .replace("\n", "<br/>")
            )

            style_parts = [
                f"font-family:{run['font']}",
                f"color:{self.owner.text_color}",
                "white-space:pre-wrap"
            ]

            if run["script"] == "super":
                style_parts.append("vertical-align:super")
                style_parts.append("font-size:75%")

            elif run["script"] == "sub":
                style_parts.append("vertical-align:sub")
                style_parts.append("font-size:75%")

            if self.all_non_italic:
                style_parts.append("font-style:normal")

            elif (
                self.specials_non_italic
                and any(ch in self.owner.backend.special_chars for ch in current)
            ):
                style_parts.append("font-style:normal")

            style = "; ".join(style_parts)

            runs.append(
                f"<span style='{style}'>{escaped}</span>"
            )

        return "".join(runs)

class CollapsibleSection(QWidget):
    def __init__(self, title, content, parent=None):
        super().__init__(parent)

        self.toggle_button = QPushButton(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)

        self.content = QTextBrowser()
        self.content.setHtml(content)
        self.content.setOpenExternalLinks(True)
        self.content.setVisible(False)
        self.content.setMaximumHeight(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self.toggle_button.clicked.connect(self.toggle)

    def toggle(self, checked):
        self.content.setVisible(checked)

        if checked:
            self.toggle_button.setText(
                "▼ " + self.toggle_button.text().lstrip("▶▼ ")
            )
            self.content.setMaximumHeight(16777215)
        else:
            self.toggle_button.setText(
                "▶ " + self.toggle_button.text().lstrip("▶▼ ")
            )
            self.content.setMaximumHeight(0)

class GeoTables(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("GeoTables")
        self.setGeometry(100, 100, 1000, 700)

        # Menu bar
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        file_menu.addAction("New", self.new_file)
        file_menu.addAction("Open", self.open_file)
        file_menu.addAction("Export GeoTable...", self.export_geotable)
        file_menu.addAction("Export PDF...", self.export_pdf)
        file_menu.addAction("Combine PDFs...", self.combine_pdfs)
        file_menu.addAction("Add JSON Entry", self.add_json_entry)
        file_menu.addAction("Replace special chars", self.replace_special_chars)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        edit_menu = menubar.addMenu("Edit")
        edit_menu.addAction("Undo", self.undo)
        edit_menu.addAction("Redo", self.redo)
        edit_menu.addSeparator()
        edit_menu.addAction("Sequence format...", self.set_sequence_format)

        help_menu = menubar.addMenu("Help")
        help_menu.addAction("About", self.about)
        help_menu.addAction("Tutorials", self.show_tutorials)

        # Config menu in the menubar (top-level)
        config_menu = menubar.addMenu("Config")
        config_menu.addAction("Sequence format...", self.set_sequence_format)
        config_menu.addAction("Select Grid Color", self.select_color)
        config_menu.addAction("Select Background Color", self.select_background_color)
        config_menu.addAction("Select Text Color", self.select_text_color)
        config_menu.addSeparator()
        config_menu.addAction("Save Data Folder to Config", self.save_data_dir_to_config)
        config_menu.addAction("Save Data Folder", self.save_data_dir_to_registry)
        config_menu.addSeparator()
        config_menu.addAction("Export Config...", self.export_config)
        config_menu.addAction("Import Config...", self.import_config)
        config_menu.addSeparator()
        char_menu = config_menu.addMenu("Character Replacements")
        char_menu.addAction("Add Replacement", self.add_character_replacement)
        tutorials_action = help_menu.addAction("Tutorials")
        tutorials_action.triggered.connect(self.show_tutorials)


        # State
        self.step = 1

        # config file path — always lives next to the script itself
        self._config_path = APP_BASE_DIR / "config.json"

        # Determine data folder: reuse saved location if valid, else fall back
        # to the script folder for now and prompt once the window is visible.
        resolved_dir = self.load_data_dir()
        needs_prompt = resolved_dir is None
        self._app_dir = resolved_dir or APP_BASE_DIR

        self.backend = Backend(self._app_dir)
        self.backend.textReplaced.connect(self.on_text_replaced)

        self._ignore_item_changed = False
        self._pending_item = None
        self._document_dirty = False
        # sequence insertion state
        self.seq_before = " ("
        self.seq_between = ", "
        self.seq_after = ")"
        self._sequence_row = None
        self._sequence_values = []
        self._sequence_limits = []
        self._sequence_placeholders = []
        self._sequence_base = None
        # default grid color (will be overridden by config)
        self.grid_color = "rgb(255,0,0)"
        self.background_color = "#ffffff"
        self.text_color = "#000000"
        # Template workflow state
        self._selected_row = None
        self._document_path = None

        # load persisted config if present (overrides defaults)
        self.load_config()

        # Table
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["מצולע", "נימוק", "טענה", "'מס"])

        header = self.table.horizontalHeader()

        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)

        # Delegate (NOW the only renderer)
        self.delegate = EnterKeyDelegate(self.table, self)
        self.table.setItemDelegate(self.delegate)

        # Delegate toggles (now valid + self-contained)
        self.sym_action = edit_menu.addAction("Symbols non-italic")
        self.sym_action.setCheckable(True)
        self.sym_action.toggled.connect(self.delegate.set_specials_non_italic)

        self.all_action = edit_menu.addAction("All non-italic")
        self.all_action.setCheckable(True)
        self.all_action.toggled.connect(self.delegate.set_all_non_italic)

        self.apply_table_styles()

        # Column sizing
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(3, 20)

        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Fixed
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Fixed
        )

        # Prevent editing number column
        self.table.setItemDelegateForColumn(3, ReadOnlyDelegate(self.table))
        self.table.setItemDelegateForColumn(
            2, TemplateDropdownDelegate(self.table, self)
        )

        # Signals
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.cellClicked.connect(self.on_number_clicked)

        # Central widget layout (table only)
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout()
        layout.addWidget(self.table)
        central.setLayout(layout)

        # start with one row
        self.add_row("")
        self._document_dirty = False

        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.auto_save)
        self._autosave_timer.start(60_000)

        if needs_prompt:
            QTimer.singleShot(0, self.prompt_for_data_dir)

    def new_file(self):
        if not self.confirm_discard_or_save():
            return

        self._ignore_item_changed = True
        self.table.setRowCount(0)
        self.add_row("")
        self._ignore_item_changed = False
        self._document_path = None
        self._document_dirty = False
        self.setWindowTitle("GeoTables")

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open GeoTable",
            "",
            "GeoTable files (*.geotable)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as file:
                document = json.load(file)
            self.load_geotable(document)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            QMessageBox.critical(self, "Open failed", str(error))
            return

        self._document_path = Path(path)
        self._document_dirty = False
        self.setWindowTitle(f"GeoTables - {self._document_path.name}")

    def save_file(self):
        if self._document_path is None:
            return self.export_geotable()
        try:
            self.write_geotable(self._document_path)
        except OSError as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return False
        self._document_dirty = False
        return True

    def confirm_discard_or_save(self):
        if not self._document_dirty:
            return True

        choice = QMessageBox.warning(
            self,
            "Unsaved changes",
            "Save changes before creating a new table?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save
        )
        if choice == QMessageBox.Save:
            return self.save_file()
        return choice == QMessageBox.Discard

    def export_geotable(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export GeoTable",
            "",
            "GeoTable files (*.geotable)"
        )
        if not path:
            return False
        if not path.lower().endswith(".geotable"):
            path += ".geotable"

        try:
            self.write_geotable(path)
        except OSError as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return False

        self._document_path = Path(path)
        self._document_dirty = False
        self.setWindowTitle(f"GeoTables - {self._document_path.name}")
        return True

    def geotable_document(self):
        rows = []
        for row in range(self.table.rowCount()):
            rows.append([
                (self.table.item(row, column).text()
                 if self.table.item(row, column) is not None else "")
                for column in range(self.table.columnCount() - 1)
            ])

        return {
            "format": "geotable",
            "version": 1,
            "rows": rows,
            "settings": {
                "seq_before": self.seq_before,
                "seq_between": self.seq_between,
                "seq_after": self.seq_after,
                "grid_color": self.grid_color,
                "background_color": self.background_color,
                "text_color": self.text_color,
            },
        }
    def write_geotable(self, path):
        with open(path, "w", encoding="utf-8") as file:
            json.dump(self.geotable_document(), file, ensure_ascii=False, indent=2)
            file.write("\n")

    def load_geotable(self, document):
        if not isinstance(document, dict) or document.get("format") != "geotable":
            raise ValueError("This is not a valid GeoTable file.")
        rows = document.get("rows")
        if not isinstance(rows, list):
            raise ValueError("GeoTable rows must be a list.")

        settings = document.get("settings", {})
        if not isinstance(settings, dict):
            raise ValueError("GeoTable settings must be an object.")
        self.seq_before = settings.get("seq_before", self.seq_before)
        self.seq_between = settings.get("seq_between", self.seq_between)
        self.seq_after = settings.get("seq_after", self.seq_after)
        self.grid_color = settings.get("grid_color", self.grid_color)
        self.background_color = settings.get("background_color", self.background_color)
        self.text_color = settings.get("text_color", self.text_color)
        self.apply_table_styles()

        self._ignore_item_changed = True
        self.table.setRowCount(0)
        for saved_row in rows:
            if not isinstance(saved_row, list):
                raise ValueError("Each GeoTable row must be a list.")
            self.add_row("")
            row = self.table.rowCount() - 1
            for column, value in enumerate(saved_row[:3]):
                item = self.table.item(row, column)
                item.setText(str(value))
                self.update_item_font(item)
        if not rows:
            self.add_row("")
        self._ignore_item_changed = False
        self.renumber_rows()
        self._document_dirty = False

    def auto_save(self):
        if not self._document_dirty:
            return
        path = self._document_path or self._app_dir / "autosave.geotable"
        try:
            self.write_geotable(path)
        except OSError:
            pass
        else:
            self._document_dirty = False

    def _cell_html(self, row, col):
        item = self.table.item(row, col)
        text = item.text() if item else ""
        if col == self.table.columnCount() - 1:
            escaped = html.escape(text)
            return f"<div style='text-align:center; color:{self.text_color}'>{escaped}</div>"
        rendered = self.delegate.html_for_text(text)
        if col == 2:
            rendered = f"<div style='text-align:center'>{rendered}</div>"
        return rendered

    def _cell_doc(self, row, col, width):
        doc = QTextDocument()
        doc.setDefaultFont(QFont("CMU Classical Serif", 14))
        doc.setDocumentMargin(0)
        doc.setHtml(self._cell_html(row, col))
        doc.setTextWidth(width)
        return doc

    def export_pdf(self):
        problem_number, accepted = QInputDialog.getText(
            self, "Export PDF", "Problem number:"
        )
        if not accepted:
            return
        problem_number = problem_number.strip()
        if problem_number and not problem_number.endswith("."):
            problem_number = "." + problem_number + " "

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export PDF",
            f"{problem_number}.pdf" if problem_number else "",
            "PDF files (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        col_count = self.table.columnCount()
        col_widths = [max(1, self.table.columnWidth(c)) for c in range(col_count)]
        width = max(1, sum(col_widths))

        header_height = self.table.horizontalHeader().height()
        title_height = max(1, self.table.rowHeight(0) * 2)

        # Measure each row from its stored text, not from live widget state,
        # so an open editor or stale layout can never affect the export.
        row_heights = []
        for row in range(self.table.rowCount()):
            row_h = self.table.rowHeight(row)
            for col in range(col_count):
                doc = self._cell_doc(row, col, col_widths[col])
                row_h = max(row_h, int(doc.size().height()))
            row_heights.append(row_h)

        body_height = header_height + sum(row_heights)

        writer = QPdfWriter(path)
        writer.setResolution(96)
        writer.setPageSize(QPageSize(
            QSizeF(width * 72 / 96, (title_height + body_height) * 72 / 96),
            QPageSize.Point,
            "GeoTable"
        ))
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Point)

        painter = QPainter(writer)
        if not painter.isActive():
            QMessageBox.critical(self, "Export failed", "Could not create the PDF.")
            return

        # Title
        title_font = QFont(self.table.font())
        title_font.setBold(True)
        title_font.setPixelSize(title_height)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(0, 0, width, title_height),
            Qt.AlignRight | Qt.AlignVCenter,
            problem_number
        )

        # Header row
        y = title_height
        header_font = QFont(self.table.font())
        header_font.setBold(True)
        painter.setFont(header_font)
        painter.setPen(QColor(self.text_color))
        x = 0
        for col in range(col_count):
            header_item = self.table.horizontalHeaderItem(col)
            header_text = header_item.text() if header_item else ""
            painter.drawText(
                QRectF(x, y, col_widths[col], header_height),
                Qt.AlignCenter,
                header_text
            )
            x += col_widths[col]
        y += header_height

        # Body rows
        for row in range(self.table.rowCount()):
            row_h = row_heights[row]
            x = 0
            for col in range(col_count):
                doc = self._cell_doc(row, col, col_widths[col])
                painter.save()
                painter.translate(x, y)
                doc.drawContents(painter)
                painter.restore()
                x += col_widths[col]
            y += row_h

        # Grid lines
        pen = painter.pen()
        pen.setColor(QColor(self.grid_color))
        painter.setPen(pen)

        x = 0
        for col in range(col_count + 1):
            painter.drawLine(
                QPointF(x, title_height),
                QPointF(x, title_height + body_height)
            )
            if col < col_count:
                x += col_widths[col]

        y = title_height
        painter.drawLine(QPointF(0, y), QPointF(width, y))
        y += header_height
        painter.drawLine(QPointF(0, y), QPointF(width, y))
        for row_h in row_heights:
            y += row_h
            painter.drawLine(QPointF(0, y), QPointF(width, y))

        painter.end()

    def combine_pdfs(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select PDFs to combine",
            "",
            "PDF files (*.pdf)"
        )
        if not paths:
            return

        numbered_files = []
        for path in paths:
            try:
                reader = PdfReader(path)
                first_page_text = reader.pages[0].extract_text() or ""
                match = re.search(r"(?m)^\s*(\d+)\.", first_page_text)
                if not match:
                    raise ValueError("No problem number found above the table.")
                numbered_files.append((int(match.group(1)), path, reader))
            except Exception as error:
                QMessageBox.critical(
                    self,
                    "Combine failed",
                    f"Could not read the problem number from {Path(path).name}:\n{error}"
                )
                return

        numbered_files.sort(key=lambda entry: entry[0])

        lowest = numbered_files[0][0]
        highest = numbered_files[-1][0]
        default_name = f"{lowest}-{highest}.pdf"

        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save combined PDF",
            default_name,
            "PDF files (*.pdf)"
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".pdf"):
            output_path += ".pdf"

        # Flatten every page from every source, in sorted order, so a
        # multi-page source still combines correctly.
        all_pages = []
        for _number, _path, reader in numbered_files:
            all_pages.extend(reader.pages)

        width = max(page.mediabox.width for page in all_pages)
        total_height = sum(page.mediabox.height for page in all_pages)

        writer = PdfWriter()
        canvas = writer.add_blank_page(width=float(width), height=float(total_height))

        # Stack pages top to bottom on one continuous page. PDF y-coordinates
        # run bottom-up, so track a cursor starting at the top and subtract
        # each page's height before placing it.
        y_cursor = float(total_height)
        for page in all_pages:
            page_height = float(page.mediabox.height)
            y_cursor -= page_height
            transformation = Transformation().translate(tx=0, ty=y_cursor)
            canvas.merge_transformed_page(page, transformation)

        try:
            with open(output_path, "wb") as file:
                writer.write(file)
        except OSError as error:
            QMessageBox.critical(self, "Combine failed", str(error))

    def undo(self):
        print("Undo")

    def redo(self):
        print("Redo")

    def about(self):
        print("About GeoTables")

    def show_tutorials(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("GeoTables Tutorials")
        dialog.resize(850, 700)

        main_layout = QVBoxLayout(dialog)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        sections_layout = QVBoxLayout(container)
        sections_layout.setAlignment(Qt.AlignTop)

        tutorials = [
            (
                "1. Getting Started",
                """
                <h2>Creating a new table</h2>

                <p>When you open GeoTables, you can begin entering your
                geometry work directly into the table.</p>

                <p>Each row represents a step in your solution. A typical
                row contains:</p>

                <ul>
                    <li>A statement</li>
                    <li>A justification</li>
                    <li>Any additional information required by the table</li>
                </ul>

                <p>Work through the table from top to bottom, adding each
                step of your proof in order.</p>

                <h2>Editing a cell</h2>

                <p>Click a cell to edit its contents.</p>

                <p>You can type mathematical expressions, geometry statements,
                and other text directly into the cells.</p>

                <p>Press <b>Enter</b> to finish editing the cell.</p>
                """
            ),

            (
                "2. Writing Mathematical Symbols",
                """
                <p>GeoTables supports convenient text shortcuts for many
                mathematical symbols.</p>

                <p>Instead of searching for a symbol manually, type its
                shortcut and GeoTables can replace it with the corresponding
                symbol.</p>

                <table border="1" cellpadding="6">
                    <tr>
                        <th>Type</th>
                        <th>Symbol</th>
                    </tr>
                    <tr>
                        <td><code>!=</code></td>
                        <td>≠</td>
                    </tr>
                    <tr>
                        <td><code>&lt;=</code></td>
                        <td>≤</td>
                    </tr>
                    <tr>
                        <td><code>&gt;=</code></td>
                        <td>≥</td>
                    </tr>
                    <tr>
                        <td><code>prl'</code></td>
                        <td>∥</td>
                    </tr>
                    <tr>
                        <td><code>ngl'</code></td>
                        <td>∠</td>
                    </tr>
                    <tr>
                        <td><code>tr'</code></td>
                        <td>△</td>
                    </tr>
                    <tr>
                        <td><code>cng'</code></td>
                        <td>≌</td>
                    </tr>
                    <tr>
                        <td><code>~</code></td>
                        <td>∼</td>
                    </tr>
                </table>

                <p>This lets you type mathematical notation quickly using
                an ordinary keyboard.</p>
                """
            ),

            (
                "3. Using Justifications",
                """
                <p>Justifications explain <b>why</b> a statement in your
                proof is true.</p>

                <p>When entering a justification, GeoTables can provide
                suggestions based on what you type.</p>

                <p>Start typing the name of a justification, and matching
                suggestions will appear.</p>

                <p>Select the appropriate justification from the suggestions.</p>

                <p>Some justifications may also contain templates that help
                you construct the statement correctly.</p>
                """
            ),

            (
                "4. Using Templates",
                """
                <p>Some justifications have predefined templates.</p>

                <p>Templates contain placeholders for information that changes
                from one proof to another.</p>

                <p>For example:</p>

                <p><code>{A} = {B}</code></p>

                <p>When using the template, replace the placeholders with the
                appropriate values.</p>

                <p>Templates are useful when the same type of reasoning appears
                repeatedly in different problems.</p>
                """
            ),

            (
                "5. Geometry Symbols",
                """
                <p>GeoTables can recognize certain categories of geometry
                justifications and associate them with appropriate shape
                symbols.</p>

                <ul>
                    <li>Triangle-related justifications can use △</li>
                    <li>Quadrilateral-related justifications can use ◻</li>
                </ul>

                <p>This can make geometry proofs easier to read and visually
                organize.</p>
                """
            ),

            (
                "6. Creating Your Own Character Replacement",
                """
                <p>You can add your own text-to-symbol shortcuts.</p>

                <p>Open the character replacement editor from the appropriate
                menu option.</p>

                <p>You will see two columns:</p>

                <p><b>Find</b> → <b>Replace</b></p>

                <p>For example:</p>

                <p><code>abc</code> → ★</p>

                <p>After saving, typing <code>abc</code> where character
                replacement is performed will produce ★.</p>

                <h3>Adding a new row</h3>

                <p>Click <b>Add row</b> to create another replacement pair.</p>

                <p>Enter the text you want GeoTables to find on the left
                and the replacement text on the right.</p>

                <p>Rows with an empty field are ignored when you save.</p>

                <h3>Saving your replacements</h3>

                <p>Click <b>OK</b> to save the changes.</p>

                <p>Your character replacements are stored in GeoTables'
                configuration, so they are available the next time you
                start the program.</p>

                <p>Click <b>Cancel</b> if you want to close the editor
                without saving your changes.</p>
                """
            ),

            (
                "7. Editing Existing Character Replacements",
                """
                <p>Open the character replacement editor to see your current
                replacement pairs.</p>

                <p>You can change either side of an existing pair.</p>

                <p>For example, you could change:</p>

                <p><code>!=</code> → ≠</p>

                <p>to another shortcut and replacement of your choice.</p>

                <p>You can also add additional rows with <b>Add row</b>.</p>

                <p>If you leave either side of a row empty, that row will not
                be saved.</p>
                """
            ),

            (
                "8. Saving Your Work",
                """
                <p>Save your GeoTables document regularly so that your work
                is not lost.</p>

                <p>Remember that there are two different kinds of saved
                information:</p>

                <p><b>Your document</b><br>
                Contains your actual geometry table and proof.</p>

                <p><b>Your settings</b><br>
                Contains things such as your character replacement pairs.</p>

                <p>Changing a setting does not necessarily change the currently
                open document, and editing a document does not necessarily
                change your settings.</p>
                """
            ),

            (
                "9. A Typical Workflow",
                """
                <ol>
                    <li>Open or create a table.</li>
                    <li>Enter the first statement of your proof.</li>
                    <li>Enter its justification.</li>
                    <li>Continue adding statements and justifications.</li>
                    <li>Use keyboard shortcuts for mathematical symbols.</li>
                    <li>Use justification suggestions and templates when helpful.</li>
                    <li>Review the completed proof.</li>
                    <li>Save your document.</li>
                </ol>
                """
            ),

            (
                "10. Tips",
                """
                <h3>Use shortcuts</h3>
                <p>Typing <code>!=</code> is usually faster than searching
                for ≠.</p>

                <h3>Keep statements concise</h3>
                <p>A proof is easier to follow when each row contains one
                clear step.</p>

                <h3>Use the justification suggestions</h3>
                <p>They can save time and help keep your terminology
                consistent.</p>

                <h3>Customize your shortcuts</h3>
                <p>If you frequently use a symbol or notation that does not
                have a convenient shortcut, add your own character replacement.</p>

                <h3>Save regularly</h3>
                <p>Especially when working on a long proof. Future-you will
                appreciate present-you's foresight. 🧮</p>
                """
            ),
        ]

        for title, content in tutorials:
            section = CollapsibleSection(title, content)
            sections_layout.addWidget(section)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        main_layout.addWidget(close_button)

        dialog.exec()

    def add_json_entry(self):
        # Load existing JSON first, since categories come from it.
        path = self._app_dir / "justifications.json"
        items = []

        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    items = json.load(f)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Failed to read JSON: {e}"
                )
                return

        if not isinstance(items, list):
            QMessageBox.critical(
                self,
                "Error",
                "JSON root must be a list."
            )
            return

        # Ask for the ID.
        id_text, ok = QInputDialog.getText(
            self,
            "Add JSON Entry",
            "id:"
        )

        if not ok or not id_text.strip():
            return

        id_text = id_text.strip()

        # Generic repeated-input helper.
        # Keeps asking until the user enters a blank value.
        def get_list(prompt):
            values = []

            while True:
                value, ok = QInputDialog.getText(
                    self,
                    "Add JSON Entry",
                    f"{prompt}\n\nLeave blank when finished."
                )

                if not ok:
                    return None

                value = value.strip()

                if not value:
                    break

                values.append(value)

            return values

        # Names
        names = get_list("Enter a name:")

        if names is None:
            return

        # Collect all existing categories.
        existing_categories = set()

        for item in items:
            if not isinstance(item, dict):
                continue

            item_categories = item.get("categories", [])

            # Support old JSON entries that may still use "category".
            if not item_categories:
                old_category = item.get("category")

                if old_category:
                    item_categories = [old_category]

            if isinstance(item_categories, list):
                for category in item_categories:
                    if isinstance(category, str) and category.strip():
                        existing_categories.add(category.strip())

        existing_categories = sorted(
            existing_categories,
            key=str.lower
        )

        # Categories
        categories = []

        while True:
            dialog = QDialog(self)
            dialog.setWindowTitle("Add JSON Entry")

            layout = QVBoxLayout(dialog)

            label = QLabel("Select a category:")
            layout.addWidget(label)

            combo = QComboBox()
            combo.addItems(existing_categories)
            combo.addItem("Other...")
            layout.addWidget(combo)

            other_field = QLineEdit()
            other_field.setPlaceholderText("Enter new category")
            other_field.setVisible(False)
            layout.addWidget(other_field)

            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            )
            layout.addWidget(buttons)

            def category_changed():
                is_other = combo.currentText() == "Other..."
                other_field.setVisible(is_other)

                if is_other:
                    other_field.setFocus()

            combo.currentTextChanged.connect(category_changed)

            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)

            # If there are no existing categories,
            # automatically select Other...
            if not existing_categories:
                combo.setCurrentText("Other...")
                other_field.setVisible(True)

            if dialog.exec() != QDialog.Accepted:
                return

            if combo.currentText() == "Other...":
                category = other_field.text().strip()

                if not category:
                    QMessageBox.warning(
                        self,
                        "Invalid category",
                        "Please enter a category."
                    )
                    continue
            else:
                category = combo.currentText().strip()

            # Don't add duplicate categories.
            if category and category not in categories:
                categories.append(category)

            # Ask whether another category should be added.
            more = QMessageBox.question(
                self,
                "Add another category?",
                "Would you like to add another category?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if more == QMessageBox.No:
                break

        if not categories:
            QMessageBox.warning(
                self,
                "Error",
                "At least one category is required."
            )
            return

        # Templates
        templates = get_list("Enter a template:")

        if templates is None:
            return

        # Variables
        variables = get_list("Enter a variable:")

        if variables is None:
            return

        # Add or update the entry.
        action = "added"

        for item in items:
            if isinstance(item, dict) and item.get("id") == id_text:
                item["name"] = names
                item["categories"] = categories
                item.pop("category", None)
                item["templates"] = templates
                item["variables"] = variables

                action = "updated"
                break

        else:
            items.append({
                "id": id_text,
                "name": names,
                "categories": categories,
                "templates": templates,
                "variables": variables,
            })

        # Save JSON.
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(
                    items,
                    f,
                    ensure_ascii=False,
                    indent=2
                )
                f.write("\n")

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to write JSON: {e}"
            )
            return

        QMessageBox.information(
            self,
            "Success",
            f"{action}: {id_text}"
        )


    def get_categories(self, items):
        # Collect all existing categories
        existing_categories = set()

        for item in items:
            categories = item.get("categories", [])

            # Support old entries that may still use "category"
            if not categories:
                old_category = item.get("category")
                if old_category:
                    categories = [old_category]

            if isinstance(categories, list):
                for category in categories:
                    if isinstance(category, str) and category.strip():
                        existing_categories.add(category.strip())

        existing_categories = sorted(existing_categories, key=str.lower)

        categories = []

        while True:
            dialog = QDialog(self)
            dialog.setWindowTitle("Add JSON Entry")

            layout = QVBoxLayout(dialog)

            label = QLabel(
                "Select a category:\n"
                "Choose an existing category or select Other..."
            )
            layout.addWidget(label)

            combo = QComboBox()
            combo.addItems(existing_categories)
            combo.addItem("Other...")
            layout.addWidget(combo)

            other_field = QLineEdit()
            other_field.setPlaceholderText("Enter new category")
            other_field.setVisible(False)
            layout.addWidget(other_field)

            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            )
            layout.addWidget(buttons)

            def category_changed(index):
                is_other = combo.currentText() == "Other..."
                other_field.setVisible(is_other)

                if is_other:
                    other_field.setFocus()

            combo.currentIndexChanged.connect(category_changed)

            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)

            # If there are no existing categories, automatically use Other
            if not existing_categories:
                combo.setCurrentText("Other...")

            if dialog.exec() != QDialog.Accepted:
                return None

            if combo.currentText() == "Other...":
                category = other_field.text().strip()

                if not category:
                    QMessageBox.warning(
                        self,
                        "Invalid category",
                        "Please enter a category."
                    )
                    continue
            else:
                category = combo.currentText().strip()

            if category and category not in categories:
                categories.append(category)

            # Ask for another category
            more = QMessageBox.question(
                self,
                "Add another category?",
                "Would you like to add another category?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if more == QMessageBox.No:
                break

        return categories

    def set_sequence_format(self):
        before, ok = QInputDialog.getText(self, "Sequence format", "Text before sequence:", text=self.seq_before)
        if not ok:
            return
        between, ok = QInputDialog.getText(self, "Sequence format", "Separator between numbers:", text=self.seq_between)
        if not ok:
            return
        after, ok = QInputDialog.getText(self, "Sequence format", "Text after sequence:", text=self.seq_after)
        if not ok:
            return

        self.seq_before = before
        self.seq_between = between
        self.seq_after = after
        self._document_dirty = True
        # persist config
        try:
            self.save_config()
        except Exception:
            pass

    def replace_special_chars(self):
        item = self.table.currentItem()
        if item is None:
            return
        self.backend.replace_all_special(item.text())

    def add_character_replacement(self):
        find_text, ok = QInputDialog.getText(
            self, "Add character replacement", "Text to find (e.g. ~=):"
        )
        if not ok or not find_text.strip():
            return
        find_text = find_text.strip()

        replace_text, ok = QInputDialog.getText(
            self, "Add character replacement", "Replacement character (e.g. ≈):"
        )
        if not ok or not replace_text.strip():
            return
        replace_text = replace_text.strip()

        added = self.backend.add_character_replacement(find_text, replace_text)
        if not added:
            QMessageBox.warning(
                self,
                "Not added",
                f'"{find_text}" is already a registered replacement, '
                "or one of the fields was empty."
            )
            return
        

        self.table.viewport().update()
        QMessageBox.information(
            self,
            "Character replacement added",
            f'"{find_text}" -> "{replace_text}" added and saved to config.json.'
        )

    def edit_charecter_replacements(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("edit find/replace character pairs")
        layout = QGridLayout(dialog)

        layout.addWidget(QLabel("find_chars"), 0, 0)
        layout.addWidget(QLabel("replace_chars"), 0,2)

        find_fields = []
        replace_fields = []

        for row, (find,replace) in enumerate(zip(
            self.backend.find_chars,self.backend.replace_chars
            ),start = 1):
            find_field = QLineEdit(find)
            arrow = QLabel("→")
            replace_field = QLineEdit(replace)
            layout.addWidget(find_field, row, 0)
            layout.addWidget(arrow, row, 1)
            layout.addWidget(replace_field, row, 2)
            find_fields.append(find_field)
            replace_fields.append(replace_field)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=dialog
        )
        def add_row():
            row = len(find_fields) + 1

            find_field = QLineEdit()
            arrow = QLabel("→")
            replace_field = QLineEdit()

            layout.addWidget(find_field, row, 0)
            layout.addWidget(arrow, row, 1)
            layout.addWidget(replace_field, row, 2)

            find_fields.append(find_field)
            replace_fields.append(replace_field)

            layout.removeWidget(add_row_button)
            layout.removeWidget(buttons)

            layout.addWidget(add_row_button, row + 1, 0, 1, 3)
            layout.addWidget(buttons, row + 2, 0, 1, 3)


        add_row_button = QPushButton("Add row")
        add_row_button.clicked.connect(add_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=dialog
        )

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout.addWidget(add_row_button, len(find_fields) + 1, 0, 1, 3)
        layout.addWidget(buttons, len(find_fields) + 2, 0, 1, 3)


        result = dialog.exec()
        if result == QDialog.Accepted:
            pairs = [
                (find_field.text().strip(), replace_field.text().strip())
                for find_field, replace_field in zip(find_fields, replace_fields)
                if find_field.text().strip() and replace_field.text().strip()
            ]

            self.backend.find_chars = [find for find, replace in pairs]
            self.backend.replace_chars = [replace for find, replace in pairs]


            self.backend.save_chars_config()
                



    def on_item_changed(self, item):
        if self._ignore_item_changed:
            return
        if item is None:
            return
        if item.column() == self.table.columnCount() - 1:
            return

        self._document_dirty = True

        self._pending_item = item
        self.backend.replace_all_special(item.text())

    def on_text_replaced(self, new_text):
        item = self._pending_item
        if item is None:
            return

        self._ignore_item_changed = True
        item.setText(new_text)
        self._ignore_item_changed = False
        self.update_item_font(item)
        self._pending_item = None

    def select_color(self):
        color = QColorDialog.getColor(QColor(self.grid_color), self, "Select grid line color")
        if color.isValid():
            self.grid_color = color.name()
            self._document_dirty = True
            self.apply_table_styles()
            try:
                self.save_config()
            except Exception:
                pass

    def select_background_color(self):
        color = QColorDialog.getColor(
            QColor(self.background_color), self, "Select background color"
        )
        if color.isValid():
            self.background_color = color.name()
            self._document_dirty = True
            self.apply_table_styles()
            self.save_config()

    def select_text_color(self):
        color = QColorDialog.getColor(
            QColor(self.text_color), self, "Select text color"
        )
        if color.isValid():
            self.text_color = color.name()
            self._document_dirty = True
            self.apply_table_styles()
            self.save_config()

    def apply_table_styles(self):
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {self.background_color};
                color: {self.text_color};
                gridline-color: {self.grid_color};
                border: none;
            }}
            QTableWidget::item {{
                background-color: {self.background_color};
                color: {self.text_color};
                border: none;
                border-bottom: 1px solid {self.grid_color};
                border-right: 1px solid {self.grid_color};
            }}
            QTableWidget QLineEdit {{
                background-color: {self.background_color};
                color: {self.text_color};
            }}
            QHeaderView::section {{
                background-color: {self.background_color};
                color: {self.text_color};
                border: none;
            }}
        """)

    # ---- Data folder persistence (registry on Windows, config-dir file elsewhere) ----

    def _cross_platform_pointer_path(self):
        import platform
        system = platform.system()
        if system == "Darwin":
            base = Path.home() / "Library" / "Application Support" / "GeoTables"
        else:  # Linux and anything else non-Windows
            base = Path.home() / ".config" / "geotables"
        base.mkdir(parents=True, exist_ok=True)
        return base / "data_dir.json"

    def save_data_dir_registry(self):
        if winreg is not None:
            key_path = r"Software\GeoTables"
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(
                        key,
                        "data_dir",
                        0,
                        winreg.REG_SZ,
                        str(self._app_dir)
                    )
            except OSError:
                pass
            return

        try:
            pointer_path = self._cross_platform_pointer_path()
            with pointer_path.open("w", encoding="utf-8") as f:
                json.dump({"data_dir": str(self._app_dir)}, f)
        except OSError:
            pass

    def load_data_dir_registry(self):
        if winreg is not None:
            key_path = r"Software\GeoTables"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    data_dir, _ = winreg.QueryValueEx(key, "data_dir")

                path = Path(data_dir)

                if self._data_dir_has_required_files(path):
                    return path

            except (FileNotFoundError, OSError):
                pass

            return None

        try:
            pointer_path = self._cross_platform_pointer_path()
            if pointer_path.exists():
                with pointer_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                saved = data.get("data_dir")
                if saved and self._data_dir_has_required_files(saved):
                    return Path(saved)
        except (OSError, json.JSONDecodeError):
            pass

        return None

    def _platform_folder_prefix(self):
        import platform
        system = platform.system()
        if system == "Windows":
            return "Geotables_windows"
        if system == "Darwin":
            return "Geotables_macOS"
        return "Geotables_linux"

    def _guess_data_dir(self):
        prefix = self._platform_folder_prefix()
        search_roots = [
            APP_BASE_DIR,
            APP_BASE_DIR.parent,
            Path.home(),
            Path.home() / "Downloads",
            Path.home() / "Desktop",
        ]

        candidate_names = [prefix] + [f"{prefix} ({n})" for n in range(1, 7)]

        for root in search_roots:
            if not root.is_dir():
                continue
            for name in candidate_names:
                candidate = root / name
                if candidate.is_dir() and self._data_dir_has_required_files(candidate):
                    return candidate

        return None

    def _data_dir_has_required_files(self, directory):
        directory = Path(directory)
        return (directory / "config.json").exists() and (directory / "justifications.json").exists()

    def load_data_dir(self):
        script_dir = APP_BASE_DIR

        # Check the OS-level saved data folder (registry on Windows,
        # config-dir pointer file on macOS/Linux).
        saved = self.load_data_dir_registry()
        if saved is not None:
            return saved

        # Try well-known platform-named folders
        guessed = self._guess_data_dir()
        if guessed is not None:
            self._app_dir = guessed
            self.save_data_dir_registry()
            return guessed

        # Fall back to the script's own folder if it already has what's needed
        if self._data_dir_has_required_files(script_dir):
            self._app_dir = script_dir
            self.save_data_dir_registry()
            return script_dir

        # Nothing worked
        return None

    def prompt_for_data_dir(self):
        QMessageBox.information(
            self,
            "GeoTables data folder needed",
            "GeoTables couldn't find config.json and/or justifications.json.\n\n"
            "Please select the folder containing the GeoTables app files."
        )
        chosen_dir = QFileDialog.getExistingDirectory(
            self, "Select GeoTables data folder", str(APP_BASE_DIR)
        )
        if not chosen_dir:
            return

        self._app_dir = Path(chosen_dir)
        self.backend = Backend(self._app_dir)
        self.load_config()
        self.apply_table_styles()
        self.save_config()
        self.save_data_dir_registry()

    def load_config(self):
        try:
            if not self._config_path.exists():
                # write defaults
                self.save_config()
                return

            with self._config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.seq_before = data.get("seq_before", self.seq_before)
            self.seq_between = data.get("seq_between", self.seq_between)
            self.seq_after = data.get("seq_after", self.seq_after)
            self.grid_color = data.get("grid_color", self.grid_color)
            self.background_color = data.get("background_color", self.background_color)
            self.text_color = data.get("text_color", self.text_color)
        except Exception:
            # ignore errors and keep defaults
            pass

    def save_data_dir_to_config(self):
        self.save_config()
        QMessageBox.information(
            self,
            "Data folder saved",
            f"Saved to config.json:\n{self._app_dir}"
        )

    def save_data_dir_to_registry(self):
        self.save_data_dir_registry()
        QMessageBox.information(
            self,
            "Data folder saved",
            f"Saved to Windows Registry:\n{self._app_dir}"
        )

    def save_config(self):
        try:
            data = {}
            if self._config_path.exists():
                try:
                    with self._config_path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            data["data_dir"] = str(self._app_dir)
            data["seq_before"] = self.seq_before
            data["seq_between"] = self.seq_between
            data["seq_after"] = self.seq_after
            data["grid_color"] = self.grid_color
            data["background_color"] = self.background_color
            data["text_color"] = self.text_color

            with self._config_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception:
            pass

    def export_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Config",
            "geotables_config.json",
            "JSON files (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"

        data = {
            "data_dir": str(self._app_dir),
            "seq_before": self.seq_before,
            "seq_between": self.seq_between,
            "seq_after": self.seq_after,
            "grid_color": self.grid_color,
            "background_color": self.background_color,
            "text_color": self.text_color,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except OSError as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return

        QMessageBox.information(self, "Config exported", path)

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Config",
            "",
            "JSON files (*.json)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return

        if not isinstance(data, dict):
            QMessageBox.critical(self, "Import failed", "Config file must contain a JSON object.")
            return

        saved_dir = data.get("data_dir")
        if saved_dir:
            if self._data_dir_has_required_files(saved_dir):
                self._app_dir = Path(saved_dir)
                self.backend = Backend(self._app_dir)
            else:
                QMessageBox.warning(
                    self,
                    "Data folder not found",
                    f"The imported config points to:\n{saved_dir}\n\n"
                    "That folder doesn't have config.json/justifications.json, "
                    "so the current data folder was kept."
                )

        self.seq_before = data.get("seq_before", self.seq_before)
        self.seq_between = data.get("seq_between", self.seq_between)
        self.seq_after = data.get("seq_after", self.seq_after)
        self.grid_color = data.get("grid_color", self.grid_color)
        self.background_color = data.get("background_color", self.background_color)
        self.text_color = data.get("text_color", self.text_color)

        self.apply_table_styles()
        self.save_config()

        QMessageBox.information(self, "Config imported", "Configuration imported successfully.")

    def get_language_font(self, text):
        if any('\u0590' <= ch <= '\u05FF' for ch in text):
            return QFont("Arial", 14)

        return QFont("CMU Classical Serif", 14)

    def create_text_item(self, text):
        item = QTableWidgetItem(text)
        item.setFont(self.get_language_font(text))
        return item

    def update_item_font(self, item):
        if item.column() == self.table.columnCount() - 1:
            return
        item.setFont(self.get_language_font(item.text()))

    def add_row(self, text, after_row=None):
        row = self.table.rowCount() if after_row is None else after_row + 1
        self.table.insertRow(row)

        # Put the polygon label in column 0 and the statement in column 1
        self.table.setItem(row, 0, self.create_text_item(""))
        self.table.setItem(row, 1, self.create_text_item(text))
        statement_item = self.create_text_item("")
        statement_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 2, statement_item)

        # Put the step/number in the last column
        num_col = self.table.columnCount() - 1

        num_item = QTableWidgetItem("")
        num_item.setTextAlignment(Qt.AlignCenter)
        # Make the cell non-editable but still selectable (so clicks work)
        num_item.setFlags(num_item.flags() & ~Qt.ItemIsEditable)

        self.table.setItem(row, num_col, num_item)

        self.renumber_rows()

    def renumber_rows(self):
        for row in range(self.table.rowCount()):
            num_item = self.table.item(row, self.table.columnCount() - 1)
            if num_item is not None:
                num_item.setText(str(row + 1))
        self.step = self.table.rowCount() + 1

    def on_number_clicked(self, row, col):
        # Only handle clicks on the number column (last column)
        num_col = self.table.columnCount() - 1
        if col != num_col:
            return

        num_item = self.table.item(row, col)
        if num_item is None:
            return

        num_text = num_item.text()
        # Insert into the last edited row if available, otherwise current
        target_row = getattr(self, "_active_row", None)
        if target_row is None or target_row < 0:
            target_row = row

        # If starting a new sequence for a different row, reset state
        if self._sequence_row != target_row:
            self._sequence_row = target_row
            self._sequence_values = []
            self._sequence_limits = []
            self._sequence_placeholders = []
            self._sequence_base = None

        # Ensure a justification item exists in the target row
        just_item = self.table.item(target_row, 1)
        if just_item is None:
            just_item = self.create_text_item("")
            self.table.setItem(target_row, 1, just_item)

        cur = just_item.text() or ""
        # Build numbered placeholders such as (2), each with a fixed capacity.
        if self._sequence_base is None:
            matches = list(re.finditer(r"\((\d+)\)", cur))
            if matches:
                self._sequence_limits = [int(match.group(1)) for match in matches]
                self._sequence_values = [[] for _ in matches]
                self._sequence_placeholders = [match.group(0) for match in matches]
                marker_index = 0

                def replace_placeholder(_match):
                    nonlocal marker_index
                    marker = f"<<<SEQ{marker_index}>>>"
                    marker_index += 1
                    return marker

                base = re.sub(r"\((\d+)\)", replace_placeholder, cur)
            else:
                # With no numbered placeholder, keep appending an unlimited list.
                base = cur + "<<<SEQ0>>>"
                self._sequence_limits = [None]
                self._sequence_values = [[]]
                self._sequence_placeholders = [""]
            self._sequence_base = base

        target_slot = next(
            (
                index
                for index, limit in enumerate(self._sequence_limits)
                if limit is None or len(self._sequence_values[index]) < limit
            ),
            None,
        )
        if target_slot is None:
            return

        self._sequence_values[target_slot].append(num_text)

        new = self._sequence_base
        for index, values in enumerate(self._sequence_values):
            if not values and self._sequence_limits[index] is not None:
                replacement = self._sequence_placeholders[index]
            else:
                replacement = (
                    f"{self.seq_before}{self.seq_between.join(values)}{self.seq_after}"
                )
            new = new.replace(f"<<<SEQ{index}>>>", replacement)

        # Set text synchronously to avoid signal races from async replacement
        try:
            normalized = self.backend.replace_all_special(new)
        except Exception:
            normalized = new

        self._ignore_item_changed = True
        just_item.setText(normalized)
        self._ignore_item_changed = False
        self.update_item_font(just_item)



app = QApplication(sys.argv)

window = GeoTables()
window.showMaximized()

sys.exit(app.exec())
