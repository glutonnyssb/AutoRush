"""Fonctions utilitaires : temps, texte, intervalles."""

from __future__ import annotations

import pytest

from autorush.utils import (
    ends_with_suspension,
    ends_with_terminal_punct,
    format_smpte,
    format_timecode,
    human_duration,
    intervals_duration,
    merge_intervals,
    normalize_text,
    normalize_word,
    parse_timecode,
    remap,
    safe_filename,
    subtract_intervals,
    tokenize,
)


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00"), (27, "00:27"), (93.4, "01:33"), (3725.2, "1:02:05")],
)
def test_format_timecode(seconds, expected):
    assert format_timecode(seconds) == expected


def test_format_smpte():
    assert format_smpte(0, 25) == "00:00:00:00"
    assert format_smpte(1.0, 25) == "00:00:01:00"
    assert format_smpte(93.4, 25) == "00:01:33:10"


def test_parse_timecode():
    assert parse_timecode("1:33") == 93.0
    assert parse_timecode("1:02:03") == 3723.0
    assert parse_timecode("12.5") == 12.5


def test_normalize_word_removes_accents_and_punctuation():
    assert normalize_word("Malgr\u00e9,") == "malgre"
    assert normalize_word("TR\u00c8S") == "tres"
    assert normalize_word("j\u2019ai") == "j'ai"
    assert normalize_word("Mal-") == "mal-"


def test_normalize_text_and_tokenize():
    assert normalize_text("Malgr\u00e9 \u00e7a, c'\u00e9tait TR\u00c8S fort...") == (
        "malgre ca c'etait tres fort"
    )
    assert tokenize("Je je pense que...") == ["je", "je", "pense", "que"]


def test_suspension_is_not_terminal_punctuation():
    """``Raru...`` est une phrase en suspens, pas une phrase finie."""
    assert ends_with_suspension("Raru...")
    assert not ends_with_terminal_punct("Raru...")
    assert ends_with_terminal_punct("Bonjour.")
    assert ends_with_terminal_punct("Quoi ?")
    assert not ends_with_terminal_punct("plut\u00f4t\u2026")


def test_merge_intervals():
    assert merge_intervals([(0, 1), (0.9, 2), (3, 4)]) == [(0.0, 2.0), (3.0, 4.0)]
    assert merge_intervals([]) == []


def test_subtract_intervals():
    assert subtract_intervals([(0, 10)], [(2, 3), (5, 6)]) == [
        (0.0, 2.0),
        (3.0, 5.0),
        (6.0, 10.0),
    ]
    assert subtract_intervals([(0, 10)], [(0, 10)]) == []


def test_intervals_duration():
    assert intervals_duration([(0, 2), (4, 5.5)]) == pytest.approx(3.5)


def test_remap_is_clamped():
    assert remap(50, 0, 100, 0, 10) == pytest.approx(5.0)
    assert remap(-20, 0, 100, 0, 10) == pytest.approx(0.0)
    assert remap(500, 0, 100, 0, 10) == pytest.approx(10.0)


def test_human_duration():
    assert human_duration(12.4) == "12.4 s"
    assert human_duration(95) == "1 min 35 s"
    assert human_duration(3725) == "1 h 02 min"


def test_safe_filename():
    assert safe_filename('mon: rush/"1"') == "mon_ rush__1_"
    assert safe_filename("") == "sortie"
