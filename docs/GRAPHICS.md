# Animated graphics

Graphics that explain something — a comparison, a figure, a product, a headline, a list, how a thing
works — are **described as data and drawn locally**. No drawing code per video: a graphic is a few
key/value pairs, and the engine lays it out for the frame (vertical or landscape), animates it and
keeps it alive.

Needs the optional extra: `pip install "social-video-editor[graphics]"` (skia-python).
`sve doctor` says whether it is installed.

```bash
sve graphic --proof [--pack NAME] [--video]      # every template on one sheet, vertical and landscape
sve graphic --cue 'bars data="Before:15, 2026:40" unit="%"'   # one graphic, in seconds
sve graphic <project> --shot 2                   # graphic shot 2 of the plan
```

Each preview is a contact sheet of four instants plus the final frame at full size
(`cache/graphics/`, or `--out DIR`). Look at it before rendering the video.

## Writing a graphic

In `plan.json` a graphic is a shot (see [PLAN_FORMAT.md](PLAN_FORMAT.md)):

```json
{"type": "graphic", "template": "bars", "title": "Battery to open an app",
 "data": "Cold start:100, Asleep:20", "unit": "%", "source": "Source: maker", "dur": 3.5}
```

The same thing as a cue (one line): `bars title="Battery to open an app" data="Cold start:100, Asleep:20" unit="%"`.

