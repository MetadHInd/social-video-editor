# Legacy Spanish keys → English

Earlier versions of this engine used Spanish names everywhere. Files written by them keep working:
`adapters/files/legacy_es.py` translates them **when they are loaded**, so the domain only sees English.
English input passes through unchanged; keys or values that are not in these tables are kept as they are.
New outputs (`plan_used.json`, `config_used.json`, `summary.json`, cache files) are always written in English.
This file is the human-readable copy of the tables in that module (a test checks it lists every mapping).

## Files and folders

### File names

| Spanish | English |
|---|---|
| `proyecto.yaml` | `project.yaml` |
| `plan_usado.json` | `plan_used.json` |
| `config_usada.json` | `config_used.json` |
| `resumen.json` | `summary.json` |

### Workspace folders (the English one is used when it exists)

| Spanish | English |
|---|---|
| `proyectos` | `projects` |
| `recetas` | `recipes` |
| `musica` | `music` |

### Cache files (legacy files are read, never modified)

| Spanish | English |
|---|---|
| `<id>_info.json` | `<id>_media.json` |
| `<id>_analisis.json` | `<id>_analysis.json` |
| `<id>_palabras.json` | `<id>_words.json` |
| `musica_<id>.json` | `music_<id>.json` |

### Environment variables

| Spanish | English |
|---|---|
| `MOTOR_ESPACIO` | `SVE_WORKSPACE` |

### Recipe names (files `recetas/resena.yaml` … are found too)

| Spanish | English |
|---|---|
| `resena` | `review` |
| `antes_despues` | `before_after` |

### Export profiles (`--profile`)

| Spanish | English |
|---|---|
| `cuadrado` | `square` |

### Sound effect files in `sfx/` (a legacy file is used if present)

| Spanish | English |
|---|---|
| `tecla` | `keystroke` |
| `obturador` | `shutter` |

## Command line

| Spanish | English |
|---|---|
| `motor` | `sve / social-video-editor` |
| `nuevo` | `new` |
| `analizar` | `analyze` |
| `vistazo` | `thumbs` |
| `planear` | `plan` |
| `render` | `render` |
| `montar` | `build` |
| `ver` | `view` |
| `recetas` | `recipes` |
| `musica` | `music` |
| `productos` | `products` |
| `davinci` | `(removed)` |
| `--borrador` | `--draft` |
| `--perfil` | `--profile` |
| `--sin-musica` | `--no-music` |
| `--forzar` | `--force` |
| `--receta` | `--recipe` |
| `--fuentes` | `--sources` |
| `--fuente` | `--source` |
| `--desde` | `--start` |
| `--hasta` | `--end` |
| `--cada` | `--every` |

## Recipes and project settings

Recipes (`recipes/*.yaml`, legacy `recetas/*.yaml`) and the `settings` of a project use these keys.

### Keys of `config`

| Spanish | English |
|---|---|
| `idioma` | `language` |
| `salida` | `output` |
| `duracion` | `duration` |
| `ritmo` | `pacing` |
| `gancho` | `hook` |
| `seleccion` | `selection` |
| `analisis` | `analysis` |
| `transcripcion` | `transcription` |
| `encuadre` | `framing` |
| `transiciones` | `transitions` |
| `efectos` | `effects` |
| `musica` | `music` |
| `subtitulos` | `subtitles` |
| `textos` | `texts` |
| `exportar` | `export` |
| `visión` | `vision` |

Removed (dropped on load) in `config`: `cierre`

### Keys of `config.output`

| Spanish | English |
|---|---|
| `ancho` | `width` |
| `alto` | `height` |

Removed (dropped on load) in `config.output`: `perfiles`

### Keys of `config.duration`

| Spanish | English |
|---|---|
| `objetivo` | `target` |
| `maximo` | `max` |

Removed (dropped on load) in `config.duration`: `minimo`

### Keys of `config.pacing`

| Spanish | English |
|---|---|
| `toma_min` | `shot_min` |
| `toma_max` | `shot_max` |
| `frase_max` | `phrase_max` |
| `separacion_min` | `min_gap` |
| `acelerar_sin_voz` | `speed_without_voice` |

### Keys of `config.hook`

