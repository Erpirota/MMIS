"""Profiles page flow tests: adding, renaming, changing paths, removing (A4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mmis.services.paths import get_db_path
from mmis.services.profiles import ProfileService
from mmis.storage.repository import ProfileRepository


def test_add_profile_dialog_validation_and_duplicate(qapp, tmp_path: Path):
    from mmis.ui import strings
    from mmis.ui.pages.profiles import AddProfileDialog

    db_path = get_db_path(tmp_path / "data")
    service = ProfileService(ProfileRepository(db_path))

    pack_dir = tmp_path / "valid_pack"
    pack_dir.mkdir()

    dlg = AddProfileDialog(service)
    dlg.show()

    # Empty name validation
    dlg._name_edit.setText("")
    dlg._path_edit.setText(str(pack_dir))
    dlg._validate_and_save()
    assert not dlg._error_label.isHidden()
    assert dlg._error_label.text() == strings.ERR_EMPTY_NAME

    # Valid add
    dlg._name_edit.setText("Primary Pack")
    dlg._validate_and_save()
    assert service.get_profile("Primary Pack") is not None

    # Duplicate name validation
    dlg2 = AddProfileDialog(service)
    dlg2.show()
    dlg2._name_edit.setText("Primary Pack")
    dlg2._path_edit.setText(str(pack_dir))
    dlg2._validate_and_save()
    assert not dlg2._error_label.isHidden()
    assert dlg2._error_label.text() == strings.ERR_DUPLICATE_NAME

    dlg.close()
    dlg2.close()


def test_rename_and_change_path_dialogs(qapp, tmp_path: Path):
    from mmis.ui.pages.profiles import ChangePathDialog, RenameProfileDialog

    db_path = get_db_path(tmp_path / "data2")
    service = ProfileService(ProfileRepository(db_path))

    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    dir2 = tmp_path / "dir2"
    dir2.mkdir()

    service.add_profile("Original Name", dir1)

    # Rename
    r_dlg = RenameProfileDialog(service, "Original Name")
    r_dlg._edit.setText("Renamed Name")
    r_dlg._validate_and_save()
    assert service.get_profile("Renamed Name") is not None

    # Change path
    p_dlg = ChangePathDialog(service, "Renamed Name", str(dir1))
    p_dlg._path_edit.setText(str(dir2))
    p_dlg._validate_and_save()
    prof = service.get_profile("Renamed Name")
    assert Path(prof.path).resolve() == dir2.resolve()


def test_remove_profile_flow(qapp, tmp_path: Path):
    from PySide6.QtWidgets import QDialog
    from mmis.ui.shell import MainWindow
    from mmis.ui.widgets.confirm_dialog import ConfirmDialog

    db_path = get_db_path(tmp_path / "data3")
    service = ProfileService(ProfileRepository(db_path))

    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    service.add_profile("To Delete", dir1)

    window = MainWindow(service=service, data_dir=tmp_path / "data3")
    window.show()

    # Stub confirm dialog accept
    with patch.object(ConfirmDialog, "exec", return_value=QDialog.DialogCode.Accepted):
        window.profiles_page._confirm_delete("To Delete")

    assert len(service.list_profiles()) == 0
    window.close()
