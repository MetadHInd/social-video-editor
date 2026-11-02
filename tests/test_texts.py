"""Text with emoji (units), typewriter timing (Card composite) and the text layer."""
from __future__ import annotations

import pytest

from social_video_editor.adapters.pillow import rich_text as rt
from social_video_editor.adapters.pillow.fonts import load_font
from social_video_editor.adapters.pillow.text_layer import PillowTextRenderer, blocks, build_card
from social_video_editor.domain import text_units as tu
from social_video_editor.domain.text_layout import Caption, Card, Layer, TextElement, resolve_style, srt_text


def test_units_letters_and_emoji():
    assert tu.units("Hola 👀") == ["H", "o", "l", "a", " ", "👀"]
    assert tu.units("") == []
    family = "\U0001F469‍\U0001F469‍\U0001F467"   # 👩‍👩‍👧 (ZWJ)
    heart = "❤️"                                  # ❤️ (variation selector VS16)
    bags = "\U0001F6CD️"                               # 🛍️
    assert tu.units(f"a{family}b") == ["a", family, "b"]
    assert tu.units(f"{heart}{bags}!") == [heart, bags, "!"]
    assert tu.units("ok ✨ ya") == ["o", "k", " ", "✨", " ", "y", "a"]
    assert all(tu.is_emoji(u) for u in (family, heart, bags, "👀"))
    assert not tu.is_emoji("a") and not tu.is_emoji(" ")


def test_units_skin_tones_and_flags_are_single_units():
    thumbs = "\U0001F44D\U0001F3FD"                         # 👍🏽
    mexico = "\U0001F1F2\U0001F1FD"                         # 🇲🇽
    coder = "\U0001F469\U0001F3FD‍\U0001F4BB"          # 👩🏽‍💻
    assert tu.units(f"ok{thumbs}") == ["o", "k", thumbs]
    assert tu.units(f"{mexico}{mexico}!") == [mexico, mexico, "!"]
    assert tu.units(coder) == [coder]
    assert all(tu.is_emoji(u) for u in (thumbs, mexico, coder))


def test_wrap_keeps_units():
    f = load_font("Inter-Black", 40)
    lines = rt.wrap("uno dos tres cuatro 👀", f, 40, 160)
    assert len(lines) > 1
    flat = [u for ln in lines for u in ln]
    assert "".join(flat).replace(" ", "") == "unodostrescuatro👀"
    assert lines[-1][-1] == "👀"


STYLE = {"size": 60, "font": "Inter-Black", "color": "#2B2B2B", "background": "#FFFFFFEB", "y": 0.2,
         "max_width": 0.88, "radius": 0.5, "animation": "typewriter", "speed": 34,
         "desc_font": "Inter-Black", "desc_size": 40, "desc_color": "#D6457F"}


def _state(card, t):
    s = card.state(t)
    return s.title, s.description, s.cursor


@pytest.mark.parametrize("text,desc,start,end", [
    ("Hola mundo", "abc", 1.0, 3.0),
    ("Un título bastante largo que no cabe a la velocidad normal", "y una descripción también larga", 0.0, 1.5),
    ("Solo título", None, 2.0, 4.0),
])
def test_typewriter_letter_by_letter(text, desc, start, end):
    tx = {"text": text, "start": start, "end": end}
    if desc:
        tx["description"] = desc
    card, image = build_card(tx, dict(STYLE), 540, 960, "Inter-Black", 0)
    tit, des = blocks(tx, STYLE, 540, "Inter-Black")
    n_tit, n_des = tit.n, (des.n if des else 0)
    assert card.pop and card.start == start and card.end == end
    assert [c for c in card.children()] == ([card.title, card.description] if desc else [card.title])

    step = 0.001
    ts = [start + k * step for k in range(int((end - start) / step))]
    states = [_state(card, t) for t in ts]
    prev = (0, 0)
    t_done = None
    for t, (n_t, n_d, cur) in zip(ts, states):
        assert n_t >= prev[0] and n_d >= prev[1]                  # letters appear in order
        assert n_t <= n_tit and n_d <= n_des
        if n_d > 0:
            assert n_t == n_tit                                   # the description comes after the title
        done = (n_t, n_d) == (n_tit, n_des)
        if not done:
            assert cur == ("t" if n_t < n_tit or not des else "d")   # cursor while typing
        elif t_done is None:
            t_done = t
        prev = (n_t, n_d)
    assert _state(card, start) == (0, 0, "t")
    # done before the text disappears, with time to read (≤ 75 % of the available time)
    available = max(end - start - 0.35, 0.3)
    assert t_done is not None
    assert t_done <= start + 0.04 + 0.75 * available + (0.14 if des else 0) + step
    # cursor blinks every 0.38 s once done: off, on, off…
    side = "d" if des else "t"
    t0 = t_done
    assert _state(card, t0 + 0.19)[2] is None
    assert _state(card, t0 + 0.57)[2] == side
    assert _state(card, t0 + 0.95)[2] is None
    # one keystroke per visible unit that is not a space
    units = tit.units + (des.units if des else [])
    keys = card.keystrokes()
    assert len(keys) == sum(1 for u in units if u != " ")
    assert keys == sorted(keys) and start < keys[0] and keys[-1] < end
    # every typing / blinking instant is a cut of the card
    assert set(card.title.reveal_times) <= card.cuts() and {start, end} <= card.cuts()
    assert image(card.state(end - 0.01)).size == image(card.state(start)).size


