"""Profiles page managing modpack directories, snapshots, and configurations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mmis.domain.errors import DuplicateProfileError, ProfileNotFoundError
from mmis.domain.profiles import ProfileStatus, ProfileSummary
from mmis.services.profiles import ProfileService
from mmis.ui import strings
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.confirm_dialog import ConfirmDialog
from mmis.ui.widgets.elided_label import ElidedLabel
from mmis.ui.widgets.state_card import PageState, StateCard
from mmis.ui.widgets.status_pill import StatusPillWidget


class ProfileCard(QFrame):
    """Card widget representing a single configured modpack profile."""

    scan_clicked = Signal(str)
    rename_clicked = Signal(str)
    change_path_clicked = Signal(str)
    delete_clicked = Signal(str)

    def __init__(self, summary: ProfileSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self.setProperty("card", "true")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_MD, tokens.SPACE_LG, tokens.SPACE_MD)
        layout.setSpacing(tokens.SPACE_SM)

        # Header row: Profile Name + Status Pill
        header_layout = QHBoxLayout()
        header_layout.setSpacing(tokens.SPACE_SM)

        self._name_label = QLabel(summary.name)
        self._name_label.setProperty("heading", "true")
        name_font = self._name_label.font()
        name_font.setPointSize(tokens.FONT_SIZE_LG)
        name_font.setWeight(QFont.Weight.Bold)
        self._name_label.setFont(name_font)
        header_layout.addWidget(self._name_label)

        # Map domain ProfileStatus to 1:1 pill status string
        pill_status_map = {
            ProfileStatus.OK: "healthy",
            ProfileStatus.WARNINGS: "warnings",
            ProfileStatus.ERRORS: "errors",
            ProfileStatus.NEVER_SCANNED: "never_scanned",
            ProfileStatus.PATH_MISSING: "path_missing",
        }
        pill_status = pill_status_map.get(summary.status, "healthy")
        self._status_pill = StatusPillWidget(pill_status, self)
        header_layout.addWidget(self._status_pill)

        header_layout.addStretch()

        # Action Buttons row: Scan (secondary), Rename, Change Path, Delete
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(tokens.SPACE_XS)

        self._scan_btn = QPushButton(f" {strings.PROFILES_CARD_SCAN_BTN}")
        self._scan_btn.setIcon(icons.get_tinted_icon("scan", color_hex=tokens.TEXT_PRIMARY, size=14))
        self._scan_btn.clicked.connect(lambda: self.scan_clicked.emit(self.summary.name))
        if summary.status == ProfileStatus.PATH_MISSING:
            self._scan_btn.setEnabled(False)
            self._scan_btn.setToolTip("Profile directory path does not exist")
        actions_layout.addWidget(self._scan_btn)

        self._rename_btn = QPushButton(f" {strings.PROFILES_CARD_RENAME_BTN}")
        self._rename_btn.setIcon(icons.get_tinted_icon("edit", color_hex=tokens.TEXT_PRIMARY, size=14))
        self._rename_btn.clicked.connect(lambda: self.rename_clicked.emit(self.summary.name))
        actions_layout.addWidget(self._rename_btn)

        self._change_path_btn = QPushButton(f" {strings.PROFILES_CARD_CHANGE_PATH_BTN}")
        self._change_path_btn.setIcon(icons.get_tinted_icon("folder", color_hex=tokens.TEXT_PRIMARY, size=14))
        self._change_path_btn.clicked.connect(lambda: self.change_path_clicked.emit(self.summary.name))
        actions_layout.addWidget(self._change_path_btn)

        self._delete_btn = QPushButton()
        self._delete_btn.setProperty("destructive", "true")
        self._delete_btn.setIcon(icons.get_tinted_icon("trash", color_hex=tokens.COLOR_ERROR, size=14))
        self._delete_btn.setToolTip("Delete profile")
        self._delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.summary.name))
        actions_layout.addWidget(self._delete_btn)

        header_layout.addLayout(actions_layout)

        layout.addLayout(header_layout)

        # Path line
        path_layout = QHBoxLayout()
        path_layout.setSpacing(tokens.SPACE_XS)
        path_title = QLabel(strings.PROFILES_CARD_PATH_LABEL)
        path_title.setProperty("subheading", "true")
        path_layout.addWidget(path_title)

        self._path_label = ElidedLabel(summary.path, mode=Qt.TextElideMode.ElideMiddle, parent=self)
        self._path_label.setProperty("code", "true")
        path_layout.addWidget(self._path_label, 1)
        layout.addLayout(path_layout)

        # Meta info: Last scan timestamp, mod count, scan history count
        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(tokens.SPACE_LG)

        last_scan_text = f"{strings.PROFILES_CARD_LAST_SCAN} {summary.last_scanned_at[:16].replace('T', ' ')}" if summary.last_scanned_at else strings.PROFILES_CARD_NEVER_SCANNED
        self._scan_info_lbl = QLabel(last_scan_text)
        self._scan_info_lbl.setProperty("caption", "true")
        meta_layout.addWidget(self._scan_info_lbl)

        mods_text = f"{summary.mod_count or 0} {strings.PROFILES_CARD_MODS_COUNT}"
        self._mods_lbl = QLabel(mods_text)
        self._mods_lbl.setProperty("caption", "true")
        meta_layout.addWidget(self._mods_lbl)

        scans_count_text = f"{summary.scan_count} snapshots recorded"
        self._scans_lbl = QLabel(scans_count_text)
        self._scans_lbl.setProperty("caption", "true")
        meta_layout.addWidget(self._scans_lbl)

        meta_layout.addStretch()
        layout.addLayout(meta_layout)

    def set_scanning(self, is_scanning: bool) -> None:
        if is_scanning:
            self._scan_btn.setEnabled(False)
            self._scan_btn.setText(strings.TOPBAR_SCANNING_BTN)
            self._rename_btn.setEnabled(False)
            self._change_path_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
        else:
            self._scan_btn.setEnabled(True)
            self._scan_btn.setText(strings.PROFILES_CARD_SCAN_BTN)
            self._rename_btn.setEnabled(True)
            self._change_path_btn.setEnabled(True)
            self._delete_btn.setEnabled(True)


class AddProfileDialog(QDialog):
    """Dialog for creating a new profile with directory validation."""

    def __init__(self, service: ProfileService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(strings.DIALOG_ADD_TITLE)
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        layout.setSpacing(tokens.SPACE_MD)

        # Name field
        name_lbl = QLabel(strings.DIALOG_NAME_LABEL)
        name_lbl.setProperty("subheading", "true")
        layout.addWidget(name_lbl)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. All The Mods 9")
        layout.addWidget(self._name_edit)

        # Path field
        path_lbl = QLabel(strings.DIALOG_PATH_LABEL)
        path_lbl.setProperty("subheading", "true")
        layout.addWidget(path_lbl)

        path_row = QHBoxLayout()
        path_row.setSpacing(tokens.SPACE_SM)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("C:/Users/.../curseforge/minecraft/Instances/...")
        path_row.addWidget(self._path_edit)

        browse_btn = QPushButton(strings.DIALOG_BROWSE_BTN)
        browse_btn.clicked.connect(self._browse_folder)
        path_row.addWidget(browse_btn)

        layout.addLayout(path_row)

        # Inline error display
        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-size: {tokens.FONT_SIZE_SM}px;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton(strings.DIALOG_CANCEL_BTN)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._save_btn = QPushButton(strings.DIALOG_SAVE_BTN)
        self._save_btn.setProperty("primary", "true")
        self._save_btn.clicked.connect(self._validate_and_save)
        btn_row.addWidget(self._save_btn)

        layout.addLayout(btn_row)

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, strings.DIALOG_SELECT_FOLDER, "")
        if chosen:
            self._path_edit.setText(chosen)

    def _validate_and_save(self) -> None:
        name = self._name_edit.text().strip()
        path = self._path_edit.text().strip()

        if not name:
            self._error_label.setText(strings.ERR_EMPTY_NAME)
            self._error_label.setVisible(True)
            return

        if not path:
            self._error_label.setText(strings.ERR_EMPTY_PATH)
            self._error_label.setVisible(True)
            return

        if not os.path.exists(path) or not os.path.isdir(path):
            self._error_label.setText(strings.ERR_PATH_NOT_DIR)
            self._error_label.setVisible(True)
            return

        try:
            self.service.add_profile(name, path)
            self.accept()
        except DuplicateProfileError:
            self._error_label.setText(strings.ERR_DUPLICATE_NAME)
            self._error_label.setVisible(True)
        except Exception as exc:
            self._error_label.setText(str(exc))
            self._error_label.setVisible(True)


class RenameProfileDialog(QDialog):
    """Dialog for renaming an existing profile."""

    def __init__(self, service: ProfileService, old_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.old_name = old_name
        self.setWindowTitle(strings.DIALOG_RENAME_TITLE)
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        layout.setSpacing(tokens.SPACE_MD)

        lbl = QLabel(strings.DIALOG_NAME_LABEL)
        lbl.setProperty("subheading", "true")
        layout.addWidget(lbl)

        self._edit = QLineEdit(old_name)
        layout.addWidget(self._edit)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-size: {tokens.FONT_SIZE_SM}px;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton(strings.DIALOG_CANCEL_BTN)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton(strings.DIALOG_SAVE_BTN)
        save_btn.setProperty("primary", "true")
        save_btn.clicked.connect(self._validate_and_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _validate_and_save(self) -> None:
        new_name = self._edit.text().strip()
        if not new_name:
            self._error_label.setText(strings.ERR_EMPTY_NAME)
            self._error_label.setVisible(True)
            return
        if new_name == self.old_name:
            self.accept()
            return
        try:
            self.service.rename_profile(self.old_name, new_name)
            self.accept()
        except DuplicateProfileError:
            self._error_label.setText(strings.ERR_DUPLICATE_NAME)
            self._error_label.setVisible(True)
        except Exception as exc:
            self._error_label.setText(str(exc))
            self._error_label.setVisible(True)


class ChangePathDialog(QDialog):
    """Dialog for modifying an existing profile's directory path."""

    def __init__(self, service: ProfileService, profile_name: str, current_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.profile_name = profile_name
        self.setWindowTitle(strings.DIALOG_CHANGE_PATH_TITLE)
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        layout.setSpacing(tokens.SPACE_MD)

        lbl = QLabel(strings.DIALOG_PATH_LABEL)
        lbl.setProperty("subheading", "true")
        layout.addWidget(lbl)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(current_path)
        path_row.addWidget(self._path_edit)

        browse_btn = QPushButton(strings.DIALOG_BROWSE_BTN)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-size: {tokens.FONT_SIZE_SM}px;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton(strings.DIALOG_CANCEL_BTN)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton(strings.DIALOG_SAVE_BTN)
        save_btn.setProperty("primary", "true")
        save_btn.clicked.connect(self._validate_and_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, strings.DIALOG_SELECT_FOLDER, self._path_edit.text())
        if chosen:
            self._path_edit.setText(chosen)

    def _validate_and_save(self) -> None:
        new_path = self._path_edit.text().strip()
        if not new_path:
            self._error_label.setText(strings.ERR_EMPTY_PATH)
            self._error_label.setVisible(True)
            return
        if not os.path.exists(new_path) or not os.path.isdir(new_path):
            self._error_label.setText(strings.ERR_PATH_NOT_DIR)
            self._error_label.setVisible(True)
            return
        try:
            self.service.update_profile_path(self.profile_name, new_path)
            self.accept()
        except Exception as exc:
            self._error_label.setText(str(exc))
            self._error_label.setVisible(True)


class ProfilesPage(QWidget):
    """Profiles management page showing cards for configured packs and action dialogs."""

    scan_requested = Signal(str)
    profiles_changed = Signal()

    def __init__(self, service: ProfileService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._cards: dict[str, ProfileCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        layout.setSpacing(tokens.SPACE_LG)

        # Header: Title + Add Profile action
        header_layout = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(tokens.SPACE_XXS)

        self._title_label = QLabel(strings.PROFILES_TITLE)
        self._title_label.setProperty("heading", "true")
        title_font = self._title_label.font()
        title_font.setPointSize(tokens.FONT_SIZE_XL)
        title_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(title_font)
        title_col.addWidget(self._title_label)

        self._subtitle_label = QLabel(strings.PROFILES_SUBTITLE)
        self._subtitle_label.setProperty("subheading", "true")
        title_col.addWidget(self._subtitle_label)

        header_layout.addLayout(title_col)
        header_layout.addStretch()

        self._add_btn = QPushButton(strings.PROFILES_ADD_BTN)
        self._add_btn.setProperty("primary", "true")
        self._add_btn.setIcon(icons.get_tinted_icon("add", color_hex=tokens.TEXT_ON_ACCENT, size=16))
        self._add_btn.clicked.connect(self._open_add_dialog)
        header_layout.addWidget(self._add_btn)

        layout.addLayout(header_layout)

        # State card (for empty state)
        self._state_card = StateCard(self)
        layout.addWidget(self._state_card)

        # Scrollable cards list
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, tokens.SPACE_SM, 0)
        self._cards_layout.setSpacing(tokens.SPACE_MD)
        self._cards_layout.addStretch()

        self._scroll_area.setWidget(self._cards_widget)
        layout.addWidget(self._scroll_area)

        self.refresh()

    def refresh(self) -> None:
        """Fetch profiles from service and rebuild card list."""
        profiles = self.service.list_profiles()

        # Clear existing cards
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()

        if not profiles:
            self._scroll_area.setVisible(False)
            self._state_card.setVisible(True)
            self._state_card.show_empty(
                title=strings.STATE_EMPTY_TITLE,
                description=strings.PROFILES_EMPTY_DESC,
                action_text=strings.PROFILES_ADD_BTN,
                on_action=self._open_add_dialog,
                icon_name="folder",
            )
        else:
            self._state_card.setVisible(False)
            self._scroll_area.setVisible(True)

            for summary in profiles:
                card = ProfileCard(summary, self._cards_widget)
                card.scan_clicked.connect(self.scan_requested.emit)
                card.rename_clicked.connect(self._open_rename_dialog)
                card.change_path_clicked.connect(self._open_change_path_dialog)
                card.delete_clicked.connect(self._confirm_delete)

                self._cards[summary.name] = card
                self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

    def set_scanning_profile(self, name: str, is_scanning: bool) -> None:
        card = self._cards.get(name)
        if card:
            card.set_scanning(is_scanning)

    def _open_add_dialog(self) -> None:
        dlg = AddProfileDialog(self.service, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.profiles_changed.emit()

    def _open_rename_dialog(self, name: str) -> None:
        dlg = RenameProfileDialog(self.service, name, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.profiles_changed.emit()

    def _open_change_path_dialog(self, name: str) -> None:
        summary = self.service.get_profile(name)
        dlg = ChangePathDialog(self.service, name, summary.path, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.profiles_changed.emit()

    def _confirm_delete(self, name: str) -> None:
        dlg = ConfirmDialog(
            title=strings.DIALOG_DELETE_CONFIRM_TITLE,
            message=strings.DIALOG_DELETE_CONFIRM_PROMPT.format(name=name),
            warning_note=strings.DIALOG_DELETE_CONFIRM_WARNING,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.service.remove_profile(name)
            self.refresh()
            self.profiles_changed.emit()
