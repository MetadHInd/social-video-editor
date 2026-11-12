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
| `diagram` | 2–5 things and the arrows between them, played step by step while a marker travels | **`nodes`** (list; `Name:ink` colors one), `edges` (`A>B` or `A>B:label`, in the order they happen), `icons` (one per node; `-` = plain card), `title` | 5.0 |
| `lower_third` | A small caption box **over the video** (transparent background), in the bottom-left corner of the safe zone so it covers little of the picture: who, what, or where a clip comes from | **`text`**, `kicker`, `color` | 3.0 |
| `callouts` | **Over the video**: 1–6 notes that pop up around the presenter, each an illustration on a round stamp with its label, a figure on the accent color, or a word on paper underlined by hand | **`items`** (list), `times` (second each one appears; default one every 0.6 s), `icons` (one per item; `-` = label alone; default: by label), `link=true` (up to 4, in a row joined by arrows: a small diagram that builds up), `color` | 4.0 |
| `bullets` | **Over the video**: 1–4 short lines stacked at the presenter's side, one by one | **`items`** (list), `times`, `color` | 4.5 |
| `pip_frame` | **Over the video**: the paper frame of the presenter's window (picture-in-picture). The engine draws it by itself; a pack can replace it | **`rect`** (`"x, y, w, h"` as shares of the frame), `label`, `color` | 3.0 |

### Illustrations

A diagram node whose name is known is drawn as an **illustration** with its name on a tag instead of
a box; `title` and `number` take one with `icon`. Names are matched in Spanish and English, with or
without accents, singular or plural (`Tu celular` → `phone`).

| Illustration | Also answers to |
|---|---|
| `phone` | celular, teléfono, móvil, smartphone, iphone, android |
| `router` | wifi, wi-fi, modem, enrutador |
| `globe` | internet, web, red, network, mundo, world, online |
| `cloud` | nube, icloud, drive |
| `server` | servidor, datacenter, backend |
| `laptop` | pc, computador, computadora, portátil, mac, ordenador, computer |
| `chip` | cpu, procesador, processor, gpu, soc |
| `ram` | memoria, memory, ddr |
| `disk` | disco, ssd, almacenamiento, storage, hdd, archivo |
| `battery` | batería, carga, cargador, energía, pila |
| `tower` | antena, torre, operador, 4g, 5g, señal, cobertura |
| `app` | aplicación, apps, programa, software |
| `user` | tú, usuario, persona, you, cliente, gente |
| `lock` | candado, seguridad, contraseña, password, cifrado, privacidad |

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
| `fonts` | `display`, `number`, `label`: a font file name or path, or `{file: …, wght: 900, wdth: 72}` for an instance of a variable font |
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
