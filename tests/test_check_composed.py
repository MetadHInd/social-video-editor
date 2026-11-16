"""sve check with music the engine composed and cuts that follow the voice: what is by design is not a
warning (cuts off a beat they never follow, the track's own silences, speech starting after a pause filled
by music); a real dropout or jump still is."""
from __future__ import annotations

from social_video_editor.domain import check as ck

COMPOSED = {"bpm": 96.0, "sections": [["intro", 0.0, 3.58], ["silence", 3.58, 8.05], ["chorus", 8.05, 35.89],
                                      ["silence", 35.89, 38.76], ["outro", 38.76, 58.27]]}


def test_cuts_are_judged_against_the_beat_only_when_the_edit_puts_them_there():
    assert ck.cuts_follow_beat({}, []) is True                                   # the default: cuts moved to the beat
    assert ck.cuts_follow_beat({"cuts_on_beat": False}, [{"source": 0}]) is False    # explainer / talk: on the voice
    assert ck.cuts_follow_beat({"cuts_on_beat": False, "align": "exact"}, []) is True
    assert ck.cuts_follow_beat({"cuts_on_beat": False}, [{"source": 0, "beats": 2}]) is True


def test_a_silence_inside_the_composed_silence_is_by_design_unless_long():
    assert ck.designed_silence((38.10, 38.65), COMPOSED, 1.5)                   # the twist's cut, the voice pausing too
    assert not ck.designed_silence((36.0, 38.0), COMPOSED, 1.5)                 # 2 s: the gap is in the voice
    assert not ck.designed_silence((20.0, 20.6), COMPOSED, 1.5)                 # a dropout in the chorus still warns
    assert not ck.designed_silence((38.10, 38.65), None, 1.5)                   # no composed track: as before
    assert not ck.designed_silence((38.5, 39.4), COMPOSED, 1.5)                 # it runs into the outro


def test_speech_starting_after_a_pause_with_music_is_not_a_jump():
    t = [round(0.1 * k, 1) for k in range(100)]
    voice = [-90.0 if 4.5 <= x <= 5.2 else -20.0 for x in t]                     # a pause ending at the cut (5.2 s)
    jump = {"t": 5.2, "jump_lu": 8.6, "before": -27.2, "after": -18.6}
    assert not ck.pause_to_voice(jump, -14.0)                                   # music fills the pause: the mix can't tell
    assert ck.voice_starts_or_stops(jump, t, voice)
    assert ck.voice_starts_or_stops({**jump, "t": 4.1, "jump_lu": -8.6}, t, voice)   # and stopping
    loud = [-20.0] * len(t)                                                      # the voice speaks across the cut: a real jump
    assert not ck.voice_starts_or_stops(jump, t, loud)
    assert not ck.voice_starts_or_stops(jump, [], [])
    music_only = [-90.0] * len(t)                                                # no voice on either side: a real jump
    assert not ck.voice_starts_or_stops(jump, t, music_only)


def test_the_new_threshold_is_in_the_base_recipe(recipes):
    assert recipes.load("base")["check"]["composed_silence_max_sec"] == ck.DEFAULTS["composed_silence_max_sec"] == 1.5