| Spanish | English |
|---|---|
| `estrategia` | `strategy` |
| `duracion` | `duration` |
| `texto` | `text` |
| `transicion` | `transition` |

### Values of `config.hook.strategy`

| Spanish | English |
|---|---|
| `inicio` | `start` |

### Values of `config.hook.transition`

| Spanish | English |
|---|---|
| `corte` | `cut` |
| `fundido` | `fade` |
| `negro` | `fade_black` |
| `slide_arriba` | `slide_up` |
| `desliz_izq` | `slide_left` |
| `circulo` | `circle` |
| `difuminar` | `blur` |
| `cortina` | `curtain` |

### Keys of `config.selection`

| Spanish | English |
|---|---|
| `pesos` | `weights` |
| `palabras_clave` | `keywords` |
| `luz_minima` | `min_brightness` |
| `borroso_percentil` | `blur_percentile` |
| `usar_todas_las_fuentes` | `use_all_sources` |
| `cobertura` | `coverage` |

### Keys of `config.selection.weights`

| Spanish | English |
|---|---|
| `movimiento` | `motion` |
| `picos` | `peaks` |
| `voz` | `voice` |
| `palabras_clave` | `keywords` |
| `nitidez` | `sharpness` |

### Keys of `config.analysis`

| Spanish | English |
|---|---|
| `muestras_por_seg` | `samples_per_sec` |
| `umbral_escena` | `scene_threshold` |

### Keys of `config.transcription`

| Spanish | English |
|---|---|
| `modelo` | `model` |
| `idioma` | `language` |
| `contexto` | `context` |
| `prob_min` | `min_probability` |

### Keys of `config.color`

| Spanish | English |
|---|---|
| `hdr_blanco` | `hdr_white` |
| `niveles` | `levels` |
| `balance_blancos` | `white_balance` |
| `luma_objetivo` | `target_luma` |
| `contraste` | `contrast` |
| `saturacion` | `saturation` |
| `temperatura` | `temperature` |
| `nitidez` | `sharpen` |
| `vineta` | `vignette` |

### Keys of `config.framing`

| Spanish | English |
|---|---|
| `modo` | `mode` |
| `enfoque_x` | `focus_x` |
| `enfoque_y` | `focus_y` |
| `margen_zoom` | `zoom_margin` |
| `empuje_lento` | `slow_push` |

### Values of `config.framing.mode`

| Spanish | English |
|---|---|
| `recortar` | `crop` |
| `desenfoque` | `blur` |

### Keys of `config.transitions`

| Spanish | English |
|---|---|
| `rotacion` | `rotation` |
| `duracion` | `duration` |

Values of `config.transitions.rotation`: same as `config.hook.transition`.

Removed (dropped on load) in `config.transitions`: `sonido`

### Keys of `config.effects`

| Spanish | English |
|---|---|
| `zoom_en_picos` | `zoom_on_peaks` |
| `zoom_escala` | `zoom_scale` |
| `pulso_cantidad` | `pulse_amount` |
| `zoom_proporcion` | `zoom_ratio` |
| `flash_en_reveal` | `flash_on_reveal` |
| `barra_progreso` | `progress_bar` |
| `color_barra` | `progress_bar_color` |

### Keys of `config.audio`

| Spanish | English |
|---|---|
| `usar_original` | `use_original` |
| `ruido` | `denoise` |
| `paso_alto` | `highpass` |
| `eq_voz` | `voice_eq` |
| `compresor` | `compressor` |
| `ganancia_sin_voz_db` | `gain_without_voice_db` |
| `pico` | `true_peak` |

### Keys of `config.music`

| Spanish | English |
|---|---|
| `archivo` | `file` |
| `carpeta` | `folder` |
| `volumen_con_voz_db` | `volume_with_voice_db` |
| `volumen_sin_voz_db` | `volume_without_voice_db` |
| `entrada_seg` | `start_at` |
| `fundido_salida` | `fade_out` |
| `fundido_entrada` | `fade_in` |
| `cortes_en_beat` | `cuts_on_beat` |
| `medio_tiempo` | `half_beats` |
| `drop_en_seg` | `drop_at` |
| `alinear` | `align` |
| `_entrada` | `_start` |

