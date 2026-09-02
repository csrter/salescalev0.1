# Instagram story generator — setter hiring

`setter-hiring-stories.jsx` builds a four-slide Instagram story set (1080×1920)
in Photoshop, in the same visual language as the reference: heavy bold white
headlines, red highlight bars behind key phrases, short red underline rules, and
a dark blurred photo background.

Slides produced:

| File | Slide | Content |
|---|---|---|
| `01-hook.png` | Hook | "We're hiring setters." + red `OTE $5K–$7K/month.` pill |
| `02-offer.png` | The offer | What the agency does, what the setter does, "Leads, script, and CRM are all provided" |
| `03-requirements.png` | Requirements | US-based / previous sales experience required / hours / pay |
| `04-cta.png` | CTA | "Spots are limited." + red DM panel with the voice-message ask |

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
- `color.red` — the accent red (`F0242C` by default).
- `background.blur` / `background.darken` — how far the photo recedes.
- `font.heavy` / `font.body` — lists of PostScript font names; the first one
  actually installed wins. Add `Inter-Bold`, `Poppins-Bold`, whatever you use.
- `safe` — margins that keep text clear of Instagram's own UI.
- `saveAsPsd` — also write a layered `.psd` per slide.

**`SLIDES`**

Copy is one entry per slide. Line breaks are deliberate: each line is its own
text layer, which is what allows a red bar behind exactly the lines you pick.

- `{ t: "some text", hl: true }` — draws the red highlight bar behind that line.
- `\r` inside a headline is a hard line break.
- On the CTA slide each line is an array of parts, and `chip: true` on a part
  puts a dark box behind just those words (that's the `voice message` treatment).
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
