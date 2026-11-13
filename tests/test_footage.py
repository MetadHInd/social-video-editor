"""Third-party footage (b-roll): the rules (licenses, brands, ranking, the piece used, credits, limits),
the sources read from canned answers (no network is ever used), and `sve broll` → render → check on a
synthetic project with fake sources."""
from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor import cli
from social_video_editor.adapters.files import legacy_es
from social_video_editor.adapters.files.packs import FilePackRepository
from social_video_editor.adapters.footage import http, official, pexels, pixabay, wikimedia
from social_video_editor.adapters.opencv.text import text_boxes
from social_video_editor.adapters.system import doctor
from social_video_editor.adapters.system.tools import FFMPEG
from social_video_editor.application import broll
from social_video_editor.application import report
from social_video_editor.container import build_services
from social_video_editor.domain import footage as fg
from social_video_editor.domain.footage import FootageError
from social_video_editor.domain.models import PlanError, UserError
from social_video_editor.domain.script import parse_script
from test_cutaways_render import FPS, explainer_project, tint

ACME = {"name": "Acme", "channel_id": "UC" + "a" * 22, "aliases": ["Rocket Phone"]}
OTHER = "UC" + "b" * 22


def clip(**over) -> dict:
    return {"source": "pexels", "kind": "stock", "id": "1", "title": "", "url": "https://example.org/a.mp4",
            "page": "https://example.org/a", "author": "Ana", "license": "Pexels License", "license_code": "pexels",
            "width": 1080, "height": 1920, "duration": 12.0, **over}


# ── rules ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, code", [
    ("CC0", "cc0"), ("Public domain", "pd"), ("PD-USGov", "pd"), ("CC BY 4.0", "cc-by"), ("cc-by-3.0-de", "cc-by"),
    ("CC BY-SA 4.0", "cc-by-sa"), ("cc-by-sa-3.0-migrated", "cc-by-sa"), ("CC BY-NC 2.0", None),
    ("CC BY-ND 4.0", None), ("CC BY-NC-SA 4.0", None), ("GFDL", None), ("Fair use", None), ("", None), (None, None)])
def test_only_open_licenses_get_a_code(text, code):
    assert fg.license_code(text) == code


def test_author_given_as_html_becomes_plain_text():
    assert fg.plain('<a href="//commons.example/wiki/User:Ana" title="User:Ana">Ana &amp; Luis</a>') == "Ana & Luis"


def test_settings_merge_limits_key_by_key():
    s = fg.settings({"broll": {"max_seconds": {"official": 3}, "credit": False, "sources": None}})
    assert s["max_seconds"] == {"official": 3, "stock": 8.0} and s["credit"] is False
    assert s["sources"] == ["official", "pexels", "pixabay", "wikimedia"] and fg.limit("official", s) == 3
    assert fg.settings(None)["max_share"] == {"official": 0.15}


def test_brands_need_a_name_and_a_channel_id():
    assert fg.brands([{**ACME, "aliases": "Rocket Phone"}]) == [ACME]
    for bad in ({"name": "Acme"}, {"name": "Acme", "channel_id": "@acme"}, {"channel_id": ACME["channel_id"]}, "Acme"):
        with pytest.raises(PlanError, match="channel_id"):
            fg.brands([bad])


def test_a_query_names_a_brand_by_whole_words_only():
    listed = fg.brands([ACME, {"name": "Pixel", "channel_id": OTHER}])
    assert fg.find_brand("the new ACME phone", listed)["name"] == "Acme"
    assert fg.find_brand("un rocket phone en la mano", listed)["name"] == "Acme"       # an alias
    assert fg.find_brand("pixels on a screen", listed) is None                          # not the word "pixel"
    assert fg.find_brand("a phone on a table", listed) is None


def test_ranking_keeps_what_may_be_used_and_prefers_the_frame_shape():
    s = fg.settings(None)
    portrait, wide = clip(id="p"), clip(id="w", width=1920, height=1080)
    small = clip(id="s", width=640, height=360)
    closed = clip(id="nc", source="wikimedia", license="CC BY-NC 2.0", license_code=None)
    by = clip(id="by", source="wikimedia", license="CC BY 4.0", license_code="cc-by", width=1920, height=1080)
    ranked, rejected = fg.rank([wide, small, closed, portrait, by], 1080, 1920, 4.0, s)
    assert [c["id"] for c in ranked] == ["p", "w", "by"]                 # same shape first; then the source's order
    assert {c["id"]: why.split(" (")[0] for c, why in rejected} == {"s": "too small", "nc": "license not accepted: CC BY-NC 2.0"}
    assert [c["id"] for c in fg.rank([wide, portrait], 1920, 1080, 4.0, s)[0]] == ["w", "p"]
    # a license can be taken off the accepted list
    assert fg.rank([by], 1080, 1920, 4.0, fg.settings({"broll": {"licenses": ["cc0"]}}))[1][0][1].startswith("license CC BY 4.0")
    # a clip long enough beats one that is too short for the time on screen
    assert [c["id"] for c in fg.rank([clip(id="short", duration=1.0), clip(id="long")], 1080, 1920, 4.0, s)[0]] == ["long", "short"]


def test_official_clips_only_from_the_listed_channel():
    s = fg.settings(None)
    mine = clip(id="a", source="official", kind="official", channel_id=ACME["channel_id"], license_code="official",
                width=None, height=None, duration=60)
    stranger = {**mine, "id": "b", "channel_id": OTHER}
    long = {**mine, "id": "c", "duration": 3600}
    ranked, rejected = fg.rank([stranger, mine, long], 1080, 1920, 4.0, s, ACME)
    assert [c["id"] for c in ranked] == ["a"]
    assert [why for _, why in rejected] == ["not from a listed official channel", "longer than 12 min"]
    assert fg.rank([mine], 1080, 1920, 4.0, s, None)[0] == []            # no brand named: nothing official


def test_the_piece_used_is_where_the_query_shows_best():
    times = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5]
    start, score = fg.best_window(times, [0, 0, 0.1, 0.9, 0.9, 0.8, 0.1, 0], 1.5, 4.0)
    assert start == 1.5 and score == pytest.approx(0.8667, abs=0.001)
    assert fg.best_window(times, [0.2] * 8, 6.0, 4.0) == (0.0, pytest.approx(0.2))     # shorter than asked: from its start
    assert fg.best_window([], [], 2.0, 4.0) == (0.0, 0.0)
    assert fg.middle_window(10.0, 4.0) == 3.0 and fg.middle_window(100.0, 5.0, "official") == 19.0
    assert fg.middle_window(2.0, 4.0) == 0.0