def test_typewriter_emoji_is_one_keystroke():
    tx = {"text": "Hola 👀", "start": 0.0, "end": 2.0}
    card, _ = build_card(tx, dict(STYLE), 540, 960, "Inter-Black", 3)
    tit, _ = blocks(tx, STYLE, 540, "Inter-Black")
    assert tit.n == 6 and len(card.keystrokes()) == 5
    assert card.key == "t3"


def test_pop_animation_is_static():
    tx = {"text": "Hola", "start": 0.0, "end": 1.0, "animation": "pop"}
    card, _ = build_card(tx, dict(STYLE), 540, 960, "Inter-Black", 1)
    assert card.state(0.1) == card.state(0.9)
    assert card.state(0.1).key == "t1" and card.state(0.1).title == 4
    assert card.pop
    assert {0.0, 1.0} <= card.cuts()
    assert card.keystrokes() == []


def test_layer_composite_frames():
    a = Caption("s0", 0.0, 1.0, 10, 10, pop=True)
    b = Card.typewriter("t0", 0.5, 2.0, 20, 20, list("ab"), None, speed=10, pop=False)
    layer = Layer([a, b])
    assert list(layer.walk())[:3] == [layer, a, b] and b.title in list(layer.walk())
    frames = layer.frames(2.0)
    assert frames[0][0] == 0.0 and frames[-1][1] == 2.0
    for (s0, e0, _), (s1, _, _) in zip(frames, frames[1:]):
        assert e0 == s1                                          # contiguous stills
    first = frames[0][2]
    assert [(e.key, sc) for e, _, sc in first] == [("s0", 0.8)]   # pop starts small
    assert layer.keystrokes() == b.keystrokes() == pytest.approx([0.64, 0.74])
    leaf = TextElement(list("abc"))
    assert leaf.state(0) == 3 and leaf.cuts() == set() and leaf.keystrokes() == []


def test_resolve_style_and_srt(cfg):
    st = resolve_style({"style": "card", "y": 0.1, "size": 50}, cfg["texts"]["styles"], 0.5)
    assert st["y"] == 0.1 and st["size"] == 25 and st["desc_size"] == 31 and st["background"] == "#FFFFFFEB"
    assert resolve_style({"style": "nope"}, cfg["texts"]["styles"])["size"] == 72          # → simple
    srt = srt_text([], [{"text": "hi", "start": 0.0, "end": 1.25}])
    assert srt == "1\n00:00:00,000 --> 00:00:01,250\nhi\n"


def test_render_layer_writes_stills(tmp_path, cfg):
    texts = [{"text": "Hola 👀", "description": "prueba ✨", "start": 0.0, "end": 1.0, "style": "card"},
             {"text": "fin", "start": 0.5, "end": 1.5, "style": "label"}]
    groups = [{"words": [{"word": "uno", "start": 0.2, "end": 0.4}, {"word": "dos", "start": 0.4, "end": 0.6}],
               "start": 0.2, "end": 0.75}]
    lst, keys = PillowTextRenderer().render_layer(texts, groups, cfg, 270, 480, 2.0, tmp_path / "layer")
    lines = lst.read_text().splitlines()
    assert lines[0] == "ffconcat version 1.0"
    total = sum(float(ln.split()[1]) for ln in lines if ln.startswith("duration"))
    assert total == pytest.approx(2.0, abs=1e-3)
    assert len(keys) == 12 and keys == sorted(keys)        # "Hola 👀" 5 + "prueba ✨" 7; pop label: none
    assert PillowTextRenderer().render_layer([], [], cfg, 270, 480, 2.0, tmp_path / "empty") == (None, [])