### Values of `config.music.file`

| Spanish | English |
|---|---|
| `ninguna` | `none` |
| `no` | `none` |

### Values of `config.music.align`

| Spanish | English |
|---|---|
| `exacto` | `exact` |

### Keys of `config.sfx`

| Spanish | English |
|---|---|
| `activado` | `enabled` |
| `volumen_db` | `volume_db` |
| `pop_en_textos` | `pop_on_texts` |
| `tecleo` | `typing` |
| `tecleo_db` | `typing_db` |
| `whoosh_en_transiciones` | `whoosh_on_transitions` |

### Keys of `config.subtitles`

| Spanish | English |
|---|---|
| `max_palabras` | `max_words` |
| `max_caracteres` | `max_chars` |
| `mayusculas` | `uppercase` |
| `fuente` | `font` |
| `tam` | `size` |
| `resalte` | `highlight` |
| `borde` | `stroke` |
| `borde_color` | `stroke_color` |
| `sombra` | `shadow` |
| `animacion` | `animation` |

### Values of `config.subtitles.animation`

| Spanish | English |
|---|---|
| `escribir` | `typewriter` |
| `ninguna` | `none` |

### Keys of `config.texts`

| Spanish | English |
|---|---|
| `estilos` | `styles` |

### Names of the entries of `config.texts.styles`

| Spanish | English |
|---|---|
| `gancho` | `hook` |
| `etiqueta` | `label` |
| `tarjeta` | `card` |
| `contador` | `counter` |
| `precio` | `price` |

### Keys of `config.texts.styles.<name>`

| Spanish | English |
|---|---|
| `tam` | `size` |
| `fuente` | `font` |
| `fondo` | `background` |
| `mayusculas` | `uppercase` |
| `max_ancho` | `max_width` |
| `radio` | `radius` |
| `contorno` | `outline` |
| `animacion` | `animation` |
| `velocidad` | `speed` |
| `desc_fuente` | `desc_font` |
| `desc_tam` | `desc_size` |

Values of `config.texts.styles.<name>.animation`: same as `config.subtitles.animation`.

### Keys of `config.export`

| Spanish | English |
|---|---|
| `portada` | `cover` |
| `portada_seg` | `cover_at` |

### Keys of `config.polaroid`

| Spanish | English |
|---|---|
| `ancho` | `width` |
| `centro_y` | `center_y` |
| `fuente` | `font` |
| `caida` | `drop_time` |
| `destello` | `flash` |

### Keys of `config.collage`

| Spanish | English |
|---|---|
| `columnas` | `columns` |

### Keys of `config.vision` (new section: local CLIP model)

| Spanish | English |
|---|---|
| `activado` | `enabled` |
| `modelo` | `model` |
| `modelo_texto` | `text_model` |
| `dispositivo` | `device` |
| `positivos` | `positive` |
| `negativos` | `negative` |
| `pesos` | `weights` |
| `puntaje_min` | `min_score` |
| `puntaje_extender` | `extend_score` |
| `continuidad` | `continuity` |
| `duracion_min` | `min_duration` |
| `duracion_max` | `max_duration` |
| `mismo_producto` | `same_product` |
| `ventana` | `window` |
| `max_productos` | `max_products` |
| `estrellas` | `stars` |
| `vocabulario` | `vocabulary` |
| `plan_productos` | `product_plan` |
| `busqueda_producto` | `product_search` |

### Keys of `config.vision.weights`

| Spanish | English |
|---|---|
| `indicaciones` | `prompts` |
| `objeto` | `objectness` |
| `novedad` | `novelty` |
| `nitidez` | `sharpness` |

## Project file (`proyecto.yaml` → `project.yaml`)

### Keys of `project`

| Spanish | English |
|---|---|
| `nombre` | `name` |
| `receta` | `recipe` |
| `fuentes` | `sources` |
| `ajustes` | `settings` |

### Values of `project.recipe`

| Spanish | English |
|---|---|
| `resena` | `review` |
| `antes_despues` | `before_after` |

### Keys of `project.sources`

| Spanish | English |
|---|---|
| `ruta` | `path` |