A graphic can also be a **cutaway**: shown over the main track while a presenter keeps talking, written
as a cue in a script (`[bars data="…"]`) or as an entry of the plan's `cutaways`
(see [Scripts and cutaways](PLAN_FORMAT.md#scripts-and-cutaways-the-explainer-recipe)).

- **Lists** are text split on `;` (or `|`), else on `,` — or a JSON list.
- **Pairs** are `label:value` items of a list (`"A:15, B:40"`; use `;` between pairs when a value has a
  comma) — or a JSON list of `[label, value]`.
- **Numbers** are written plainly (`14900`, `2.5`) and shown for the language (`14.900` / `14,900`).
- `dur` is the seconds on screen (each template has a default). `color` is an ink of the theme, a theme
  color name or `#RRGGBB`. `caption` draws what is being said in the narration box under the graphic.
- Spanish keys and template names are accepted (`barras`, `datos`, `unidad`…): [LEGACY_KEYS.md](LEGACY_KEYS.md).

## Templates

| Template | Shows | Keys (required in **bold**) | Default `dur` |
|---|---|---|---|
| `title` | A section title or hook as a poster, as large as the frame allows | **`text`** (`\|` forces line breaks), `kicker`, `icon`, `color` | 2.2 |
| `bars` | 1–6 values as gauges that fill; the numbers count up; one is the point | **`data`** (pairs), `unit`, `prefix`, `title`, `source`, `highlight` (label or row number; default the last), `max`, `color` | 3.5 |
| `number` | One big figure, stamped, with rays | **`value`** (a number counts up; other text is shown as is), `label`, `unit`, `prefix`, `sub`, `source`, `icon`, `from`, `count=false`, `color` | 3.0 |
| `product_card` | A product photo as a sticker with up to 4 facts | **`name`**, `image` (file; a PNG cut-out looks best), `specs` (pairs), `price`, `source`, `color` | 4.0 |
| `headline` | A headline or quote as a taped paper clipping with a phrase marked | **`text`**, `outlet`, `date`, `highlight` (words of the text), `color` | 4.0 |
| `list` | 1–6 points, one by one, each on a strip with a round mark | **`items`** (list), `title`, `marks` (`n` numbers, `+` checks, `-` crosses, or one per item: `"+,+,-"`), `color` | 4.5 |
| `diagram` | 2–5 things and the arrows between them, played step by step while a marker travels. A thing with two arrows out to things after it makes it a **flow** (up to 6 things, laid out in steps: a branch opens into two; an arrow back to an earlier thing is a loop drawn around the outside); otherwise a chain (a row, or a zigzag in vertical) | **`nodes`** (list; `Name:ink` colors one), `edges` (`A>B` or `A>B:label`, in the order they happen), `icons` (one per node; `-` = plain card), `title`, `layout` (`chain` / `flow`; default by the edges) | 5.0 |
| `line_chart` | Values over time: the line draws itself, each point pops when the line reaches it, the highlighted value is stamped above its point and a dashed line drops to the axis. Years are spaced by their value | **`data`** (pairs: `"2015:12, 2018:30, 2024:75"`), `data2` (a second line, same labels), `series` (their names), `unit`, `prefix`, `title`, `source`, `highlight` (label or point number; default the last), `min`, `max`, `color` | 4.0 |
| `versus` | Two things face to face (an illustration when the name is known) around a VS stamp; one row at a time, both figures as bars that meet in the middle; the better side gets the color and a check, the side that wins more rows is crowned with the score | **`left`**, **`right`**, **`rows`** (`"Battery:20 h\|9 h; Price:900*\|1200"`: `Label:left\|right`; a `*` marks the better value, else the higher number wins; text values win only with a `*`), `icons` (two; `-` = none), `title`, `source`, `color` | 5.0 |
| `code` | A terminal (a prompt before each command, the output printed under them) or an editor (line numbers, the output on a paper slip below): the lines are typed one by one with a cursor, colored by token; one line can be marked. Long lines wrap in a vertical frame | **`lines`** (`"npm install; npm run build"`; or `text` with line breaks — `\n` in a cue — when a line has `;`), `lang` (empty / `bash` / `zsh`… = terminal; `python`, `js`… = editor), `prompt` (`$` in a terminal), `output` (lines), `highlight` (line number or words of a line), `file` (name on the window bar), `title`, `color` | 5.0 |
| `screen` | A screenshot in a device or browser frame with a ring on the part that matters, then zoomed to it — or, when the frame leaves a band empty (a wide screenshot in a vertical video, a phone screenshot in a landscape one), that part magnified in a loupe joined to the ring | **`image`** (file, relative to the project like a photo), `focus_x`, `focus_y` (0–1; default the middle), `zoom` (1–8; default 2.2), `frame` (`browser`, `phone`, `window`, `none`; default by the picture's shape), `url`, `label` (a note by the ring), `title`, `source`, `color` | 4.5 |
| `lower_third` | A small caption box **over the video** (transparent background), in the bottom-left corner of the safe zone so it covers little of the picture: who, what, or where a clip comes from | **`text`**, `kicker`, `color` | 3.0 |
| `callouts` | **Over the video**: 1–6 notes that pop up around the presenter, each an illustration on a round stamp with its label, a figure on the accent color, or a word on paper underlined by hand | **`items`** (list), `times` (second each one appears; default one every 0.6 s), `icons` (one per item; `-` = label alone; default: by label), `link=true` (up to 4, in a row joined by arrows: a small diagram that builds up), `color` | 4.0 |
| `bullets` | **Over the video**: 1–4 short lines stacked at the presenter's side, one by one | **`items`** (list), `times`, `color` | 4.5 |
| `scene` | An **illustrated scene**: a thing doing what the sentence says, played once and then alive in a loop — an app swiped away (`swipe`), opened (`open`), asleep (`sleep`), a battery that drains or charges (`drain`, `charge`), a packet that travels (`send`), something stored (`store`), frozen (`freeze`), cleaned (`clean`), working hard (`work`), waiting (`wait`), faster or slower (`faster`, `slower`), two things compared on a balance (`compare`), a magnifier that finds (`search`), a cable that plugs in (`connect`), something thrown in the trash (`delete`), pieces downloaded into a device (`download`), one stream shared among many (`share`), a line waiting at a gate (`queue`). The engine's director draws one when no footage is good enough | **`action`**, `subject` (an illustration; default by action), `target` (send / store / connect / delete / download: where it goes; compare: the other thing; share / queue: who receives or waits), `text` (a short poster above), `kicker`, `color` | 3.5 |
| `pip_frame` | **Over the video**: the paper frame of the presenter's window (picture-in-picture). The engine draws it by itself; a pack can replace it | **`rect`** (`"x, y, w, h"` as shares of the frame), `label`, `color` | 3.0 |

### Illustrations

A diagram node whose name is known is drawn as an **illustration** with its name on a tag instead of
a box; `title` and `number` take one with `icon`. Names are matched in Spanish and English, with or
without accents, singular or plural (`Tu celular` → `phone`). `versus` draws one for each side whose name is known.

Words are matched whole (with their plural), never as a piece of a word; a label with no such word gets no illustration.

| Illustration | Also answers to |
|---|---|
| `phone` | celular, telefono, movil, smartphone, iphone, android, cel |
| `router` | wifi, wi-fi, wi fi, modem, enrutador, routers, modems |
| `globe` | internet, web, network, world, online |
| `cloud` | nube, icloud |
| `server` | servidor, servidores, datacenter, backend |
| `laptop` | pc, computador, computadora, portatil, mac, ordenador, computer |
| `chip` | cpu, procesador, processor, gpu, soc |
| `ram` | memoria, memory, ddr |
| `disk` | disco, ssd, almacenamiento, storage, hdd, disco duro, hard drive, hard disk |
| `battery` | bateria, batteries |
| `tower` | antena, antenas, torre, torres, operador, operadora, 4g, 5g, antenna, cell tower, torre celular, torre de celular |
| `app` | aplicacion, apps, aplicaciones, software |
| `user` | tu, usuario, persona, you, cliente, yo, person, customer |
| `lock` | candado, seguridad, contrasena, contrasenas, password, passwords, cifrado, privacidad, security, privacy, encryption, padlock |
| `signal` | senal, senales, cobertura, coverage, rayitas |
| `building` | edificio, edificios, apartamento, apartment, condominio, oficina, bloque de pisos, torre de departamentos |
| `shower` | ducha, duchas, regadera, showers, banarse, duchar |
| `tap` | grifo, grifos, canilla, llave de agua, faucet, agua, water, gota, gotas, tuberia, tuberias, caneria, pipe, pipes |
| `clock` | reloj, relojes, clocks, hora, horas, horario, hour, hours, minuto, minutos, minute, minutes, alarma, alarm, despertador, hora pico, rush hour, cronometro, timer, temporizador |
| `moon` | luna, noche, noches, nocturno, nocturna, madrugada, medianoche, night, nights, midnight, tonight, de noche |
| `download` | descarga, descargas, descargar, downloads, downloading, bajada, descargando |
| `gauge` | velocidad, velocidades, velocimetro, speed, speeds, speedometer, megas, mbps, megabits, ancho de banda, bandwidth, latencia, latency, ping, speed test |
| `crowd` | gente, personas, multitud, muchedumbre, vecinos, vecino, usuarios, crowds, people, users, neighbors, neighbours, todo el mundo |
| `video` | videos, pelicula, peliculas, streaming, reproductor, movie, movies, youtube, netflix, tiktok, cargando, loading, buffering, buffer, reel, reels |
| `cable` | cables, enchufe, enchufes, plug, plugs, conector, connector, usb, fibra, fiber, fibre, ethernet, fibra optica, cableado |
| `satellite` | satelite, satelites, satellites, orbita, orbit |
| `keyboard` | teclado, teclados, keyboards, tecla, teclas, keystroke |
| `shield` | escudo, escudos, proteccion, protection, antivirus, firewall, cortafuegos |
| `key` | llave, llaves, keys |
| `bug` | bugs, virus, malware, glitch, insecto |
| `folder` | carpeta, carpetas, folders, directorio, directory |
| `file` | archivo, archivos, documento, documentos, files, document, documents, pdf, fichero, ficheros |
| `photo` | foto, fotos, fotografia, fotografias, imagen, imagenes, photos, image, images, galeria, gallery, selfie, selfies |
| `camera` | camara, camaras, cameras, webcam, lens, fotografo, photographer |
| `mic` | microfono, microfonos, microphone, microphones, podcast, grabacion, recording |
| `speaker` | altavoz, parlante, parlantes, bocina, bocinas, speakers, sonido, sonidos, audio, musica, music, cancion, song |
| `message` | mensaje, mensajes, messages, chat, chats, whatsapp, sms, mensajeria, messaging, comentario, comentarios, dm |
| `bell` | campana, campanas, campanita, bells, notificacion, notificaciones, notification, notifications, timbre |
| `charger` | cargador, cargadores, chargers, adaptador, adaptadores, adapter, carga rapida, fast charging |
| `trash` | basura, papelera, papeleras, bin, garbage, bote de basura, reciclaje, recycle, desechos, waste bin |
| `search` | lupa, lupas, busqueda, busquedas, buscador, buscadores, searches, magnifier, google |
| `gear` | ajustes, configuracion, settings, engranaje, engranajes, gears |
| `money` | dinero, plata, moneda, monedas, pago, pagos, precio, precios, costo, costos, coin, coins, cash, price, prices, cost, costs, dolar, dolares, dollar, dollars, euro, euros, billete, billetes, factura, facturas, tarifa, ahorros, savings, sueldo, salary |
| `calendar` | calendario, calendarios, fecha, fechas, calendars, mes, meses, month, months, semana, semanas, week, weeks, agenda, schedule, fin de semana, weekend |
| `pin` | mapa, mapas, ubicacion, ubicaciones, gps, map, maps, location, locations, ciudad, city |
| `rocket` | cohete, cohetes, rockets, despegue, despegar, liftoff |
| `idea` | ideas, bombilla, bombillas, bombillo, bulb, lightbulb, truco, trucos, tip, tips, consejo, consejos, hack, hacks, trick, tricks |
| `warning` | alerta, alertas, advertencia, advertencias, peligro, peligros, warnings, alert, alerts, danger |

### Choosing a graphic without a cue

`domain/graphics/suggest.py` turns what a sentence says into a ready spec (or None), so the engine can pick
these graphics by itself: figures tied to three or more years / months / quarters → `line_chart`; two things
set against each other (`vs`, `contra`, `frente a`, `compared to`) with figures on both sides → `versus`; a
command in backticks or a known command with its arguments → `code`; a screenshot file → `screen`; things
that branch (`… al servidor o a la nube`) or loop back (`… vuelve al celular`) → a `diagram` flow.

### The narration box as a subtitle line

`kit.caption(cv, st, text, t, highlight=…, x=…, y=…, width=…, anchor="left" | "center", size=…, max_rows=2,
mark=1.0)` draws the paper narration box anywhere (its top at `y`, its left end or middle at `x`, at most
`width` wide) and marks the word being said with an ink band (`highlight`: the word, or its 0-based
position; `mark`: how far the band has grown). It returns the box `(x, y, w, h)`. Without those
arguments it is the narration box under a graphic, as before.

## What every graphic has

- **A place, not a blank page.** Behind every graphic: a grid, circuit traces with pulses of data,
  small chips with a blinking light and twinkling bits — low contrast and outside the safe zone.
- **Three layers:** the subject; context around it (empty gauges behind bars, tags, tape, a second
  sheet, sparkles, a glow); and ambient life that never stops (the marker bobs, lights blink, dashes
  march along the connection in use).
- **A first frame that is never empty**: titles and labels are there at 0 s; values come in.
- **The largest size that fits** the safe zone of the frame. Vertical frames leave the band below the
  safe zone for the narration box.
- **Sound**: a whoosh when it enters and a pop on each impact, on the effects track.

## Theme

The look is data (`domain/graphics/theme.py:DEFAULT_THEME`). The default is a flat dark stage. A theme
with an ink outline, a halftone shadow, registration offsets and "printed" motion gives a risograph
look: outlined shapes whose color prints slightly off, dotted shadows instead of blur, text that
prints from two color ghosts, objects animated on twos, strokes that wobble.

Override any part from a pack or a project (`graphics.theme` in the settings):

| Key | Meaning |
|---|---|
| `colors` | `bg`, `grid`, `paper` (light text, boxes), `ink` (outlines, text on paper), `muted`, `accent`, `alert`, `shadow`, `ghost_a` / `ghost_b`, `trace`, `device`, `metal` |
| `inks` | Named colors with a fixed meaning (`{green: "#00A95C"}`); `color="green"` picks one. `accent_ink` names the default |
| `icon_inks` | Illustration → ink it is drawn with (`{phone: blue}`) |
| `registration` | Ink → `[dx, dy]` px: how far its fill prints from its outline |
| `fonts` | `display`, `number`, `label`, `mono` (code; default Menlo, else the bold sans): a font file name or path, or `{file: …, wght: 900, wdth: 72}` for an instance of a variable font |
| `stroke` | `outline`, `halo` (light line around ink), `thread`, `thread_halo` (px at 1080) |
| `halftone` | `cell`, `angle`, `radius`, `shadow_offset` (0 = no shadows) |
| `grid`, `grain` | `cell` / `width`; `strength` / `fps` |
| `motion` | `on_twos`, `boil`, `print_in`, `ambient` |
| `decor` | `traces`, `chips`, `bits`, `sparkles` (how many), `glow` |
| `poster`, `box`, `sticker` | `extrude` / `outline` / `tilt`; `tilts` / `radius`; `border` |
| `scale` | `max`, `max_vertical`, `fill`: how large a graphic may grow |
| `safe` | `vertical` / `landscape`: the safe zone as fractions `[x0, y0, x1, y1]` |

## Packs

A pack is the look and the reusable drawings of one channel: `<workspace>/packs/<name>/`.

```
packs/mychannel/pack.yaml      theme: {…}   (overrides of the theme)
packs/mychannel/elements.py    optional Python that registers elements, templates, illustrations
packs/mychannel/fonts/…        font files the theme points to (paths relative to the pack)
```

Use it with `graphics: {pack: mychannel}` in a project's settings, or `--pack mychannel`.

```python
from social_video_editor.domain.graphics import element, template, Scene, kit, paths, motion
from social_video_editor.domain.graphics.icons import icon

@element("marker")                      # the dot that rides bars and travels along arrows
def marker(cv, st, x, y, t=0.0, scale=1.0, **options): ...

@icon("printer", words=("impresora",))  # a new illustration, drawn in a 100 × 100 box
def printer(pen): ...

@template("timeline")                   # a new template
class Timeline(Scene):
    def layout(self): ...               # read self.spec, place things with self.z(), set self.block
    def draw(self, cv, t): ...          # draw the instant t on the canvas
```

Templates draw on a small canvas interface (`domain/graphics/canvas.py`), so they can be tested
without the drawing library.

## Speed

A graphic says which frames look the same: each state is drawn once and reused. Objects move on twos
when the theme says so (half the drawings), and after the entrance a graphic is a loop of 32 states
(≈2.1 s), so a long hold costs nothing more. A 3.5 s graphic at 1080 × 1920 takes about 2 s to draw
and encode; its segment is cached, so only a graphic whose data or theme changed is drawn again.
