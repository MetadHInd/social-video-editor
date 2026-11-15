"""Illustrations and illustrated scenes: every one is reached from the words of a natural sentence (Spanish
and English), near misses are not, negations and participles do not trigger an action, and every drawing
renders at both sizes, the same way each time."""
from __future__ import annotations

import pytest

from social_video_editor.domain.graphics import RecordingCanvas, Stage, build_theme, make_scene
from social_video_editor.domain.graphics.icons import DESCRIPTIONS, ICONS, describe_icon, draw_icon, find_icon, match_icon
from social_video_editor.domain.graphics.scenes import ACTION_TEXTS, ACTIONS, TARGETED, action_of, actions_in, negated

VERTICAL, LANDSCAPE = (1080, 1920), (1920, 1080)
PRINTED = {"stroke": {"outline": 6, "halo": 3, "thread": 6, "thread_halo": 3.5}, "halftone": {"shadow_offset": 12},
           "motion": {"on_twos": True, "boil": True, "print_in": True}}


def first_icon(sentence: str) -> str | None:
    """What the engine picks for a sentence: the first word that names an illustration (as the director and
    the notes read them, word by word)."""
    return next((i for w in sentence.split() if (i := find_icon(w))), None)


# ── every new illustration, reached from a sentence ─────────────────────────
SAID_ICONS = {          # icon → (Spanish sentence, English sentence, a near miss that must not give it)
    "signal": ("Tengo dos rayitas de señal", "The signal drops at night", "Señalar con el dedo"),
    "building": ("Vivo en un edificio grande", "I live in a big building", "Una charla edificante"),
    "shower": ("Me doy una ducha", "I take a shower", "Duchamp pintó eso"),
    "tap": ("Abres el grifo", "Turn on the tap", "Aguerrido como siempre"),
    "clock": ("A esa hora todos se conectan", "At that hour everyone connects", "Ahora mismo"),
    "moon": ("De noche va mejor", "At night it is faster", "Nochebuena en familia"),
    "download": ("La descarga tarda", "The download is slow", "Un descargo de responsabilidad"),
    "gauge": ("La velocidad baja", "The speed drops", "Un auto veloz"),
    "crowd": ("Mucha gente usa la red", "Many people use the network", "Un gesto gentil"),
    "video": ("El video se traba", "The video freezes", "Un videojuego nuevo"),
    "cable": ("Un cable de red", "A cable is faster", "Cablevisión llegó"),
    "satellite": ("Un satélite en órbita", "A satellite in orbit", "Una ciudad satelital"),
    "keyboard": ("El teclado del celular", "The keyboard of the phone", "Teclear rápido"),
    "shield": ("Un escudo contra virus", "A shield against viruses", "El escudero llegó"),
    "key": ("La llave de tu casa", "Your house key", "Un llavero bonito"),
    "bug": ("Un bug en la app", "A bug in the app", "Un Bugatti rojo"),
    "folder": ("Esa carpeta pesa", "That folder is heavy", "Un carpetazo"),
    "file": ("Ese archivo pesa", "That file is big", "Archivar todo"),
    "photo": ("Tus fotos ocupan espacio", "Your photos take space", "Una fotocopia"),
    "camera": ("La cámara del celular", "The camera of the phone", "Mi camarada"),
    "mic": ("El micrófono capta todo", "The microphone hears everything", "Un micro"),
    "speaker": ("El parlante suena fuerte", "The speaker is loud", "El parlamento votó"),
    "message": ("Un mensaje de WhatsApp", "A message from a friend", "El mensajero llegó"),
    "bell": ("Una notificación nueva", "A new notification", "Notificar al jefe"),
    "charger": ("El cargador original", "The original charger", "Un cargamento grande"),
    "trash": ("A la basura", "Into the trash", "Un basurero lleno"),
    "search": ("La búsqueda en Google", "A Google search", "Un buscapersonas"),
    "gear": ("Entra en ajustes", "Open the settings", "Un traje ajustado"),
    "money": ("Cuesta dinero", "It costs money", "Un dinerario"),
    "calendar": ("Cada semana pasa", "Every week it happens", "Un semanario"),
    "pin": ("En el mapa se ve", "On the map you see it", "Un mapache"),
    "rocket": ("Sale como cohete", "Like a rocket", "La cohetería"),
    "idea": ("Una buena idea", "A good idea", "Es ideal"),
    "warning": ("Una alerta roja", "A warning for you", "Alertar a todos"),
}


@pytest.mark.parametrize("name", sorted(SAID_ICONS))
def test_each_new_illustration_is_reached_from_a_sentence_in_both_languages(name):
    es, en, miss = SAID_ICONS[name]
    assert first_icon(es) == name and find_icon(es) == name
    assert first_icon(en) == name and find_icon(en) == name
    assert first_icon(miss) != name and find_icon(miss) != name


def test_every_illustration_has_words_a_description_and_a_sentence():
    assert len(ICONS) >= 40
    old = {"phone", "router", "globe", "cloud", "server", "laptop", "chip", "ram", "disk", "battery", "tower", "app",
           "user", "lock"}
    assert set(ICONS) == old | set(SAID_ICONS)
    for name in ICONS:
        assert name in DESCRIPTIONS and describe_icon(name, "en") != name


