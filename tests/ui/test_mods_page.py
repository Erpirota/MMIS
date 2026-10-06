"""Mods page table, filtering, drawer, and 1,000-row performance tests (A6)."""

from __future__ import annotations

import time

from mmis.domain.models import Loader, ModInfo
from mmis.services.pack_view import FilterCriteria, ModRow, filter_and_sort


def _make_sample_rows() -> list[ModRow]:
    rows = []
    for i in range(20):
        rows.append(
            ModRow(
                mod_id=f"mod_{i}",
                name=f"Mod Name {i}",
                version=f"1.{i}.0",
                loader="fabric" if i % 2 == 0 else "forge",
                jar_path=f"mods/mod_{i}.jar",
                jar_name=f"mod_{i}.jar",
                is_disabled=(i == 5),
                is_nested=(i == 7),
                status="error" if i == 0 else ("warning" if i == 1 else "ok"),
                issue_count=2 if i == 0 else (1 if i == 1 else 0),
                error_count=2 if i == 0 else 0,
                warning_count=1 if i == 1 else 0,
                mod_info=ModInfo(id=f"mod_{i}", name=f"Mod Name {i}", version=f"1.{i}.0"),
            )
        )
    return rows


def test_mods_page_filtering_and_drawer(qapp):
    from mmis.ui import strings
    from mmis.ui.pages.mods import ModsPage
    from mmis.ui.widgets.state_card import PageState

    page = ModsPage()
    page.show()

    rows = _make_sample_rows()
    page._all_rows = rows
    page.set_state(PageState.READY)
    page.reset_filters()

    assert page._model.rowCount() == 20
    assert page._count_label.text() == strings.MODS_SHOWING_COUNT.format(visible=20, total=20)

    # Search filter
    page._search_input.setText("Mod Name 3")
    assert page._model.rowCount() == 1
    assert page._model.get_row(0).mod_id == "mod_3"

    # Reset search
    page._search_input.clear()
    assert page._model.rowCount() == 20

    # Status filter
    page._status_combo.setCurrentText(strings.MODS_STATUS_ERROR)
    assert page._model.rowCount() == 1
    assert page._model.get_row(0).mod_id == "mod_0"

    page._status_combo.setCurrentText(strings.MODS_STATUS_ALL)
    assert page._model.rowCount() == 20

    # Issues only checkbox
    page._issues_check.setChecked(True)
    assert page._model.rowCount() == 2  # mod_0 and mod_1
    page._issues_check.setChecked(False)

    # Drawer open on row selection
    assert not page.is_drawer_open()
    page.open_drawer(rows[0])
    assert page.is_drawer_open()
    assert page._drawer._mod_title.text() == f"{rows[0].name} v{rows[0].version}"

    # Close drawer
    page.close_drawer()
    assert not page.is_drawer_open()

    # Empty result state
    page._search_input.setText("unmatched_query_xyz")
    assert page._model.rowCount() == 0
    assert page._empty_filter_card.isVisible()
    assert not page._table.isVisible()

    page.close()


def test_1000_row_performance_bound():
    """Verify that filtering and sorting 1,000 mod rows completes within a tight time bound."""
    large_rows = []
    for i in range(1000):
        large_rows.append(
            ModRow(
                mod_id=f"benchmark_mod_{i}",
                name=f"Benchmark Mod {i}",
                version=f"{i % 10}.0.0",
                loader="fabric" if i % 2 == 0 else "forge",
                jar_path=f"mods/benchmark_{i}.jar",
                jar_name=f"benchmark_{i}.jar",
                is_disabled=(i % 50 == 0),
                is_nested=(i % 20 == 0),
                status="error" if i % 30 == 0 else ("warning" if i % 15 == 0 else "ok"),
                issue_count=i % 4,
                error_count=1 if i % 30 == 0 else 0,
                warning_count=1 if i % 15 == 0 else 0,
                mod_info=ModInfo(id=f"benchmark_mod_{i}", name=f"Benchmark Mod {i}"),
            )
        )

    criteria = FilterCriteria(search_text="Benchmark Mod 5", loader="fabric", sort_key="name", sort_ascending=True)

    start = time.perf_counter()
    result = filter_and_sort(large_rows, criteria)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    # Must complete comfortably under 100ms
    assert elapsed_ms < 100.0, f"Filter & sort took too long: {elapsed_ms:.2f}ms"
    assert len(result) > 0
