# Instagram story generator — setter hiring

`setter-hiring-stories.jsx` builds a four-slide Instagram story set (1080×1920)
in Photoshop, in the same visual language as the reference but on Atlas Reach's
palette: heavy bold white headlines, cobalt highlight bars behind key phrases,
short cobalt underline rules, and a navy-tinted blurred photo background.

Colours come straight from the product's brand tokens — cobalt `#2b62e0`
(`--brand-blue`) carries the highlights, navy `#0f2147` / `#0a1022` carries the
chrome and backgrounds. Deep navy is deliberately *not* used for the highlight
bars: against a dark background it has almost no contrast, so cobalt does that
job and navy sits underneath it.

Slides produced:

| File | Slide | Content |
|---|---|---|
| `01-hook.png` | Hook | "We're hiring setters." + cobalt `OTE $5K–$7K/month.` pill |
| `02-offer.png` | The offer | What the agency does, what the setter does, "Leads, script, and CRM are all provided" |
| `03-requirements.png` | Requirements | US-based / previous sales experience required / hours / pay |
| `04-cta.png` | CTA | "Spots are limited." + cobalt DM panel with the voice-message ask |

## Run it

1. Open Photoshop (CS6 or any CC version).
2. **File → Scripts → Browse…** and select `setter-hiring-stories.jsx`.
3. PNGs land in `~/Desktop/setter-stories` and the layered documents stay open
   so you can nudge anything by hand.

To get it into the Scripts menu permanently, copy the file into
`<Photoshop>/Presets/Scripts/` and restart Photoshop.

## Customising

Everything editable lives in the two blocks at the top of the file.

**`CONFIG`**

- `brand` — agency name used in the offer copy.
- `outputFolder` — where PNGs are written.
- `backgroundFolder` — point this at a folder of photos (jpg/png) and one is
  used per slide in alphabetical order, blurred and darkened automatically.
  Leave it `""` for flat near-black backgrounds.
- `color.accent` — the highlight colour (cobalt `2B62E0` by default). A deeper
  navy alternative (`1C3178`) is noted in a comment right below it.
- `color.ink` / `color.chip` / `color.dot` — the navy scale used for flat
  backgrounds, the chip inside the CTA panel, and the numbered circles.
- `background.tint` — the overlay colour laid over photos; navy rather than
  black, so backgrounds pick up the brand cast.
- `background.blur` / `background.darken` — how far the photo recedes.
- `font.heavy` / `font.body` — lists of PostScript font names; the first one
  actually installed wins. Add `Inter-Bold`, `Poppins-Bold`, whatever you use.
- `safe` — margins that keep text clear of Instagram's own UI.
- `saveAsPsd` — also write a layered `.psd` per slide.

**`SLIDES`**

Copy is one entry per slide. Line breaks are deliberate: each line is its own
text layer, which is what allows a red bar behind exactly the lines you pick.

- `{ t: "some text", hl: true }` — draws the cobalt highlight bar behind that line.
- `\r` inside a headline is a hard line break.
- On the CTA slide each line is an array of parts, and `chip: true` on a part
  puts a navy box behind just those words (that's the `voice message` treatment).
- Font sizes auto-shrink to fit the column, so longer copy still lays out
  cleanly — you don't have to hand-tune sizes after an edit.

## Notes

- Curly quotes, em dashes and en dashes are written as `\u` escapes so the file
  stays ASCII-safe across editors and Photoshop's script engine.
- Text layers stay live (not rasterised), so headline tweaks in Photoshop are
  a double-click away.
- The numbered circles on the Requirements slide replace the reference's icon
  glyphs. Swap the `icon` values for any short string, or drop in your own
  icon PNGs on top after running.
