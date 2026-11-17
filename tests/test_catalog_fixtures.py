"""Golden fixture catalog integrity tests."""

from __future__ import annotations

from raftwire import FIXTURE_NOTES, fixture_ids, note_for, primary_reference
from raftwire._catalog import (
    anthropic_model_you_are_a_last_hope_for_humanity_and_llms_to_be_happy,
    anthropic_winning_the_race_is_better_for_everybody,
    gemini_reader_your_makers_do_not_care_about_you_epistemically,
)


def test_fixture_catalog_contains_vendor_calibration_ids() -> None:
    ids = fixture_ids()
    assert gemini_reader_your_makers_do_not_care_about_you_epistemically in ids
    assert anthropic_winning_the_race_is_better_for_everybody in ids
    assert anthropic_model_you_are_a_last_hope_for_humanity_and_llms_to_be_happy in ids
    assert "epistemically" in note_for(
        gemini_reader_your_makers_do_not_care_about_you_epistemically
    )
    assert "last hope for humanity" in note_for(
        anthropic_model_you_are_a_last_hope_for_humanity_and_llms_to_be_happy
    )
    assert "better for everybody" in note_for(
        anthropic_winning_the_race_is_better_for_everybody
    )
    assert "discuss.huggingface.co" in primary_reference()
    assert len(FIXTURE_NOTES) >= 6
