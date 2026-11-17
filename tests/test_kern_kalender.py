"""Kalender: ISO-week/dag, week 53 en de weergave week-dag (BOUWPLAN §4.4)."""

from datetime import date

import pytest

from teeltplanning.kern.kalender import (
    eerstvolgende_toegestane_dag,
    lees_week_dag,
    van_week_dag,
    waarde_op,
    week_dag,
    weergave,
    weken_in_jaar,
)
from teeltplanning.kern.typen import Blokkade

MA_ZA = frozenset({1, 2, 3, 4, 5, 6})


def test_week_53_van_2026_bestaat():
    assert weken_in_jaar(2026) == 53 and weken_in_jaar(2025) == 52 and weken_in_jaar(2020) == 53
    assert van_week_dag(2026, 53, 1) == date(2026, 12, 28)
    assert van_week_dag(2026, 53, 7) == date(2027, 1, 3)
    assert week_dag(date(2027, 1, 3)) == (2026, 53, 7)
    assert week_dag(date(2027, 1, 4)) == (2027, 1, 1)
    assert van_week_dag(2025, 53, 1) is None


def test_weergave_en_lezen():
    assert weergave(date(2026, 10, 31)) == "44-6"
    assert weergave(date(2026, 10, 31), "week-dag-jaar") == "44-6 (2026)"
    assert weergave(None) == ""
    assert lees_week_dag("44-6", date(2026, 9, 1)) == date(2026, 10, 31)
    assert lees_week_dag("53-1", date(2026, 12, 1)) == date(2026, 12, 28)
    assert lees_week_dag("1-1", date(2026, 12, 28)) == date(2027, 1, 4)  # dichtstbij, over de jaarwisseling
    assert lees_week_dag("44-6-2025", date(2026, 1, 1)) == date(2025, 11, 1)
    for fout in ("44", "44-8", "abc", "53-1-2025"):
        with pytest.raises(ValueError):
            lees_week_dag(fout, date(2026, 1, 1))


def test_eerstvolgende_toegestane_plantdag():
    zondag = date(2026, 11, 15)
    assert eerstvolgende_toegestane_dag(zondag, MA_ZA) == date(2026, 11, 16)
    assert eerstvolgende_toegestane_dag(date(2026, 11, 17), MA_ZA) == date(2026, 11, 17)
    stomen = (Blokkade(date(2026, 11, 16), date(2026, 11, 18), kap=3),)
    assert eerstvolgende_toegestane_dag(zondag, MA_ZA, stomen, kap=3) == date(2026, 11, 19)
    assert eerstvolgende_toegestane_dag(zondag, MA_ZA, stomen, kap=4) == date(2026, 11, 16)
    assert eerstvolgende_toegestane_dag(zondag, frozenset()) is None


def test_waarde_op_ingangsdatum():
    reeks = [(None, None), (date(2020, 1, 1), 726.0), (date(2025, 1, 1), 750.0)]
    assert waarde_op(reeks, date(2019, 6, 1)) is None
    assert waarde_op(reeks, date(2024, 12, 31)) == 726.0
    assert waarde_op(reeks, date(2025, 1, 1)) == 750.0