def test_record_and_credits():
    stock = fg.record(clip(id="77", author_url="https://example.org/ana"), "footage/pexels_77.mp4", "abc", "a phone",
                      "2030-01-02T03:04:05Z")
    assert stock == {"file": "footage/pexels_77.mp4", "sha256": "abc", "source": "pexels", "kind": "stock",
                     "url": "https://example.org/a", "author": "Ana", "license": "Pexels License",
                     "downloaded_at": "2030-01-02T03:04:05Z", "query": "a phone", "used": True,
                     "id": "77", "author_url": "https://example.org/ana"}
    brand = fg.record(clip(source="official", kind="official", brand="Acme", channel_id=ACME["channel_id"], author="Acme TV",
                           license="© Acme (official channel)", page="https://video.example/watch?v=1"),
                      "footage/official_1.mp4", "def", "acme phone", "2030-01-02T03:04:05Z", (12.0, 18.25))
    assert brand["piece"] == [12.0, 18.25] and brand["brand"] == "Acme"
    assert fg.credit_label(stock) == "Ana · Pexels" and fg.credit_label(brand) == "Acme"
    assert fg.credit_label({"source": "wikimedia", "author": ""}) == "Wikimedia Commons"
    # the caption keeps the name short; the record keeps it whole
    assert fg.short_author("engineerguy (Bill Hammack, Nick Ziech & Patrick Ryan)") == "engineerguy"
    assert fg.short_author("Miguel.Angel.HMA Obra derivada de / Derivative work: 2022 Simon Eberl") == "Miguel.Angel.HMA"
    assert fg.short_author("ZDF/Terra X/ T. Schultes") == "ZDF"
    assert fg.short_author("Philippine Government - Radio Television Malacañang") == "Philippine Government"
    assert fg.short_author("A very long collective name of many people") == "A very long collective…"
    assert fg.credits_text([stock, brand, stock]) == (
        "Video: Ana / Pexels, Pexels License — https://example.org/a\n"
        "Video: Acme (official channel) — https://video.example/watch?v=1\n")
    assert fg.credits_text([]) == ""
    assert fg.file_name({"source": "wikimedia", "id": "9/8", "title": "Cómo viaja un mensaje — parte 2 (final)"}, ".webm") == \
        "wikimedia_98_como_viaja_un_mensaje.webm"


def test_check_findings_for_third_party_footage():
    s = fg.settings(None)
    fine = {"file": "footage/a.mp4", "kind": "stock", "start": 1.0, "end": 8.0, "recorded": True}
    assert fg.issues([fine], 60.0, s) == []
    long = {"file": "footage/b.mp4", "kind": "official", "start": 10.0, "end": 20.0, "recorded": True}
    lost = {"file": "footage/c.mp4", "kind": None, "start": 30.0, "end": 32.0, "recorded": False}
    found = fg.issues([fine, long, lost], 60.0, s)
    assert [code for code, _ in found] == ["third_party_too_long", "footage_without_record", "third_party_share"]
    assert "stays 10.0 s" in found[0][1] and "limit 5 s" in found[0][1]
    assert "footage/c.mp4" in found[1][1] and "17%" in found[2][1] and "limit 15%" in found[2][1]


def test_spanish_names_of_the_settings_and_the_cue():
    assert legacy_es.config({"tomas_externas": {
        "fuentes": ["oficial", "pexels"], "segundos_max": {"oficial": 4, "banco": 6}, "proporcion_max": {"oficial": 0.1},
        "credito": False, "encuadre": "desenfoque", "licencias": ["cc0"],
        "marcas": [{"nombre": "Acme", "canal": ACME["channel_id"], "alias": ["Rocket Phone"]}]}}) == {
        "broll": {"sources": ["official", "pexels"], "max_seconds": {"official": 4, "stock": 6},
                  "max_share": {"official": 0.1}, "credit": False, "framing": "blur", "licenses": ["cc0"],
                  "brands": [ACME]}}
    script = parse_script('Uno.\n[toma consulta="un celular" marca="Acme" origen="oficial" encuadre="recortar" segundos=3]\nDos.',
                          legacy_es.cue)
    (cue,) = script.cues
    assert (cue.kind, cue.seconds) == ("broll", 3.0)
    assert cue.spec == {"query": "un celular", "brand": "Acme", "source": "official", "framing": "crop"}


def test_base_recipe_matches_the_defaults(cfg):
    assert fg.settings(cfg) == {**fg.DEFAULTS, **cfg["broll"]} and set(cfg["broll"]) == set(fg.DEFAULTS)
    assert fg.settings(cfg)["max_seconds"] == {"official": 5, "stock": 8}


# ── sources, from canned answers ─────────────────────────────────────────────
def canned(monkeypatch, answer, seen: list):
    def get_json(url, params=None, headers=None, timeout=30):
        seen.append({"url": url, "params": params, "headers": headers})
        return answer
    monkeypatch.setattr(http, "get_json", get_json)


def test_pexels_needs_its_key_and_reads_the_answer(monkeypatch):
    src = pexels.PexelsSource()
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert "PEXELS_API_KEY is not set" in src.ready()
    with pytest.raises(FootageError, match="PEXELS_API_KEY"):
        src.search("a phone", 5, "portrait")
    monkeypatch.setenv("PEXELS_API_KEY", "secret-key")
    seen: list = []
    canned(monkeypatch, {"videos": [
        {"id": 7, "url": "https://stock.example/video/7", "duration": 14, "user": {"name": "Ana", "url": "https://stock.example/@ana"},
         "video_files": [{"link": "https://cdn.example/7_uhd.mp4", "file_type": "video/mp4", "width": 2160, "height": 3840},
                         {"link": "https://cdn.example/7_hd.mp4", "file_type": "video/mp4", "width": 1080, "height": 1920},
                         {"link": "https://cdn.example/7_sd.mp4", "file_type": "video/mp4", "width": 540, "height": 960}]},
        {"id": 8, "url": "https://stock.example/video/8", "duration": 5, "user": {}, "video_files": []}]}, seen)
    assert src.ready() is None
    (found,) = src.search("un celular", 5, "portrait", "es")
    assert seen[0]["headers"] == {"Authorization": "secret-key"}
    assert seen[0]["params"] == {"query": "un celular", "per_page": 5, "orientation": "portrait", "locale": "es-ES"}
    assert found == {"source": "pexels", "kind": "stock", "id": "7", "title": "", "query": "un celular",
                     "url": "https://cdn.example/7_hd.mp4", "page": "https://stock.example/video/7", "author": "Ana",
                     "author_url": "https://stock.example/@ana", "license": "Pexels License", "license_code": "pexels",
                     "license_url": "https://www.pexels.com/license/", "width": 1080, "height": 1920, "duration": 14.0,
                     "thumb": None}
    assert pexels.pick_file([{"link": "x", "width": 640, "height": 360}, {"link": "y", "width": 960, "height": 540}])["link"] == "y"


