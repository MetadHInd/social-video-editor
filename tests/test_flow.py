"""A sentence describing a flow between things → the ordered stops of a diagram (domain/flow.py)."""
from __future__ import annotations

import pytest

from social_video_editor.domain.flow import Flow, flow


@pytest.mark.parametrize("text, nodes, carries", [
    ("El agua pasa de la nube al río y luego al mar.", ("Nube", "Río", "Mar"), "Agua"),
    ("Tu celular manda la foto a la antena.", ("Tu celular", "Antena"), "Foto"),
    ("La señal sale de la torre, llega al satélite y de ahí baja a tu casa.", ("Torre", "Satélite", "Tu casa"), "Señal"),
    ("El paquete viaja desde el almacén hasta tu puerta.", ("Almacén", "Tu puerta"), "Paquete"),
    ("Los datos van del celular al centro de datos.", ("Celular", "Centro de datos"), "Datos"),
    ("El mensaje pasa por el router y llega al servidor.", ("Router", "Servidor"), "Mensaje"),
    ("The router sends the data to the server.", ("Router", "Server"), "Data"),
    ("The letter goes from the office to the post office and then to your house.",
     ("Office", "Post office", "Your house"), "Letter"),
    ("Your phone sends a request through the router to the server.", ("Your phone", "Router", "Server"), "Request"),
])
def test_a_flow_is_read_as_its_stops_in_order(text, nodes, carries):
    got = flow(text.split())
    assert isinstance(got, Flow) and got.nodes == nodes and got.carries == carries


def test_words_with_times_work_too_and_say_where_the_flow_starts():
    words = [[w, float(i), float(i) + 0.3] for i, w in enumerate("Luego el agua pasa de la nube al río.".split())]
    got = flow(words)
    assert got.nodes == ("Nube", "Río") and got.at == 3                  # "pasa"


@pytest.mark.parametrize("text", [
    "De lunes a viernes trabajo mucho.",                 # no movement
    "De 15 % antes a 40 % hoy.",
    "Va a llover.",                                      # "ir a" + a verb
    "Mi hermana va a la tienda.",                        # a single stop
    "Te manda un mensaje.",
    "Le pasa de todo.",
    "Al cerrarla, la caja baja de precio y al abrirla empieza de cero.",     # "al abrirla", "de cero"
    "Si las guardas se van al cajón y traerlas de vuelta cuesta.",           # "de vuelta"
    "She went to sleep early.",
])
def test_not_a_flow(text):
    assert flow(text.split()) is None
