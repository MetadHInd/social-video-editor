"""Layered configuration, recipe lookup, language resolution and version folder names."""
from __future__ import annotations

import pytest
import yaml

from social_video_editor.adapters.files.projects import YamlProjectRepository
from social_video_editor.adapters.files.workspace import PACKAGE_RECIPES
from social_video_editor.domain.config import deep_merge, output_name, resolve, variant_suffix


def test_deep_merge_without_mutation():
    base = {"a": {"b": 1, "c": 2}, "list": [1, 2], "x": 1}
    over = {"a": {"b": 3, "d": {"e": 4}}, "list": [9], "y": None}
    out = deep_merge(base, over)
    assert out == {"a": {"b": 3, "c": 2, "d": {"e": 4}}, "list": [9], "x": 1, "y": None}
    assert base == {"a": {"b": 1, "c": 2}, "list": [1, 2], "x": 1}          # base untouched
    out["a"]["d"]["e"] = 99
    assert over["a"]["d"]["e"] == 4                                        # deep copy
    assert deep_merge(base, None) == base


def test_deep_merge_dict_replaces_scalar_and_back():
    assert deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
    assert deep_merge({"a": {"b": 2}}, {"a": 5}) == {"a": 5}


def test_layered_unboxing_recipe(recipes):
    base, unb = recipes.load("base"), recipes.load("unboxing")
    assert unb["pacing"]["shot_max"] == 2.8                              # from unboxing.yaml
    assert unb["pacing"]["shot_min"] == base["pacing"]["shot_min"] == 0.8   # inherited from base.yaml
    assert unb["selection"]["weights"]["peaks"] == 1.1
    assert unb["selection"]["weights"]["motion"] == base["selection"]["weights"]["motion"]
    assert unb["transitions"]["duration"] == 0.22
    assert unb["transitions"]["rotation"][-1] == "slide_up"            # lists are replaced whole


def test_all_packaged_recipes_load(recipes):
    names = set(recipes.available())
    assert {"base", "unboxing", "tutorial", "review", "vlog", "before_after", "storytime"} <= names
    for n in names:
        cfg = recipes.load(n)
        assert cfg["output"]["fps"] == 30
        assert cfg["language"] == "es"


def test_spanish_recipe_names_are_accepted(recipes):
    assert recipes.load("resena") == recipes.load("review")
    assert recipes.load("antes_despues") == recipes.load("before_after")


def test_unknown_recipe(recipes):
    with pytest.raises(SystemExit, match="Recipe 'does_not_exist' not found"):
        recipes.load("does_not_exist")


def test_workspace_recipe_has_priority(workspace, recipes):
    assert recipes.path("unboxing") == PACKAGE_RECIPES / "unboxing.yaml"
    (workspace / "recipes").mkdir()
    (workspace / "recipes" / "unboxing.yaml").write_text("pacing:\n  shot_max: 9.5\n", encoding="utf-8")
    (workspace / "recipes" / "mine.yaml").write_text("duration:\n  target: 7\n", encoding="utf-8")
    assert recipes.path("unboxing") == workspace / "recipes" / "unboxing.yaml"
    cfg = recipes.load("unboxing")
    assert cfg["pacing"]["shot_max"] == 9.5
    assert cfg["selection"]["weights"]["peaks"] == 0.8          # the packaged one is no longer used
    assert recipes.load("mine")["duration"]["target"] == 7
    assert recipes.available()["mine"] == workspace / "recipes" / "mine.yaml"


def test_legacy_workspace_recipes_are_translated(workspace, recipes):
    (workspace / "recetas").mkdir()
    (workspace / "recetas" / "resena.yaml").write_text(
        "ritmo: {toma_max: 4.5}\ntransiciones: {rotacion: [corte, desliz_izq]}\n", encoding="utf-8")
    assert recipes.path("review") == workspace / "recetas" / "resena.yaml"
    cfg = recipes.load("resena")
    assert cfg["pacing"]["shot_max"] == 4.5
    assert cfg["transitions"]["rotation"] == ["cut", "slide_left"]
    assert "review" in recipes.available() and "resena" not in recipes.available()


def test_language_resolution(recipes):
    es, en = recipes.load("unboxing"), recipes.load("unboxing", {"language": "en"})
    assert es["language"] == "es" and es["transcription"]["language"] == "es"
    assert "guau" in es["selection"]["keywords"] and "look" not in es["selection"]["keywords"]
    assert en["language"] == "en" and en["transcription"]["language"] == "en"
    assert "look" in en["selection"]["keywords"] and "guau" not in en["selection"]["keywords"]
    assert en["transcription"]["context"].startswith("Unboxing an online order")
    # an explicit transcription language wins; a plain list is kept as is
    cfg = recipes.load("base", {"language": "en", "transcription": {"language": "pt"},
                                "selection": {"keywords": ["x"]}})
    assert cfg["transcription"]["language"] == "pt" and cfg["selection"]["keywords"] == ["x"]
    assert resolve({"a": {"es": 1, "en": 2}, "language": "en"})["a"] == 2
    with pytest.raises(SystemExit, match="Unknown language"):
        resolve({"language": "fr"})