def test_pixabay_needs_its_key_and_reads_the_answer(monkeypatch):
    src = pixabay.PixabaySource()
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assert "PIXABAY_API_KEY is not set" in src.ready()
    monkeypatch.setenv("PIXABAY_API_KEY", "secret-key")
    seen: list = []
    canned(monkeypatch, {"hits": [{
        "id": 125, "pageURL": "https://stock.example/videos/id-125/", "tags": "flowers, yellow", "duration": 12, "user": "Luis",
        "videos": {"large": {"url": "", "width": 0, "height": 0},
                   "medium": {"url": "https://cdn.example/125_medium.mp4", "width": 1920, "height": 1080},
                   "small": {"url": "https://cdn.example/125_small.mp4", "width": 1280, "height": 720}}}]}, seen)
    (found,) = src.search("yellow flowers", 2, "landscape", "xx")
    assert seen[0]["params"] == {"key": "secret-key", "q": "yellow flowers", "per_page": 3, "safesearch": "true", "lang": "en"}
    assert (found["url"], found["width"], found["author"], found["license_code"], found["title"]) == (
        "https://cdn.example/125_medium.mp4", 1920, "Luis", "pixabay", "flowers, yellow")


def test_wikimedia_reads_license_author_and_a_rendition_of_reasonable_size(monkeypatch):
    seen: list = []
    page = {"pageid": 5, "title": "File:Parts of a phone.webm", "index": 2, "videoinfo": [{
        "url": "https://upload.example/orig.webm", "size": 900 << 20, "width": 3840, "height": 2160, "duration": 74.0,
        "descriptionurl": "https://commons.example/wiki/File:Parts_of_a_phone.webm",
        "derivatives": [{"src": "https://upload.example/t.360p.mov", "type": 'video/quicktime; codecs="mp4v"', "width": 640, "height": 360},
                        {"src": "https://upload.example/t.480p.webm", "type": 'video/webm; codecs="vp9"', "width": 854, "height": 480},
                        {"src": "https://upload.example/t.1080p.webm", "type": 'video/webm; codecs="vp9"', "width": 1920, "height": 1080},
                        {"src": "https://upload.example/t.2160p.webm", "type": 'video/webm; codecs="vp9"', "width": 3840, "height": 2160}],
        "extmetadata": {"Artist": {"value": '<a href="//commons.example/wiki/User:Ana">Ana</a>'},
                        "LicenseShortName": {"value": "CC BY-SA 4.0"}, "License": {"value": "cc-by-sa-4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]}
    small = {"pageid": 6, "title": "File:Short.webm", "index": 1, "videoinfo": [{
        "url": "https://upload.example/short.webm", "size": 4 << 20, "width": 1280, "height": 720, "duration": 9.0,
        "descriptionurl": "https://commons.example/wiki/File:Short.webm", "derivatives": [],
        "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 2.0"}, "License": {"value": "cc-by-nc-2.0"}}}]}
    canned(monkeypatch, {"query": {"pages": [page, small]}}, seen)
    src = wikimedia.WikimediaSource()
    assert src.ready() is None
    first, second = src.search("phone", 10, "portrait")
    assert seen[0]["params"]["gsrsearch"] == "filetype:video phone" and seen[0]["params"]["gsrnamespace"] == 6
    assert (first["id"], first["url"], first["license_code"]) == ("6", "https://upload.example/short.webm", None)   # search order
    assert second == {"source": "wikimedia", "kind": "stock", "id": "5", "title": "Parts of a phone", "query": "phone",
                      "url": "https://upload.example/t.1080p.webm",
                      "page": "https://commons.example/wiki/File:Parts_of_a_phone.webm", "author": "Ana",
                      "license": "CC BY-SA 4.0", "license_code": "cc-by-sa",
                      "license_url": "https://creativecommons.org/licenses/by-sa/4.0", "width": 1920, "height": 1080,
                      "duration": 74.0, "thumb": None}
    assert fg.rank([first, second], 1080, 1920, 4.0, fg.settings(None))[0] == [second]
    # a transcoded copy too heavy for its length (bit rate × duration) is passed over for a smaller one
    info = {"url": "https://upload.example/o.webm", "size": 900 << 20, "width": 1920, "height": 1080, "duration": 1000.0,
            "derivatives": [{"src": "a", "type": "video/webm", "width": 854, "height": 480, "bandwidth": 800_000},
                            {"src": "b", "type": "video/webm", "width": 1920, "height": 1080, "bandwidth": 5_000_000}]}
    assert wikimedia.pick_file(info, 250 << 20)["url"] == "a" and wikimedia.pick_file(info, 50 << 20) is None
    assert fg.rank([{**second, "duration": 1800.0}], 1080, 1920, 4.0, fg.settings(None))[1][0][1] == "longer than 12 min"


def test_official_source_keeps_only_the_brands_own_videos(monkeypatch):
    listing = {"channel_id": ACME["channel_id"], "entries": [
        {"id": "aaa", "title": "Rocket Phone: the film", "duration": 61, "channel_id": ACME["channel_id"], "channel": "Acme",
         "url": "https://video.example/watch?v=aaa"},
        {"id": "bbb", "title": "Somebody's review", "duration": 600, "channel_id": OTHER, "channel": "Reviews"},
        {"id": "ccc", "title": "No channel given", "duration": 30}]}
    (found,) = official.candidates(listing, ACME, "rocket phone")
    assert (found["id"], found["kind"], found["brand"], found["channel_id"], found["url"]) == (
        "aaa", "official", "Acme", ACME["channel_id"], "https://video.example/watch?v=aaa")
    assert official.search_url(ACME["channel_id"], "rocket phone ñ") == (
        f"https://www.youtube.com/channel/{ACME['channel_id']}/search?query=rocket%20phone%20%C3%B1")
    calls: list = []
    monkeypatch.setattr(official, "_run", lambda args: calls.append(args) or json.dumps(listing))
    src = official.OfficialChannelSource()
    assert src.search("rocket phone", 5, "portrait") == [] and calls == []          # no brand: the network is not touched
    assert [c["id"] for c in src.search("rocket phone", 5, "portrait", "en", ACME)] == ["aaa"]
    assert calls[0][:4] == ["--flat-playlist", "--playlist-end", "5", "-J"]
    with pytest.raises(FootageError, match="channel id"):
        src.download({**found, "channel_id": None}, Path("x.mp4"))


def test_official_download_is_filtered_by_channel_and_takes_only_the_piece(monkeypatch, tmp_path):
    calls: list = []

    def run(args):
        calls.append(args)
        out = Path(args[args.index("-o") + 1].replace(".%(ext)s", ".mp4"))
        out.write_bytes(b"video")
        return f"{out}\n"
    monkeypatch.setattr(official, "_run", run)
    cand = {"id": "aaa", "url": "https://video.example/watch?v=aaa", "channel_id": ACME["channel_id"]}
    src = official.OfficialChannelSource()
    path = src.download(cand, tmp_path / "footage" / "official_aaa.mp4", piece=(12.0, 18.5))
    assert path == tmp_path / "footage" / "official_aaa.mp4" and path.is_file()
    args = calls[0]
    assert args[args.index("--match-filter") + 1] == f"channel_id={ACME['channel_id']}"
    assert args[args.index("--download-sections") + 1] == "*12.00-18.50" and args[args.index("-f") + 1] == official.PIECE_FORMAT
    src.download(cand, tmp_path / "preview" / "p.mp4", piece=(1, 2), preview=True)
    assert "--download-sections" not in calls[1] and calls[1][calls[1].index("-f") + 1] == official.PREVIEW_FORMAT
    monkeypatch.setattr(official, "_run", lambda args: "")                        # the filter let nothing through
    with pytest.raises(FootageError, match="nothing was downloaded"):
        src.download(cand, tmp_path / "x.mp4")


def test_http_errors_never_show_the_request_url(monkeypatch):
    def refuse(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(http.urllib.request, "urlopen", refuse)
    with pytest.raises(FootageError) as e:
        http.get_json("https://api.example/videos/", {"key": "secret-key", "q": "a phone"})
    assert str(e.value) == "the API key was not accepted" and "secret" not in str(e.value)

    def offline(request, timeout=0):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(http.urllib.request, "urlopen", offline)
    with pytest.raises(FootageError, match="no connection"):
        http.get_json("https://api.example/videos/", {"key": "secret-key"})
    assert http.suffix_of("https://cdn.example/a/b.WEBM?x=1") == ".webm" and http.suffix_of("https://cdn.example/a") == ".mp4"


def test_download_is_abandoned_when_too_big(monkeypatch, tmp_path):
    class Body:
        def __init__(self):
            self.left = 5

        def read(self, n):
            self.left -= 1
            return b"x" * 1024 if self.left >= 0 else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda request, timeout=0: Body())
    assert http.fetch("https://cdn.example/a.mp4", tmp_path / "f" / "a.mp4").read_bytes() == b"x" * 5120
    with pytest.raises(FootageError, match="larger than"):
        http.fetch("https://cdn.example/a.mp4", tmp_path / "f" / "b.mp4", max_bytes=2048)
    assert sorted(p.name for p in (tmp_path / "f").iterdir()) == ["a.mp4"]          # nothing half-written is left


def test_pack_brands_and_doctor(workspace, monkeypatch):
    pack = workspace / "packs" / "demo"
    pack.mkdir(parents=True)
    (pack / "pack.yaml").write_text("theme: {}\n", encoding="utf-8")
    repo = FilePackRepository()
    assert repo.brands("demo") == []
    (pack / "brands.yaml").write_text(f"brands:\n  - {{name: Acme, channel_id: {ACME['channel_id']}, aliases: [Rocket Phone]}}\n",
                                      encoding="utf-8")
    assert fg.brands(repo.brands("demo")) == [ACME]
    svc = build_services()
    cfg = {"graphics": {"pack": "demo"}, "broll": {"brands": [{"name": "Other", "channel_id": OTHER}]}}
    assert [b["name"] for b in broll.allowed_brands(svc, cfg)] == ["Other", "Acme"]
    (pack / "brands.yaml").write_text("brands: Acme\n", encoding="utf-8")
    with pytest.raises(UserError, match="list of brands"):
        repo.brands("demo")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    checks = {name: (status, detail) for status, name, detail in doctor.footage_checks(svc.footage)}
    assert checks["b-roll: pexels"][0] == "optional" and "PEXELS_API_KEY" in checks["b-roll: pexels"][1]
    assert checks["b-roll: pixabay"] == ("ok", "stock footage") and checks["b-roll: wikimedia"][0] == "ok"
    assert "b-roll: official" in checks


def test_credit_captions_once_per_author_and_rewritten_each_time():
    records = {"footage/a.mp4": {"source": "pexels", "kind": "stock", "author": "Ana"},
               "footage/b.mp4": {"source": "pexels", "kind": "stock", "author": "Ana"},
               "footage/c.mp4": {"source": "official", "kind": "official", "brand": "Acme"}}
    plan = {"cutaways": [{"type": "broll", "query": "x", "file": "footage/a.mp4", "at_sentence": 1, "until_sentence": 1},
                         {"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 2},
                         {"type": "broll", "query": "y", "file": "footage/b.mp4", "at_sentence": 3},
                         {"type": "broll", "query": "z", "file": "footage/c.mp4", "start": 9.0},
                         {"type": "broll", "file": "mine.mp4", "at_sentence": 5}]}
    s = fg.settings(None)
    assert broll._credit_overlays(plan, records, s) is True
    credits = [c for c in plan["cutaways"] if c.get("_auto") == "credit"]
    assert credits == [
        {"type": "graphic", "template": "lower_third", "kicker": "Video", "text": "Ana · Pexels", "at_sentence": 1,
         "seconds": 3.0, "_auto": "credit"},
        {"type": "graphic", "template": "lower_third", "kicker": "Video", "text": "Acme", "start": 9.0, "seconds": 3.0,
         "_auto": "credit"}]
    assert [c.get("file") or c.get("template") for c in plan["cutaways"]] == [
        "footage/a.mp4", "lower_third", "bars", "footage/b.mp4", "footage/c.mp4", "lower_third", "mine.mp4"]
    assert broll._credit_overlays(plan, records, s) is False                     # nothing new to say
    assert broll._credit_overlays(plan, records, {**s, "credit": False}) is True
    assert all(c.get("_auto") != "credit" for c in plan["cutaways"])


# ── sve broll → render → check, with fake sources ────────────────────────────
class FakeSource:
    """A source that "downloads" a clip of one flat color made on the spot."""

    def __init__(self, name: str, kind: str, clips: list[dict], color: str, why: str | None = None):
        self.name, self.kind, self.clips, self.color, self.why = name, kind, clips, color, why
        self.searches: list = []
        self.downloads: list = []
        self.stills: list = []

    def ready(self):
        return self.why

    def search(self, query, limit, orientation, language="en", brand=None):
        self.searches.append((query, orientation, language, brand and brand["name"]))
        return [dict(c, source=self.name, kind=self.kind, query=query) for c in self.clips]

    def preview(self, candidate, dest):
        if not candidate.get("thumb"):
            return None
        out = Path(dest).with_suffix(".jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), np.full((90, 160, 3), 128, np.uint8))
        self.stills.append(candidate["id"])
        return out

    def download(self, candidate, dest, piece=None, preview=False):
        self.downloads.append((candidate["id"], piece, preview))
        if candidate.get("broken"):
            raise FootageError("the download failed (test)")
        seconds = piece[1] - piece[0] if piece else float(candidate["duration"])
        dest = Path(dest).with_suffix(".mp4")
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = candidate.get("size", "360x640")
        if candidate.get("caption"):                         # a title printed over the whole clip
            w, h = (int(v) for v in size.split("x"))
            still = dest.with_suffix(".png")
            cv2.imwrite(str(still), captioned(w, h, candidate["caption"]))
            source = ["-loop", "1", "-framerate", str(FPS), "-t", str(seconds), "-i", str(still)]
        else:
            source = ["-f", "lavfi", "-i", f"color=c={candidate.get('color', self.color)}:s={size}:d={seconds}:r={FPS}"]
        subprocess.run([FFMPEG, "-v", "error", "-y", *source, "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(dest)], check=True)
        return dest


def captioned(w: int, h: int, text: str, at: float = 0.15):
    """A dark picture with a line of white printed text (a title card)."""
    img = np.full((h, w, 3), (40, 90, 30), np.uint8)
    cv2.putText(img, text, (int(w * 0.08), int(h * at) + 40), cv2.FONT_HERSHEY_DUPLEX, w / 520, (255, 255, 255), 2, cv2.LINE_AA)
    return img


SCRIPT = """How does a message travel?
[broll query="a phone on a table"]
Every time you send one, it splits again.
[broll query="the Rocket Phone launch"]
And they travel apart.
"""


def fake_services(monkeypatch, sources: dict):
    svc = build_services()
    svc.footage = sources
    monkeypatch.setattr(cli, "build_services", lambda: svc)
    return svc


def stock_clip(**over) -> dict:
    return {"id": "11", "title": "Phone on a table", "url": "https://stock.example/11.mp4", "page": "https://stock.example/11",
            "author": "Ana", "license": "Stock License", "license_code": "pexels", "width": 1080, "height": 1920,
            "duration": 6.0, **over}


@pytest.mark.integration
@requires_ffmpeg
def test_broll_from_cue_to_video_with_record_credits_and_checks(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, SCRIPT, {"broll": {
        "sources": ["official", "nokey", "stock"], "credit": False, "max_seconds": {"official": 1.0},
        "brands": [ACME]}})
    stock = FakeSource("stock", "stock", [stock_clip(id="10", broken=True), stock_clip()], "0x1EC81E")
    brand = FakeSource("official", "official", [
        {"id": "zz", "title": "A stranger's video", "url": "https://video.example/zz", "channel_id": OTHER, "duration": 40.0},
        {"id": "aa", "title": "Rocket Phone: the film", "url": "https://video.example/aa", "page": "https://video.example/aa",
         "author": "Acme", "brand": "Acme", "channel_id": ACME["channel_id"], "license": "© Acme (official channel)",
         "license_code": "official", "duration": 40.0}], "0x1E1EC8")
    nokey = FakeSource("nokey", "stock", [], "0x000000", why="NOKEY_API_KEY is not set")
    fake_services(monkeypatch, {"official": brand, "nokey": nokey, "stock": stock})
    main = cli.main

    main(["plan", "demo"])
    plan0 = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert [(c["type"], c["query"]) for c in plan0["cutaways"]] == [("broll", "a phone on a table"), ("broll", "the Rocket Phone launch")]
    capsys.readouterr()
    main(["report", "demo"])
    assert "b-roll: 2 cues · 0 with a clip · ⚠ 2 to fetch (sve broll <project>)" in capsys.readouterr().out

    # a dry run lists the candidates and leaves the project as it was
    main(["broll", "demo", "--dry-run"])
    listed = capsys.readouterr().out
    assert "dry run, nothing downloaded" in listed and "stock 11 “Phone on a table” · Ana · Stock License · 1080×1920 · 6 s" in listed
    assert "official aa “Rocket Phone: the film”" in listed and "zz" not in listed
    assert json.loads((d / "plan.json").read_text(encoding="utf-8")) == plan0 and not (d / "footage").exists()
    assert stock.downloads == [] and brand.downloads == []

    main(["broll", "demo"])
    said = capsys.readouterr().out
    assert "B-roll: 2 cues · 2 fetched · 0 already had a clip · 0 without a clip" in said
    assert "nokey: NOKEY_API_KEY is not set" in said and "stock 10: the download failed (test)" in said
    assert "held to 1 s (broll.max_seconds.official)" in said
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    first, second = plan["cutaways"]
    assert first["file"] == "footage/stock_11_phone_on_a_table.mp4" and "seconds" not in first
    assert second["file"] == "footage/official_aa_rocket_phone_the_film.mp4" and second["seconds"] == 1.0
    assert second["clip_start"] == 0.5                                    # the piece was downloaded with half a second around it
    # the brand's channel is searched only for the cue that names the brand; the stranger's video is never downloaded
    assert brand.searches == [("the Rocket Phone launch", "portrait", "es", "Acme")]     # once: the answer is cached a day
    assert len(stock.searches) == 1
    assert [x[0] for x in brand.downloads] == ["aa", "aa"] and brand.downloads[0][2] is True  # a preview, then the piece
    a, b = brand.downloads[1][1]                                          # about a fifth into the video, ± 0.5 s
    assert 6.8 <= a <= 7.8 and b - a == pytest.approx(2.0, abs=0.01)
    assert not (d / "cache" / "footage_preview").exists()
    records = json.loads((d / "footage" / "credits.json").read_text(encoding="utf-8"))
    assert [r["file"] for r in records] == [second["file"], first["file"]]
    official_rec, stock_rec = records
    assert stock_rec["sha256"] == build_services().projects.file_hash(d / first["file"]) and len(stock_rec["sha256"]) == 64
    assert (stock_rec["source"], stock_rec["kind"], stock_rec["author"], stock_rec["url"], stock_rec["query"], stock_rec["used"]) == (
        "stock", "stock", "Ana", "https://stock.example/11", "a phone on a table", True)
    assert (official_rec["kind"], official_rec["brand"], official_rec["channel_id"], official_rec["piece"]) == (
        "official", "Acme", ACME["channel_id"], [round(a, 3), round(b, 3)])
    assert sorted(p.name for p in (d / "footage").iterdir()) == [
        "credits.json", "official_aa_rocket_phone_the_film.mp4", "stock_11_phone_on_a_table.mp4"]
    # running it again fetches nothing
    downloads = len(stock.downloads) + len(brand.downloads)
    main(["broll", "demo"])
    assert "0 fetched · 2 already had a clip" in capsys.readouterr().out
    assert len(stock.downloads) + len(brand.downloads) == downloads

    main(["render", "demo", "--draft"])
    vdir = next(p for p in d.glob("v1*") if p.is_dir())
    final = next(vdir.glob("*.mp4"))
    summary = json.loads((vdir / "summary.json").read_text(encoding="utf-8"))
    a, b = summary["cutaways"]
    assert (a["type"], a["file"], a["third_party"], a["recorded"], a["source"], a["kind"]) == (
        "broll", first["file"], True, True, "stock", "stock")
    assert b["kind"] == "official" and b["end"] - b["start"] == pytest.approx(1.0, abs=0.05)
    assert summary["credits"] == "credits.txt"
    (short,) = summary["cutaway_warnings"]                    # the ceiling wins over the time a cutaway needs to be read
    assert "1.0 s on screen, it needs 1.2 s" in short
    top = (0.1, 0.9, 0.1, 0.5)
    assert [tint(final, t, top) for t in (0.5, (a["start"] + a["end"]) / 2, (b["start"] + b["end"]) / 2, b["end"] + 0.2)] == [
        "red", "green", "blue", "red"]
    assert (vdir / "credits.txt").read_text(encoding="utf-8") == (
        "Video: Ana / Stock, Stock License — https://stock.example/11\n"
        "Video: Acme (official channel) — https://video.example/aa\n")
    capsys.readouterr()
    main(["check", "demo", "--version", vdir.name])
    checked = json.loads((vdir / "check.json").read_text(encoding="utf-8"))
    assert checked["metrics"]["footage"]["clips"] == 2 and checked["metrics"]["footage"]["issues"] == ["third_party_share"]

    # forcing the official clip to stay longer, and a file nobody recorded: the check says so
    forced = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    del forced["cutaways"][1]["seconds"]
    (d / "footage" / "stock_11_phone_on_a_table.mp4").write_bytes((d / "footage" / "official_aa_rocket_phone_the_film.mp4").read_bytes())
    (d / "plan.json").write_text(json.dumps(forced), encoding="utf-8")
    main(["render", "demo", "--draft"])
    again = d / f"{vdir.name}_2"                              # a new folder: nothing is overwritten
    main(["check", "demo", "--version", again.name])
    out = capsys.readouterr().out
    checked = json.loads((again / "check.json").read_text(encoding="utf-8"))
    assert set(checked["metrics"]["footage"]["issues"]) == {"third_party_too_long", "footage_without_record", "third_party_share"}
    assert "third-party footage too long: footage/official_aa_rocket_phone_the_film.mp4 (official)" in out
    assert "footage without a record: footage/stock_11_phone_on_a_table.mp4" in out and "limit 1 s" in out


@pytest.mark.integration
@requires_ffmpeg
def test_nothing_found_leaves_the_cue_without_a_clip_and_the_render_says_so(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock", "gone"], "credit": False, "fallback_card": False}})
    closed = FakeSource("stock", "stock", [stock_clip(license="CC BY-NC 2.0", license_code=None)], "0x1EC81E")
    fake_services(monkeypatch, {"stock": closed})
    cli.main(["plan", "demo"])
    capsys.readouterr()
    cli.main(["broll", "demo"])
    out = capsys.readouterr().out
    assert "0 fetched" in out and "1 without a clip" in out and "⚠ no clip" in out
    assert "stock: nothing usable among 1 results (license not accepted: CC BY-NC 2.0)" in out and "gone: unknown source" in out
    assert closed.downloads == [] and not (d / "footage" / "credits.json").exists()
    cli.main(["render", "demo", "--draft"])
    summary = json.loads((next(p for p in d.glob("v1*") if p.is_dir()) / "summary.json").read_text(encoding="utf-8"))
    assert summary["cutaways"] == [] and "run `sve broll demo`" in summary["cutaway_warnings"][0]


def test_no_clip_becomes_an_illustrated_card_and_the_director_cuts_in(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\n'
                                     'Every time you send one, it splits again.\n[camera]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock"], "credit": False}})
    fake_services(monkeypatch, {"stock": FakeSource("stock", "stock", [], "0x1EC81E")})
    cli.main(["plan", "demo"])
    capsys.readouterr()
    cli.main(["broll", "demo"])
    out = capsys.readouterr().out
    assert "no clip passed: an illustrated card instead (scene “Every time you send one”)" in out
    (cut,) = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    assert cut == {"type": "graphic", "template": "scene", "action": "send", "text": "Every time you send one",
                   "subject": "phone", "target": "globe", "at_sentence": 1, "until_sentence": 1, "_auto": "card"}


@pytest.mark.integration
@requires_ffmpeg
def test_the_same_clip_is_never_used_twice_and_the_credit_comes_with_its_clip(workspace, monkeypatch):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone"]\nEvery time you send one, it splits again.\n'
                                     '[broll query="a phone"]\nAnd they travel apart.\n', {"broll": {"sources": ["stock"]}})
    stock = FakeSource("stock", "stock", [stock_clip(id="1", title="one"), stock_clip(id="2", title="two")], "0x1EC81E")
    fake_services(monkeypatch, {"stock": stock})
    cli.main(["plan", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    plan["cutaways"][1]["from_word"] = [0, 10.3]                          # the second clip starts at a word
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    cli.main(["broll", "demo"])
    cuts = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    clips = [c["file"] for c in cuts if c["type"] == "broll"]
    assert clips == ["footage/stock_1_one.mp4", "footage/stock_2_two.mp4"]
    (credit,) = [c for c in cuts if c.get("_auto") == "credit"]          # one author: one caption, with the first clip
    assert credit["at_sentence"] == 1 and "from_word" not in credit
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    plan["cutaways"] = [c for c in plan["cutaways"] if c.get("_auto") != "credit"]
    plan["cutaways"][0]["from_word"] = [0, 6.6]
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    cli.main(["broll", "demo"])
    (credit,) = [c for c in json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"] if c.get("_auto") == "credit"]
    assert credit["from_word"] == [0, 6.6]


def test_the_explainer_recipe_lets_the_director_propose_footage(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, "How does a message travel? And they travel apart.\nEvery time you send one, it splits again.\n"
                                     "And they travel apart.\n", {"script": {"support": False, "auto_broll": True, "opening": 1.0,
                                                                           "max_camera": 1.5, "auto_min": 0.6}})
    cli.main(["plan", "demo"])
    cuts = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    assert cuts and all(c["_auto"] == "director" and c["type"] == "broll" and c["from_word"] for c in cuts)
    assert "b-roll proposed" in capsys.readouterr().out


class FakeVision:
    """The vision model stand-in, in three dimensions: (the query, something lively, a dull backdrop).
    A clip is scored by its file name; a still by its candidate id."""

    def __init__(self, stills=None):
        self.stills = stills or {}

    def ready(self, settings, download=False):
        return None

    def model_name(self, settings):
        return "fake"

    def frame_embeddings(self, video, cache_dir, key, settings):
        times = np.arange(0, 6, 0.5)
        emb = np.zeros((len(times), 3), np.float32)
        emb[:] = (0.3, 0.3, 0.1)                        # related to the query, a little alive (over the minimums)
        if "good" in Path(video).name:
            emb[6:10] = (1.0, 0.5, 0.0)                 # seconds 3–5 show the query, alive
        if "dull" in Path(video).name:
            emb[:] = (0.4, 0.1, 0.3)                    # on the subject but a backdrop
        return times, emb

    def text_embeddings(self, texts, settings):
        lively, dull = fg.DEFAULTS["lively"], fg.DEFAULTS["dull"]
        return np.array([[0, 1, 0] if t in lively else [0, 0, 1] if t in dull else [1, 0, 0] for t in texts], np.float32)

    def picture_embeddings(self, files, settings):
        return np.array([self.stills.get(Path(f).stem.split("_")[1], (0.3, 0.3, 0.1)) for f in files], np.float32)

    def image_embeddings(self, images, settings):
        return np.array([(0.3, 0.3, 0.1)] * len(images), np.float32)


@pytest.mark.integration
@requires_ffmpeg
def test_the_vision_model_picks_the_clip_and_the_piece(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock"], "credit": False}, "vision": {"enabled": True}})
    stock = FakeSource("stock", "stock", [stock_clip(id="1", title="plain"), stock_clip(id="2", title="good one"),
                                          stock_clip(id="3", title="never tried")], "0x1EC81E")
    svc = fake_services(monkeypatch, {"stock": stock})
    svc.visual = FakeVision()
    cli.main(["plan", "demo"])
    cli.main(["broll", "demo"])
    (cut,) = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    assert cut["file"] == "footage/stock_2_good_one.mp4" and cut["clip_start"] == 0.5
    assert [x[0] for x in stock.downloads] == ["1", "2"]                  # broll.candidates: two are compared
    # a clip that does not look like the query at all is not used
    assert fg.settings(None)["min_match"] == 0.27
    assert sorted(p.name for p in (d / "footage").iterdir()) == ["credits.json", "stock_2_good_one.mp4"]   # the loser is deleted
    (rec,) = json.loads((d / "footage" / "credits.json").read_text(encoding="utf-8"))
    assert rec["piece"][0] == 2.5                                         # seconds 3–5 show the query (± 0.5 s kept)
    kept = build_services().probe.duration(d / cut["file"])
    assert kept == pytest.approx(rec["piece"][1] - rec["piece"][0], abs=0.1) and kept < 6.0   # only the piece is stored
    assert "match 1.00" in capsys.readouterr().out


@pytest.mark.integration
@requires_ffmpeg
def test_stills_decide_what_is_downloaded_and_dull_clips_become_drawings(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock"], "credit": False, "candidates": 1}, "vision": {"enabled": True}})
    stock = FakeSource("stock", "stock", [stock_clip(id="off", title="off", thumb="x"), stock_clip(id="dull", title="dull one", thumb="x"),
                                          stock_clip(id="good", title="good one", thumb="x")], "0x1EC81E")
    svc = fake_services(monkeypatch, {"stock": stock})
    svc.visual = FakeVision({"off": (0.1, 0.3, 0.0), "dull": (0.4, 0.2, 0.1), "good": (0.5, 0.6, 0.0)})
    cli.main(["plan", "demo"])
    capsys.readouterr()
    cli.main(["broll", "demo"])
    out = capsys.readouterr().out
    assert stock.stills == ["off", "dull", "good"]                       # every still looked at…
    assert "1 of 3 left out by their still" in out                      # …the one off the subject is never downloaded
    assert [x[0] for x in stock.downloads] == ["good"]                   # the best still first: one download is enough
    (cut,) = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    assert cut["file"] == "footage/stock_good_good_one.mp4"
    # only dull clips: none is used, the engine draws the sentence instead
    stock.clips = [stock_clip(id="dull", title="dull one")]
    stock.downloads.clear()
    (d / "cache" / "footage_search.json").unlink()                       # search answers are cached a day
    cli.main(["broll", "demo", "--force"])
    out = capsys.readouterr().out
    assert "looks dull (interest" in out and "an illustrated card instead" in out
    (cut,) = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    assert cut["type"] == "graphic" and cut["_auto"] == "card"


def test_interest_and_motion():
    still = [np.full((9, 16), 100.0)] * 4
    moving = [np.full((9, 16), 100.0 + 20 * k) for k in range(4)]
    assert fg.motion_amount(still) == 0.0 and fg.motion_amount(moving) == pytest.approx(20 / 255)
    assert fg.motion_amount([]) == 0.0
    assert fg.interest(0.31, 0.22) == pytest.approx(0.09)
    assert fg.interest(0.31, 0.22, motion=0.05) == pytest.approx(0.11)       # it moves: a little more
    assert fg.interest(0.25, 0.24, motion=0.001) == pytest.approx(-0.02)     # a still picture: less
    assert fg.settings(None)["min_interest"] == 0.05 and fg.settings(None)["min_match"] == 0.27


@pytest.mark.integration
@requires_ffmpeg
def test_make_does_everything_with_one_command(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock"], "credit": False}})
    fake_services(monkeypatch, {"stock": FakeSource("stock", "stock", [stock_clip()], "0x1EC81E")})
    cli.main(["make", "demo"])
    out = capsys.readouterr().out
    assert (d / "plan.json").exists() and "1 fetched" in out
    assert "Video: " in out and "Review v1Borrador: OK" in out
    vdir = d / "v1Borrador"
    assert (vdir / "check.json").exists() and next(vdir.glob("*.mp4")).exists()
    cli.main(["hacer", "demo"])                                         # again: the plan and the clip are kept
    out = capsys.readouterr().out
    assert "1 already had a clip" in out and "v1Borrador_2" in out


@pytest.mark.integration
@requires_ffmpeg
def test_make_leaves_what_it_cannot_fix_as_decisions(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\nAnd they travel apart.\n'
                                     'Nobody ever said this sentence out loud.\n',
                          {"broll": {"sources": ["stock"], "credit": False}})
    fake_services(monkeypatch, {"stock": FakeSource("stock", "stock", [stock_clip()], "0x1EC81E")})
    cli.main(["make", "demo"])
    out = capsys.readouterr().out
    assert "DECISIONS (1)" in out and "was not found in the recording" in out
    saved = json.loads((d / "review.json").read_text(encoding="utf-8"))
    assert saved["version"] == "v1Borrador" and saved["renders"] == 1
    assert [x["code"] for x in saved["pending"]] == ["sentence_unmatched"]
    cli.main(["report", "demo"])
    out = capsys.readouterr().out
    assert "MAKE v1Borrador: 0 fixed by the engine" in out and "record it again and run sve make" in out


@pytest.mark.integration
@requires_ffmpeg
def test_clips_with_printed_text_are_skipped_and_wide_clips_are_cropped(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone on a table"]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock"], "credit": False}})
    stock = FakeSource("stock", "stock", [stock_clip(id="t", title="title card", caption="FIBER OPTIC CABLES"),
                                          stock_clip(id="w", title="wide", size="1280x720", width=1280, height=720)],
                       "0x1EC81E")
    fake_services(monkeypatch, {"stock": stock})
    cli.main(["plan", "demo"])
    capsys.readouterr()
    cli.main(["broll", "demo"])
    out = capsys.readouterr().out
    assert "stock t: shows printed text (titles, captions) in 100% of its best piece; not used (broll.text)" in out
    (cut,) = json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]
    assert cut["file"] == "footage/stock_w_wide.mp4" and (cut["focus_x"], cut["focus_y"]) == (0.5, 0.5)
    assert sorted(p.name for p in (d / "footage").iterdir()) == ["credits.json", "stock_w_wide.mp4"]


def test_text_is_found_on_title_cards_but_not_on_icons_or_plain_pictures():
    title = captioned(1280, 720, "HOW FIBER OPTIC CABLES WORK")
    (box,) = text_boxes(title)
    assert box[1] < 0.25 and box[2] > 0.3                                 # one line, near the top, wide
    plain = np.full((720, 1280, 3), (40, 90, 30), np.uint8)
    cv2.circle(plain, (640, 360), 200, (240, 240, 240), 6)                # a drawing, not text
    cv2.line(plain, (100, 600), (1100, 120), (30, 30, 220), 5)
    assert text_boxes(plain) == []
    icons = np.full((720, 1280, 3), (235, 235, 235), np.uint8)
    for k, color in enumerate([(60, 200, 60), (230, 120, 40), (40, 40, 230), (200, 60, 200), (40, 200, 230), (120, 120, 120)]):
        x = 300 + k * 110                                                 # a row of colorful app icons with soft shading
        for r in range(40, 0, -4):
            cv2.rectangle(icons, (x + 40 - r, 330 - r), (x + 40 + r, 330 + r), tuple(int(c * (0.7 + r / 130)) for c in color), -1)
    assert text_boxes(icons) == []


def test_crops_for_a_frame_of_another_shape():
    (only,) = fg.crop_spans(1080, 1920, 1080, 1920)                       # same shape: the whole clip
    assert only == (0.5, 0.5, (0.0, 0.0, 1.0, 1.0))
    wide = fg.crop_spans(1920, 1080, 1080, 1920)                          # 16:9 in 9:16 keeps 32 % of the width
    assert [round(f, 2) for f, _, _ in wide] == [0.0, 0.25, 0.5, 0.75, 1.0] and all(fy == 0.5 for _, fy, _ in wide)
    x0, y0, x1, y1 = wide[-1][2]
    assert x1 == pytest.approx(1.0) and x1 - x0 == pytest.approx(1080 / 1920 * 1080 / 1920, abs=0.001) and (y0, y1) == (0.0, 1.0)
    assert len(fg.crop_spans(1080, 1350, 1080, 1920)) == 5                # 4:5 in 9:16 keeps 70 %: placed
    assert len(fg.crop_spans(1300, 1920, 1080, 1920)) == 1                # keeps 83 %: the middle
    tall = fg.crop_spans(1080, 1920, 1920, 1080)
    assert all(fx == 0.5 for fx, _, _ in tall) and [fy for _, fy, _ in tall][-1] == 1.0


def subject_picture(x0: float, x1: float, w: int = 1280, h: int = 720, cloth: bool = False):
    """A light background (or a grainy cloth) with a dark object between x0 and x1 (shares of the width)."""
    rng = np.random.default_rng(1)
    img = np.full((h, w, 3), 235, np.uint8)
    if cloth:
        img = np.clip(np.full((h, w, 3), (60, 110, 90), float) + rng.normal(0, 25, (h, w, 1)), 0, 255).astype(np.uint8)
    img[int(h * 0.15):int(h * 0.85), int(w * x0):int(w * x1)] = (25, 25, 30)
    img[int(h * 0.3):int(h * 0.4), int(w * (x0 + 0.02)):int(w * (x1 - 0.02))] = (200, 60, 60)    # a detail on it
    return img


@pytest.mark.parametrize("x0, x1, cloth, mode", [
    (0.42, 0.58, False, "crop"),      # a narrow phone: it fits in the vertical crop
    (0.42, 0.58, True, "crop"),       # also on a grainy cloth
    (0.2, 0.8, False, "fit"),         # a wide subject: shown whole, large, over a blurred copy
])
def test_the_subject_is_framed_whole(x0, x1, cloth, mode):
    pic = subject_picture(x0, x1, cloth=cloth)
    span = fg.subject_span([fg.detail_profile(pic, 1)])
    assert span[0] == pytest.approx(x0, abs=0.06) and span[1] == pytest.approx(x1, abs=0.06)
    shape = fg.framing(1280, 720, 1080, 1920, span)
    assert shape["mode"] == mode and shape["focus"] == pytest.approx(0.5, abs=0.08)
    if mode == "fit":
        assert x1 - x0 < shape["fit"] <= 1.0                            # the subject and a margin, nothing cut
    assert fg.framing(1080, 1920, 1080, 1920, span)["mode"] == "whole"
    assert fg.framing(1280, 720, 1080, 1920, (0.0, 0.95))["mode"] == "scene"
    assert fg.framing(1280, 720, 1080, 1920, None)["mode"] == "scene"
    assert fg.detail_profile(np.zeros((4, 4)), 1) == [] and fg.subject_span([]) is None


def test_a_fitted_clip_is_drawn_larger_over_its_blurred_copy():
    from social_video_editor.adapters.ffmpeg import filters

    info = {"width": 1920, "height": 1080}
    cfg = {"framing": {"mode": "blur", "zoom_margin": 1.0}}
    whole = filters.prescale(info, 1080, 1920, cfg, {})
    big = filters.prescale(info, 1080, 1920, cfg, {"fit": 0.5, "focus_x": 0.25})
    assert whole.startswith("format=yuv420p,split=2") and "[fg]scale=1080:608" in whole and ",crop=" not in whole.split("[fg]")[2]
    assert "[fg]scale=2160:1216:flags=lanczos,crop=1080:1216:270:0[fg2]" in big


def test_text_inside_a_crop_and_the_piece_without_text():
    boxes = [(0.0, 0.1, 0.3, 0.1)]                                        # a title on the left
    assert fg.text_in(boxes, (0.0, 0.0, 0.32, 1.0)) == pytest.approx(0.03 / 0.32)
    assert fg.text_in(boxes, (0.68, 0.0, 1.0, 1.0)) == 0.0
    times = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    texts = [0.1, 0.1, 0.1, 0.0, 0.0, 0.0, 0.0, 0.1, 0.1, 0.1]           # a title at the start and the end
    start, score, shown = fg.choose_piece(times, None, texts, 3.0, 10.0)
    assert start == 3.0 and score is None and shown == 0.0
    scores = [0.33, 0.33, 0.33, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25]   # the query best seen on the title: still avoided
    assert fg.choose_piece(times, scores, texts, 3.0, 10.0)[0] == 3.0
    assert fg.choose_piece(times, scores, [0.0] * 10, 3.0, 10.0)[:2] == (0.0, pytest.approx(0.33))
    assert fg.choose_piece(times, None, [0.1] * 10, 3.0, 10.0)[2] == 1.0   # text everywhere
    assert fg.choose_piece([], None, [], 3.0, 10.0) == (3.5, None, 0.0)
    assert fg.pick_crop(None, [[0.0], [0.0], [0.0]]) == 1                 # nothing to tell them apart: the middle
    assert fg.pick_crop(None, [[0.0], [0.05], [0.05]]) == 0               # away from the text
    assert fg.pick_crop([[0.1], [0.1], [0.3]], [[0.0], [0.0], [0.0]]) == 2   # where the query shows


def test_broll_needs_a_plan(workspace, monkeypatch):
    monkeypatch.chdir(workspace.parent)
    d = workspace / "projects" / "demo"
    d.mkdir(parents=True)
    (d / "project.yaml").write_text("name: demo\nrecipe: base\nsources: []\n", encoding="utf-8")
    svc = build_services()
    with pytest.raises(UserError, match="sve plan demo"):
        broll.fetch_broll(svc, svc.projects.load(d))
    (d / "plan.json").write_text(json.dumps({"shots": [], "texts": []}), encoding="utf-8")
    assert broll.broll_lines(broll.fetch_broll(svc, svc.projects.load(d))) == [
        'The plan has no broll cutaways (a [broll query="…"] cue in the script adds one).']
    assert report._broll({"cutaways": [{"type": "broll", "query": "x"}, {"type": "broll", "file": "a.mp4"}, {"type": "photo"}]}) == {
        "cues": 2, "without_clip": 1}