`project.settings` uses the same table as `config`.

## Plan (`plan.json`)

### Keys of `plan`

| Spanish | English |
|---|---|
| `nota` | `note` |
| `tomas` | `shots` |
| `textos` | `texts` |
| `candidatos_extra` | `extra_candidates` |

### Keys of `plan.shots`

| Spanish | English |
|---|---|
| `fuente` | `source` |
| `ini` | `start` |
| `fin` | `end` |
| `velocidad` | `speed` |
| `rol` | `role` |
| `transicion` | `transition` |
| `efectos` | `effects` |
| `golpes` | `beats` |
| `continua` | `continuous` |
| `buscar` | `search` |
| `_buscado` | `_searched` |
| `tipo` | `type` |
| `de_toma` | `from_shot` |
| `de_tomas` | `from_shots` |
| `fondo_de` | `background_from` |
| `texto` | `text` |
| `giro` | `tilt` |
| `columnas` | `columns` |
| `enfoque_x` | `focus_x` |
| `enfoque_y` | `focus_y` |
| `volumen_db` | `volume_db` |
| `silenciar` | `mute` |
| `congelar` | `freeze` |
| `nota` | `note` |
| `puntaje` | `score` |
| `pico` | `peak` |
| `voz` | `voice` |
| `grupo` | `group` |
| `_golpes` | `_beats` |

### Values of `plan.shots.role`

| Spanish | English |
|---|---|
| `gancho` | `hook` |
| `historia` | `story` |
| `revelacion` | `reveal` |
| `cierre` | `outro` |
| `apertura` | `opening` |
| `producto` | `product` |
| `estrella` | `star` |

Values of `plan.shots.transition`: same as `config.hook.transition`.

Removed (dropped on load) in `plan.shots`: `_apariciones`

### Keys of `plan.shots.effects`

| Spanish | English |
|---|---|
| `tipo` | `type` |
| `escala` | `scale` |
| `cantidad` | `amount` |
| `amplitud` | `amplitude` |
| `seg` | `seconds` |

### Values of `plan.shots.effects.type`

| Spanish | English |
|---|---|
| `zoom_golpe` | `zoom_punch` |
| `empuje` | `push_in` |
| `alejar` | `pull_out` |
| `sacudida` | `shake` |
| `congelar` | `freeze` |
| `pulso_ritmo` | `beat_pulse` |

### Keys of `plan.shots.search`

| Spanish | English |
|---|---|
| `en` | `range` |
| `criterio` | `criterion` |
| `texto` | `text` |

`plan.shots.search.range` may also be written `in` (e.g. `{"text": "phone case", "in": [40, 60]}`);
without a range the whole source is searched. A `text` search needs the vision model (`vision`).

### Values of `plan.shots.search.criterion`

| Spanish | English |
|---|---|
| `producto` | `product` |
| `accion` | `action` |

`plan.shots._searched` uses the same table as `plan.shots.search`.

`plan.shots.color` uses the same table as `config.color`.

### Keys of `plan.texts`

| Spanish | English |
|---|---|
| `tam` | `size` |
| `fuente` | `font` |
| `fondo` | `background` |
| `mayusculas` | `uppercase` |
| `max_ancho` | `max_width` |
| `radio` | `radius` |
| `contorno` | `outline` |
| `animacion` | `animation` |
| `velocidad` | `speed` |
| `desc_fuente` | `desc_font` |
| `desc_tam` | `desc_size` |
| `texto` | `text` |
| `descripcion` | `description` |
| `ini` | `start` |
| `fin` | `end` |
| `toma` | `shot` |
| `hasta_toma` | `until_shot` |
| `estilo` | `style` |
| `sonido` | `sound` |

### Values of `plan.texts.style`

| Spanish | English |
|---|---|
| `gancho` | `hook` |
| `etiqueta` | `label` |
| `tarjeta` | `card` |
| `contador` | `counter` |
| `precio` | `price` |

Values of `plan.texts.animation`: same as `config.subtitles.animation`.

### Values of `plan.texts.sfx`

| Spanish | English |
|---|---|
| `tecla` | `keystroke` |
| `obturador` | `shutter` |

`plan.extra_candidates` uses the same table as `plan.shots`.