def _project(workspace, recipes, settings=None, file="project.yaml", data=None):
    d = workspace / "projects" / "demo"
    d.mkdir(parents=True)
    source = workspace / "clip.mov"
    source.write_bytes(b"")
    data = data or {"name": "demo", "recipe": "unboxing", "sources": [str(source)], "settings": settings or {}}
    (d / file).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    repo = YamlProjectRepository(recipes)
    return repo, repo.load(d)


def test_project_settings_over_recipe(workspace, recipes):
    _, p = _project(workspace, recipes, {"pacing": {"shot_min": 0.5}, "music": {"start_at": "auto"}})
    assert p.recipe == "unboxing"
    assert p.cfg["pacing"]["shot_min"] == 0.5
    assert p.cfg["pacing"]["shot_max"] == 2.8
    assert p.cfg["music"]["start_at"] == "auto"
    assert p.cfg["music"]["fade_out"] == 1.0
    assert p.sources[0]["path"] == str(workspace / "clip.mov")
    assert p.cache.is_dir()


def test_legacy_spanish_project_file(workspace, recipes):
    source = workspace / "clip.mov"
    data = {"nombre": "demo", "receta": "resena", "fuentes": [{"ruta": str(source)}],
            "ajustes": {"ritmo": {"toma_min": 0.5}, "musica": {"entrada_seg": "auto"},
                        "textos": {"estilos": {"etiqueta": {"y": 0.155}}}}}
    _, p = _project(workspace, recipes, file="proyecto.yaml", data=data)
    assert (p.name, p.recipe) == ("demo", "review")
    assert p.file.name == "proyecto.yaml"
    assert p.cfg["pacing"]["shot_min"] == 0.5 and p.cfg["music"]["start_at"] == "auto"
    assert p.cfg["texts"]["styles"]["label"]["y"] == 0.155
    assert p.cfg["texts"]["styles"]["label"]["background"] == "#FF3B6B"     # rest of the style inherited
    assert p.sources == [{"path": str(source)}]


def test_project_missing_source(workspace, recipes):
    d = workspace / "p2"
    d.mkdir()
    (d / "project.yaml").write_text("sources: [/does/not/exist.mov]\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Source not found"):
        YamlProjectRepository(recipes).load(d)


def test_project_missing_file(workspace, recipes):
    with pytest.raises(SystemExit, match="Missing .*project.yaml"):
        YamlProjectRepository(recipes).load(workspace)


def test_variant_folders_and_versions(workspace, recipes):
    repo, p = _project(workspace, recipes)
    assert repo.last_version(p) is None
    assert repo.variant_folder(p, "SinMusica").name == "v1SinMusica"      # no versions yet: number 1
    assert repo.new_version(p).name == "v1"
    (p.dir / "v2").mkdir()
    assert repo.new_version(p).name == "v3"
    assert repo.variant_folder(p, "SinMusica").name == "v3SinMusica"
    assert repo.variant_folder(p, "SinMusica").name == "v3SinMusica_2"
    assert repo.variant_folder(p, "SinMusica").name == "v3SinMusica_3"
    assert repo.variant_folder(p, "Borrador").name == "v3Borrador"
    assert repo.last_version(p).name == "v3"                              # variants do not count
    assert repo.new_version(p).name == "v4"


def test_variant_suffixes_follow_language():
    assert variant_suffix("es", no_music=True) == "SinMusica"
    assert variant_suffix("es", draft=True) == "Borrador"
    assert variant_suffix("es", profile="youtube") == "Youtube"
    assert variant_suffix("es", draft=True, profile="youtube", no_music=True) == "BorradorYoutubeSinMusica"
    assert variant_suffix("es", profile="square") == "Cuadrado"
    assert variant_suffix("en", no_music=True) == "NoMusic"
    assert variant_suffix("en", draft=True) == "Draft"
    assert variant_suffix("en", profile="youtube") == "Youtube"
    assert variant_suffix("en", draft=True, profile="square", no_music=True) == "DraftSquareNoMusic"
    assert variant_suffix("es") == variant_suffix("en") == ""             # main versions: vN
    assert output_name("demo", "es", draft=True, no_music=True) == "demo_borrador_sin_musica"
    assert output_name("demo", "en", draft=True, profile="youtube", no_music=True) == "demo_draft_youtube_no_music"


def test_language_drives_variant_folder_names(workspace, recipes):
    repo, p = _project(workspace, recipes, {"language": "en"})
    assert p.language == "en"
    lang = p.cfg["language"]
    assert repo.variant_folder(p, variant_suffix(lang, no_music=True)).name == "v1NoMusic"
    assert repo.variant_folder(p, variant_suffix(lang, draft=True)).name == "v1Draft"
    assert repo.variant_folder(p, variant_suffix(lang, no_music=True)).name == "v1NoMusic_2"
