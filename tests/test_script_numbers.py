"""Numbers are matched by value between the script and the transcription (domain/numbers.py,
script_align.py): the script spells them out, the transcription writes digits, or the other way round."""
from __future__ import annotations

from social_video_editor.domain import director
from social_video_editor.domain import script_align as sa
from social_video_editor.domain.numbers import fold, same_number
from test_script import said


def folded(text: str) -> list[str]:
    return [t for t, _, _ in fold(text.split())]


def heard(*words: str, start: float = 0.0) -> list[dict]:
    return [{"word": w, "start": round(start + 0.4 * i, 2), "end": round(start + 0.4 * i + 0.3, 2), "prob": 1}
            for i, w in enumerate(words)]


def test_a_number_however_written_is_one_token_of_its_value():
    assert folded("va a ciento sesenta y un megas.") == ["va", "a", "#161", "megas"]
    assert folded("va a 161 megas.") == ["va", "a", "#161", "megas"]
    assert folded("a 161Mbps") == ["a", "#161", "mbps"]
    assert folded("a cincuenta y cuatro.") == folded("a 54.") == ["a", "#54"]
    assert folded("one hundred and sixty-one dollars") == folded("161 dollars") == ["#161", "dollars"]
    assert folded("diecisiete mil apps") == folded("17 mil apps") == folded("17.000 apps") == folded("17,000 apps")
    assert folded("2,5 GB") == folded("2.5 GB") == ["#2.5", "gb"]
    assert folded("54%") == folded("cincuenta y cuatro %") == ["#54"]
    assert folded("dos mil quince") == folded("2015") == ["#2015"]


def test_ranges_conjunctions_and_articles_stay_apart():
    assert folded("de siete a nueve") == folded("de 7 a 9") == ["de", "#7", "a", "#9"]
    assert folded("dos y tres") == ["#2", "y", "#3"]                     # "y" between two numbers: two numbers
    assert folded("un segundo") == ["un", "segundo"]                       # an article stays a word…
    assert same_number("un", "#1") and same_number("#1", "one") and same_number("cero", "#0")   # …that can stand for 1
    assert not same_number("un", "#2") and not same_number("#54", "#45")


def test_a_spelled_sentence_is_found_in_a_transcription_with_digits():
    rec = heard("De", "7", "a", "9", "de", "la", "noche,", "a", "54.")
    m = sa.align(["De siete a nueve de la noche, a cincuenta y cuatro."], sa.transcript([{"words": rec}]), {})[0]
    assert m is not None and m["score"] == 1.0
    words = m["words"]
    assert [w for w, _, _ in words] == "De siete a nueve de la noche, a cincuenta y cuatro.".split()
    last = rec[-1]                                                        # "54." said once: its three words share it
    assert words[-3][1] == last["start"] and words[-1][2] == last["end"]
    assert words[-3][2] <= words[-2][1] <= words[-1][1]


def test_a_sentence_with_digits_is_found_in_a_transcription_in_words():
    rec = heard("It", "costs", "one", "hundred", "and", "sixty-one", "dollars", "now.")
    m = sa.align(["It costs 161 dollars now."], sa.transcript([{"words": rec}]), {})[0]
    assert m is not None and m["score"] == 1.0
    assert m["words"][2] == ["161", rec[2]["start"], rec[5]["end"]]          # the digit spans the four words heard
    assert m["start"] <= rec[0]["start"] and m["end"] >= rec[-1]["end"]


def test_an_article_matches_the_digit_one_and_long_numbers_both_ways():
    rec = heard("Lo", "bajabas", "en", "1", "segundo.")
    assert sa.align(["Lo bajabas en un segundo."], sa.transcript([{"words": rec}]), {})[0]["score"] == 1.0
    rec = heard("Revisaron", "diecisiete", "mil", "apps", "ayer.")
    assert sa.align(["Revisaron 17.000 apps ayer."], sa.transcript([{"words": rec}]), {})[0]["score"] == 1.0


def test_a_different_number_does_not_match():
    rec = heard("A", "45.")
    assert sa.align(["A cincuenta y cuatro."], sa.transcript([{"words": rec}]), {}) == [None]
    rec = heard("It", "lasts", "17001", "hours", "today.")
    got = sa.align(["It lasts 17000 hours today."], sa.transcript([{"words": rec}]), {})[0]
    assert got["score"] < 1.0                                             # close digits are not the same number
    assert sa.transcript([{"words": said("dos y tres")}])[0].token == "#2"


def test_the_director_reads_figures_as_before():
    assert director.spelled("ciento sesenta y uno".split(), 0) == (161, 4)
    assert director.spelled("treinta y dos sillas".split(), 0) == (32, 3)
    assert director.figure([(w, i * 0.4) for i, w in enumerate("Son diecisiete mil apps".split())])["value"] == "17000"