@pytest.mark.parametrize("label, name", [
    ("Celular.", "phone"), ("¿Wi-Fi?", "router"), ("el wi-fi de casa", "router"), ("En hora pico", "clock"),
    ("las torres celulares", "tower"), ("Batteries", "battery"), ("Usuarios", "crowd"), ("disco duro", "disk"),
    ("cargando el video", "video"), ("las luces", None), ("Lo usas, no tienes que limpiarlo", None),
    ("Seguro que te pasó", None), ("la página carga lento", None), ("Ahora", None), ("Workers", None),
])
def test_names_are_whole_words_never_pieces_of_one(label, name):
    assert find_icon(label) == name


def test_among_limits_the_illustrations_and_exact_names_are_told_apart():
    assert find_icon("La señal del celular", among={"phone"}) == "phone"
    assert find_icon("La señal", among={"phone"}) is None
    assert match_icon("señal") == ("signal", True) and match_icon("Ducha fría") == ("shower", False)


@pytest.mark.parametrize("name", sorted(ICONS))
def test_every_illustration_draws_the_same_each_time(name):
    for theme in (PRINTED, {"motion": {"ambient": False}}):
        st = Stage(*VERTICAL, build_theme(theme))
        for size in (120, 240):
            for t, active in ((0.0, 0.0), (0.4, 0.0), (1.1, 1.0)):
                a, b = RecordingCanvas(st.W, st.H), RecordingCanvas(st.W, st.H)
                draw_icon(a, st, name, 400, 600, size, t=t, active=active)
                draw_icon(b, st, name, 400, 600, size, t=t, active=active)
                assert a.calls == b.calls
                assert a.count("fill") + a.count("stroke") >= 5
                assert a.count("save") == a.count("restore")


# ── scenes ───────────────────────────────────────────────────────────────────
SAID_ACTIONS = {        # action → (Spanish sentence, English sentence, a near miss that must not give it)
    "compare": ("Compara tu plan con el de tu vecino", "Compare your plan with your neighbor's", "Lo comparte con todos"),
    "search": ("Buscas un video y no aparece", "You search for a video", "No lo busques"),
    "connect": ("Te conectas al wifi del vecino", "You connect to the wifi", "Sin conectarlo"),
    "delete": ("Borra las apps que no usas", "Delete the apps you never open", "No borres nada"),
    "download": ("Descargas una película entera", "You download a whole movie", "El cargamento llegó"),
    "share": ("Todos comparten la misma antena", "Everyone shares the same tower", "Compáralo bien"),
    "queue": ("Tus datos hacen fila", "Your data sits in a queue", "Un filamento"),
}


@pytest.mark.parametrize("action", sorted(SAID_ACTIONS))
def test_each_new_action_comes_from_a_sentence_in_both_languages(action):
    es, en, miss = SAID_ACTIONS[action]
    assert (action_of(es.split()) or ("",))[0] == action
    assert (action_of(en.split()) or ("",))[0] == action
    assert (action_of(miss.split()) or ("",))[0] != action


@pytest.mark.parametrize("words, action", [
    ("Lo usas, no tienes que limpiarlo", None), ("You don't have to clean it", None),
    ("No están abiertas, están dormidas", "sleep"), ("Si no la cierras, se queda abierta", None),
    ("Gasta batería sin usarla", "drain"), ("Sin cerrarla", None), ("No la cierras, la duermes", "sleep"),
    ("al día siguiente cuesta amasarla", None), ("El sol de abril calienta la arena", "work"),
    ("lo pones a trabajar más", "work"), ("La tarde se hace larga", None), ("It is quite slow", "slower"),
    ("El video se queda cargando", "wait"), ("Cargas el celular y la batería sube", "charge"),
    ("Save the file", "store"), ("Save battery", "charge"), ("Never delete it, but store it", "store"),
    ("Los tenía guardados", "store"), ("El tráfico de datos", "queue"),
])
def test_negations_participles_and_look_alikes(words, action):
    found = action_of(words.split())
    assert (found[0] if found else None) == action


def test_a_negation_reaches_its_clause_only():
    words = "No tienes que limpiarlo, pero hay que cerrarla".split()
    assert negated(words, 3) and not negated(words, 7)
    assert [(a, plain) for a, _, plain in actions_in(words)] == [("swipe", True)]


@pytest.mark.parametrize("action", sorted(ACTIONS))
@pytest.mark.parametrize("size", [VERTICAL, LANDSCAPE])
def test_every_scene_draws_the_same_each_time(action, size):
    assert action in ACTION_TEXTS
    spec = {"template": "scene", "action": action, "text": "Una frase corta"}
    if action in ("share", "queue"):
        spec["target"] = "user"
    for t in (0.0, 0.8, 1.6, 3.2):
        a = RecordingCanvas(*size)
        b = RecordingCanvas(*size)
        make_scene(spec, Stage(*size, build_theme(PRINTED))).paint(a, t)
        make_scene(spec, Stage(*size, build_theme(PRINTED))).paint(b, t)
        assert a.calls == b.calls and a.count("save") == a.count("restore")


def test_new_scenes_read_their_subject_and_target():
    st = Stage(*VERTICAL, build_theme())
    assert {"compare", "connect", "delete", "download", "share", "queue"} <= set(TARGETED)
    s = make_scene({"template": "scene", "action": "share", "subject": "antena", "target": "gente"}, st)
    assert (s.subject, s.target) == ("tower", "crowd")
    s = make_scene({"template": "scene", "action": "delete"}, st)
    assert (s.subject, s.target) == ("file", "trash")
    assert make_scene({"template": "scene", "action": "search"}, st).target is None