## Analysis cache

### Keys of `media`

| Spanish | English |
|---|---|
| `ruta` | `path` |
| `duracion` | `duration` |
| `audio` | `has_audio` |
| `audio_canales` | `audio_channels` |
| `audio_indice` | `audio_index` |
| `ancho` | `width` |
| `alto` | `height` |
| `rotacion` | `rotation` |

### Keys of `analysis`

| Spanish | English |
|---|---|
| `duracion` | `duration` |
| `muestras` | `samples` |
| `escenas` | `scenes` |
| `palabras` | `words` |
| `voz` | `voice` |

### Keys of `analysis.samples`

| Spanish | English |
|---|---|
| `luz` | `brightness` |
| `nitidez` | `sharpness` |
| `movimiento` | `motion` |

### Keys of `analysis.audio`

| Spanish | English |
|---|---|
| `paso` | `step` |
| `picos` | `peaks` |
| `db_medio` | `median_db` |

### Keys of `analysis.words`

| Spanish | English |
|---|---|
| `p` | `word` |
| `ini` | `start` |
| `fin` | `end` |

### Keys of `analysis.voice`

| Spanish | English |
|---|---|
| `tramos` | `segments` |
| `proporcion` | `ratio` |

### Keys of `music_analysis`

| Spanish | English |
|---|---|
| `duracion` | `duration` |
| `intervalo` | `interval` |
| `golpes` | `beats` |

## Rendered summary (`resumen.json` → `summary.json`)

### Keys of `summary`

| Spanish | English |
|---|---|
| `duracion_seg` | `duration_sec` |
| `tomas` | `shots` |
| `resolucion` | `resolution` |
| `musica` | `music` |
| `subtitulos_auto` | `auto_subtitles` |
| `textos` | `texts` |
| `efectos_sonido` | `sound_effects` |
| `tiempo_render_seg` | `render_time_sec` |
| `tomas_detalle` | `shot_details` |

### Keys of `summary.music`

| Spanish | English |
|---|---|
| `archivo` | `file` |
| `desde_seg` | `start_sec` |

### Keys of `summary.audio`

| Spanish | English |
|---|---|
| `antes_lufs` | `before_lufs` |
| `despues_lufs` | `after_lufs` |
| `pico_dbtp` | `true_peak_dbtp` |

### Keys of `summary.shot_details`

| Spanish | English |
|---|---|
| `tipo` | `type` |
| `fuente` | `source` |
| `de` | `from` |
| `a` | `to` |
| `en_video` | `in_video` |
| `corte_seg` | `cut_sec` |
| `rol` | `role` |
| `transicion` | `transition` |

Values of `summary.shot_details.role`: same as `plan.shots.role`.

Values of `summary.shot_details.transition`: same as `config.hook.transition`.

## Removed or changed configuration keys

| Key | What happened |
|---|---|
| `salida.perfiles` | removed: the profile is chosen with `--profile` |
| `duracion.minimo` | removed: nothing read it |
| `cierre.bucle`, `cierre.fundido_audio` | removed: nothing read them |
| `transiciones.sonido` | removed: duplicate of `sfx.whoosh_on_transitions` |
| `exportar.portada` → `export.cover` | now implemented: `false` skips the cover image |
| `transcripcion.idioma` → `transcription.language` | `null` (default) follows the new top-level `language` |
| (new) `language` | `es` (default) or `en`: Whisper language, version folder/file names, default keywords |
| (new) `framing.focus_y` | vertical crop position, inherited by shots like `focus_x` |
| (new) `vision` | local CLIP model: `sve products`, `search.text`, one shot per product in unboxing plans |

## Version folders

| | `language: es` | `language: en` |
|---|---|---|
| main | `v3` | `v3` |
| draft | `v3Borrador` | `v3Draft` |
| no music | `v3SinMusica` | `v3NoMusic` |
| profile | `v3Youtube`, `v3Cuadrado` | `v3Youtube`, `v3Square` |
| files | `name_borrador_sin_musica.mp4`, `name_portada.jpg` | `name_draft_no_music.mp4`, `name_cover.jpg` |
| failed render | `v3_fallida` | `v3_failed` |
