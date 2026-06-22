import sys
from pathlib import Path
import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMenu
from PySide6.QtCore import Qt, QAbstractTableModel
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFileSystemModel,
    QTreeView,
    QTableView,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QMessageBox,
    QInputDialog,
    QSplitter,
)
import argparse

# ==========================================================
# Pandas Model
# ==========================================================

class PandasModel(QAbstractTableModel):

    def __init__(self, df=None):
        super().__init__()
        self.df = df if df is not None else pd.DataFrame()

    def rowCount(self, parent=None):
        return len(self.df.index)

    def columnCount(self, parent=None):
        return len(self.df.columns)

    def data(self, index, role=Qt.DisplayRole):

        if not index.isValid():
            return None

        if role in (Qt.DisplayRole, Qt.EditRole):
            value = self.df.iloc[index.row(), index.column()]

            if pd.isna(value):
                return ""

            col = self.df.columns[index.column()]

            if col == "timestamp":
                try:
                    dt = pd.to_datetime(value, unit="ns")
                    return dt.strftime("%Y-%b-%d %H:%M:%S.%f")[:-3]
                except Exception:
                    pass

            return str(value)

        return None

    def setData(self, index, value, role=Qt.EditRole):

        if role == Qt.EditRole:

            col = self.df.columns[index.column()]
            row = index.row()

            try:
                dtype = self.df[col].dtype

                if pd.api.types.is_integer_dtype(dtype):
                    value = int(value)

                elif pd.api.types.is_float_dtype(dtype):
                    value = float(value)

                self.df.at[row, col] = value

            except Exception:
                self.df.at[row, col] = value

            self.dataChanged.emit(index, index)
            return True

        return False

    def flags(self, index):
        return (
            Qt.ItemIsEnabled
            | Qt.ItemIsSelectable
            | Qt.ItemIsEditable
        )

    def headerData(self, section, orientation, role):

        if role != Qt.DisplayRole:
            return None

        if orientation == Qt.Horizontal:
            return str(self.df.columns[section])

        return str(section)

    def update_dataframe(self, df):
        self.beginResetModel()
        self.df = df
        self.endResetModel()


# ==========================================================
# Main Window
# ==========================================================

