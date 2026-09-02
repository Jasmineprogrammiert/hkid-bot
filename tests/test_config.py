"""Settings are checked at load, so a local typo is never blamed on Immigration.

find_openings() indexes these keys straight off the config. Left unchecked, a
missing one surfaced there as a KeyError, which check.py reports as 'schema may
have changed' -- pointing at the wrong culprit entirely."""

import pytest

from watcher.config import parse_offices, validate


def good(**over):
    cfg = {"target_date": "2026-10-09", "offices": ["RHK"],
           "include_almost_full": True, "include_target_day": True}
    cfg.update(over)
    return cfg


def test_a_complete_config_passes():
    assert validate(good()) is None


@pytest.mark.parametrize("missing", ["offices", "include_almost_full", "include_target_day"])
def test_a_missing_key_is_named(missing):
    cfg = good()
    del cfg[missing]
    with pytest.raises(SystemExit) as exc:
        validate(cfg)
    assert missing in str(exc.value)


def test_a_mistyped_key_says_what_it_should_be():
    with pytest.raises(SystemExit) as exc:
        validate(good(offices="RHK"))          # a string, not a list of one
    assert "should be a list" in str(exc.value)


def test_an_empty_office_list_can_never_match():
    with pytest.raises(SystemExit) as exc:
        validate(good(offices=[]))
    assert "nothing can ever match" in str(exc.value)


def test_an_unknown_office_code_is_rejected():
    """It would match nothing, and silence is this tool's normal state -- so a
    typo would be indistinguishable from "no slots" until you went looking."""
    with pytest.raises(SystemExit) as exc:
        validate(good(offices=["RHK", "XYZ"]))
    assert "XYZ" in str(exc.value)
    assert "RHK" in str(exc.value)          # and it says what is valid


@pytest.mark.parametrize("raw, expected", [
    ("RHK,RKO", ["RHK", "RKO"]),
    (" rhk , rko ", ["RHK", "RKO"]),        # case and spacing forgiven
    ("RHK,,RKO,", ["RHK", "RKO"]),          # a stray comma is not an office
    ("RHK", ["RHK"]),
])
def test_the_offices_override_parses(raw, expected):
    assert parse_offices(raw) == expected


def test_a_target_date_in_the_wrong_format_is_caught_here():
    """Otherwise it raises inside find_openings and gets reported as a feed
    schema change, which sends you looking at the wrong system."""
    with pytest.raises(SystemExit) as exc:
        validate(good(target_date="09/10/2026"))
    assert "not YYYY-MM-DD" in str(exc.value)