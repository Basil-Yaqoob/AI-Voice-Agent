"""SSML construction.

Rests on one invariant from native review: Urdu script means "say this
Urdu-style", Latin script means "say this in English".
"""

from __future__ import annotations

from xml.etree import ElementTree

import pytest

from agent.language.normalize import normalize_for_tts
from agent.language.ssml import build_ssml, has_latin_run

VOICE = "ur-PK-UzmaNeural"


def _parse(ssml: str) -> ElementTree.Element:
    """Parsing is the assertion: malformed SSML is rejected by Azure mid-call."""
    return ElementTree.fromstring(ssml)


def test_pure_urdu_needs_no_language_switch() -> None:
    ssml = build_ssml("آپ کا آرڈر کل آئے گا", voice=VOICE)
    assert "<lang" not in ssml
    _parse(ssml)


def test_latin_word_gets_english_switch() -> None:
    ssml = build_ssml("آپ کا data ختم ہو گیا", voice=VOICE)
    assert '<lang xml:lang="en-US">data</lang>' in ssml
    _parse(ssml)


def test_latin_run_kept_together() -> None:
    """One switch for 'Premium Plan', not two - separate tags make the voice
    pause between the words."""
    ssml = build_ssml("آپ کا Premium Plan فعال ہے", voice=VOICE)
    assert ssml.count("<lang") == 1
    assert ">Premium Plan<" in ssml


def test_curly_apostrophe_keeps_a_run_together() -> None:
    """Models emit U+2019 in possessives. Splitting there would put a language
    switch mid-word and make the voice stumble."""
    ssml = build_ssml("میں customer’s order دیکھتا ہوں", voice=VOICE)
    assert ssml.count("<lang") == 1
    assert "customer’s order" in ssml


def test_multiple_separate_runs_each_switch() -> None:
    ssml = build_ssml("میں data اور call دونوں", voice=VOICE)
    assert ssml.count("<lang") == 2


def test_voice_and_language_set() -> None:
    ssml = build_ssml("سلام", voice=VOICE)
    root = _parse(ssml)
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "ur-PK"
    assert root[0].get("name") == VOICE


@pytest.mark.parametrize("hostile", ["A & B", "5 < 6", 'say "hi"', "a > b", "<script>x</script>"])
def test_xml_escaping(hostile: str) -> None:
    """The model can emit & or <. An unescaped one makes Azure reject the whole
    request, which drops the turn rather than mispronouncing a word."""
    _parse(build_ssml(hostile, voice=VOICE))


def test_script_tag_is_not_executable_markup() -> None:
    ssml = build_ssml("<script>alert(1)</script>", voice=VOICE)
    assert "<script>" not in ssml
    _parse(ssml)


def test_empty_text_still_valid() -> None:
    _parse(build_ssml("", voice=VOICE))


def test_urdu_survives_intact() -> None:
    ssml = build_ssml("آپ کا آرڈر", voice=VOICE)
    assert "آپ کا آرڈر" in ssml


def test_has_latin_run() -> None:
    assert has_latin_run("آپ کا data")
    assert not has_latin_run("آپ کا آرڈر کل آئے گا")


# ── the end-to-end invariant ───────────────────────────────────────────────


def test_normalised_english_pronunciation_words_reach_ssml_as_latin() -> None:
    """data/track/paid/app/call must survive normalisation in Latin so the SSML
    layer can switch language for them."""
    spoken = normalize_for_tts("آپ کا data ختم ہو گیا ہے")
    assert "data" in spoken
    assert '<lang xml:lang="en-US">data</lang>' in build_ssml(spoken, voice=VOICE)


def test_transliterated_words_never_get_a_switch() -> None:
    """order becomes آرڈر during normalisation, so nothing Latin is left to
    switch on - it is spoken with a Pakistani accent, as the reviewer wanted."""
    spoken = normalize_for_tts("آپ کا order کل آئے گا")
    assert "<lang" not in build_ssml(spoken, voice=VOICE)


def test_unmapped_english_gets_english_pronunciation() -> None:
    """A word missing from the lexicon is left in Latin, so it lands on the
    English switch. Better than guessing a spelling and mispronouncing it."""
    spoken = normalize_for_tts("Wednesday کو")
    assert '<lang xml:lang="en-US">Wednesday</lang>' in build_ssml(spoken, voice=VOICE)


def test_mixed_sentence_switches_only_where_needed() -> None:
    spoken = normalize_for_tts("آپ کا order کل deliver ہوگا اور data ختم ہے")
    ssml = build_ssml(spoken, voice=VOICE)
    assert "آرڈر" in ssml and "ڈیلیور" in ssml  # transliterated, no switch
    assert ssml.count("<lang") == 1  # only data
    _parse(ssml)