class RepositoryBrowser(QMainWindow):

    def __init__(self, open_file=None, select_timestamp=None):

        super().__init__()

        self.start_file = Path(open_file) if open_file else None
        self.start_timestamp = (
            int(select_timestamp)
            if select_timestamp
            else None
        )

        self.setWindowTitle("Repository Browser")
        self.resize(1400, 800)

        self.current_file = None
        self.df = pd.DataFrame()

        self.init_ui()

        if self.start_file and self.start_file.exists():

            self.load_file_path(self.start_file)

            if self.start_timestamp is not None:
                self.select_timestamp(
                    self.start_timestamp
                )

    def init_ui(self):

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)

        # toolbar buttons

        toolbar = QHBoxLayout()

        self.btn_add_row = QPushButton("+ Row")
        self.btn_del_row = QPushButton("- Row")
        self.btn_add_col = QPushButton("+ Column")
        self.btn_del_col = QPushButton("- Column")
        self.btn_save = QPushButton("Save")

        toolbar.addWidget(self.btn_add_row)
        toolbar.addWidget(self.btn_del_row)
        toolbar.addWidget(self.btn_add_col)
        toolbar.addWidget(self.btn_del_col)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_save)

        self.btn_fill = QPushButton("Fill Selected")
        toolbar.addWidget(self.btn_fill)
        self.btn_fill.clicked.connect(self.fill_selected_cells)

        layout.addLayout(toolbar)

        splitter = QSplitter()

        # ==========================================
        # repository tree
        # ==========================================

        self.tree_model = QFileSystemModel()

        repository_path = (
            Path(__file__).resolve().parent.parent
            / "DataRepository"
        )

        repository_path.mkdir(exist_ok=True)

        self.tree_model.setRootPath(str(repository_path))
        self.tree_model.setNameFilters(["*.parquet"])
        self.tree_model.setNameFilterDisables(False)

        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setRootIndex(
            self.tree_model.index(str(repository_path))
        )

        for i in range(1, 4):
            self.tree.hideColumn(i)

        splitter.addWidget(self.tree)

        # ==========================================
        # table
        # ==========================================

        self.table = QTableView()

        self.table.setSelectionBehavior(QTableView.SelectItems)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_table_menu)

        self.model = PandasModel(self.df)

        self.table.setModel(self.model)

        splitter.addWidget(self.table)

        splitter.setSizes([300, 1100])

        layout.addWidget(splitter)

        # signals

        self.tree.clicked.connect(self.load_file)

        self.btn_add_row.clicked.connect(self.add_row)
        self.btn_del_row.clicked.connect(self.delete_row)

        self.btn_add_col.clicked.connect(self.add_column)
        self.btn_del_col.clicked.connect(self.delete_column)

        self.btn_save.clicked.connect(self.save_file)

    # ======================================================
    # File Operations
    # ======================================================

    def load_file(self, index):

        file_path = Path(
            self.tree_model.filePath(index)
        )

        if not file_path.is_file():
            return

        if file_path.suffix.lower() != ".parquet":
            return

        try:

            self.df = pd.read_parquet(file_path)

            self.current_file = file_path

            self.model.update_dataframe(self.df)

            self.setWindowTitle(
                f"Repository Browser - {file_path.name}"
            )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Error",
                str(e)
            )

    def load_file_path(self, file_path: Path):

        try:
            self.df = pd.read_parquet(file_path)
            self.current_file = file_path
            self.model.update_dataframe(self.df)

            self.setWindowTitle(
                f"Repository Browser - {file_path.name}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


    def select_timestamp(self, timestamp_ns: int):

        if "timestamp" not in self.df.columns:
            return

        matches = self.df.index[self.df["timestamp"] == timestamp_ns].tolist()

        if not matches:
            # fallback: najdi nejbližší timestamp
            diff = (self.df["timestamp"] - timestamp_ns).abs()
            row = int(diff.idxmin())
        else:
            row = int(matches[0])

        index = self.model.index(row, 0)

        self.table.selectRow(row)
        self.table.scrollTo(index)
        self.table.setCurrentIndex(index)    

    def open_table_menu(self, pos):

        menu = QMenu(self)

        fill_action = menu.addAction("Fill selected cells...")
        nan_action = menu.addAction("Set selected cells to NaN")
        column_nan_action = menu.addAction("Set current column to NaN")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))

        if action == fill_action:
            self.fill_selected_cells()

        elif action == nan_action:
            self.set_selected_cells_nan()

        elif action == column_nan_action:
            self.set_current_column_nan()


    def fill_selected_cells(self):

        if self.current_file is None:
            return

        indexes = self.table.selectedIndexes()

        if not indexes:
            return

        text, ok = QInputDialog.getText(
            self,
            "Fill selected cells",
            "New value:"
        )

        if not ok:
            return

        value = self.parse_user_value(text)

        for index in indexes:
            row = index.row()
            col = index.column()
            col_name = self.df.columns[col]
            self.df.at[row, col_name] = value

        self.model.update_dataframe(self.df)


    def set_selected_cells_nan(self):

        indexes = self.table.selectedIndexes()

        if not indexes:
            return

        for index in indexes:
            row = index.row()
            col = index.column()
            col_name = self.df.columns[col]
            self.df.at[row, col_name] = np.nan

        self.model.update_dataframe(self.df)


    def set_current_column_nan(self):

        index = self.table.currentIndex()

        if not index.isValid():
            return

        col_name = self.df.columns[index.column()]

        answer = QMessageBox.question(
            self,
            "Set column to NaN",
            f"Set whole column '{col_name}' to NaN?"
        )

        if answer != QMessageBox.Yes:
            return

        self.df[col_name] = np.nan

        self.model.update_dataframe(self.df)


    def parse_user_value(self, text):

        text = text.strip()

        if text.lower() in ("nan", "none", "null", ""):
            return np.nan

        try:
            return int(text)
        except ValueError:
            pass

        try:
            return float(text)
        except ValueError:
            pass

        return text

    def save_file(self):

        if self.current_file is None:
            return

        try:

            self.df.to_parquet(
                self.current_file,
                index=False
            )

            QMessageBox.information(
                self,
                "Saved",
                "File saved successfully."
            )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Error",
                str(e)
            )
        self.df.replace({None: np.nan}, inplace=True)    

    # ======================================================
    # Row Operations
    # ======================================================

    def add_row(self):

        if self.df.empty and len(self.df.columns) == 0:
            return

        self.df.loc[len(self.df)] = [
            None
            for _ in self.df.columns
        ]

        self.model.update_dataframe(self.df)

    def delete_row(self):

        indexes = self.table.selectionModel().selectedRows()

        if not indexes:
            return

        rows = sorted(
            [idx.row() for idx in indexes],
            reverse=True
        )

        for row in rows:
            self.df.drop(index=row, inplace=True)

        self.df.reset_index(drop=True, inplace=True)

        self.model.update_dataframe(self.df)

    # ======================================================
    # Column Operations
    # ======================================================

    def add_column(self):

        if self.current_file is None:
            return

        name, ok = QInputDialog.getText(
            self,
            "Add Column",
            "Column name:"
        )

        if not ok or not name:
            return

        self.df[name] = None

        self.model.update_dataframe(self.df)

    def delete_column(self):

        if self.current_file is None:
            return

        columns = list(self.df.columns)

        if not columns:
            return

        name, ok = QInputDialog.getItem(
            self,
            "Delete Column",
            "Column:",
            columns,
            0,
            False
        )

        if not ok:
            return

        self.df.drop(columns=[name], inplace=True)

        self.model.update_dataframe(self.df)


# ==========================================================
# main
# ==========================================================

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=None)
    parser.add_argument("--timestamp", default=None)

    args = parser.parse_args()

    app = QApplication(sys.argv)

    window = RepositoryBrowser(
        open_file=args.file,
        select_timestamp=args.timestamp
    )

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()