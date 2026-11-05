# acestream-suite — logo assets

The product logo of **acestream-suite**, ready to be copied into the web app: a horizontal lockup for
the header and a square mark for the favicon.

The **product** is called acestream-suite. **AceStream** is the engine the suite plays through, and it
keeps its own name and its own mark — that is why the official AceStream icon sits inside our lockup.

## Files

| File | What it is |
|---|---|
| `ace-stream-mark.svg` | The official AceStream application icon, exactly as it was copied. 192 × 192 Inkscape document. **Provenance copy**: nothing references it, do not edit it. |
| `logo.svg` | The product lockup: the official mark plus the wordmark. Self-contained, `viewBox="0 0 11399.6 2919"` (about 3.9 : 1), no `width`/`height`, so it scales from CSS. |
| `logo-mark.svg` | Square, mark-only variant for the favicon: the official mark on a near-black rounded plate, `viewBox="0 0 256 256"`. Reads at 64 px and at 32 px. |
| `favicon-64.png` | 64 × 64 RGBA raster of `logo-mark.svg`, transparent outside the rounded plate. Generated with headless Chrome; see [Regenerating the PNG](#regenerating-the-png). |

Nothing here is generated from a font. Both SVGs draw the letters as paths, so they render identically
on a machine that has no fonts installed.

## Where the AceStream mark comes from

- **Collection:** `awesome-dashboard-icons`, file `icons/acestream.svg`.
- **Upstream name:** `apps_acestream.svg` — recorded in the copy's own Inkscape metadata
  (`sodipodi:docname="apps_acestream.svg"`, Inkscape 1.1-rc, 2021). This is the name the icon had
  before the collection renamed it.
- **URL:** `https://github.com/benlinton/awesome-dashboard-icons/blob/main/icons/acestream.svg`
  (raw: `https://raw.githubusercontent.com/benlinton/awesome-dashboard-icons/main/icons/acestream.svg`).
  Fetched on 2026-11-05 with `curl -sL` while preparing this rename; the copy kept beside this file as
  `ace-stream-mark.svg` is that download, 4426 bytes.

The mark's own shapes, coordinates and colours are used as they came: `logo.svg` and `logo-mark.svg`
inline the same path data as `ace-stream-mark.svg`. The only thing dropped is the three blurred
copies of the ring (two drop shadows and one glow) and the filter definitions they needed — they are
invisible below 200 px and only add noise. The wordmark is our own, not part of the AceStream brand.

## Palette

Measured in `ace-stream-mark.svg`; the last two rows are ours.

| Colour | Used for |
|---|---|
| `#ff8201` | orange, upper-left wedge of the ring |
| `#ff482e` | red, upper-right wedge |
| `#9e56d6` | purple, right wedge |
| `#3591ac` | teal, lower-right wedge |
| `#2cba66` | green, lower-left wedge |
| `#ffc825` | yellow, left wedge |
| `#ffffff` | inner disc; thin rim highlight at 20 % opacity |
| `#33c0e8` | cyan: the play triangle of the mark, and the word `suite` |
| `#0b1014` | near-black plate behind the mark in `logo-mark.svg` only — it is not part of the official icon |

## How the lockup is built

- **Two lines, not one.** `acestream-suite` is 14 glyphs long. On a single line the lockup is about
  10.6 : 1, so at the 200 px the header gives it it would stand only ~19 px tall, with a 13 px
  x-height and a 16 px mark — favicon sizes on a header. Split in two, the same 200 px width buys an
  18 px x-height and a 38 px mark, and the lockup becomes ~200 × 51 px, which is a header-logo shape.
  The hyphen is not set: the two halves of the name stack, and the accessible name (`<title>`) still
  says the exact string `acestream-suite`.
- **Wordmark.** Lato SemiBold converted to outlines, letter spacing tightened by 0.01 em. Lato is
  under the SIL Open Font License 1.1 (© Łukasz Dziedzic); the licence leaves documents made with the
  font unrestricted, and no font file ships here — only the resulting paths.
- **Colours.** `acestream` is `currentColor` and the `suite` line is the mark's cyan `#33c0e8`, so the
  two halves read as one name in two parts.
- **Light and dark.** The root element of `logo.svg` carries `color="#ffffff"`, which is what makes
  the wordmark white inside an `<img>` on the app's near-black header. Inlined into HTML instead, a
  CSS rule on the element overrides it — presentation attributes lose to every stylesheet rule. On a
  light background set the colour to something dark.
- **The mark** is drawn at 88 % of the wordmark block's height: at full height the dense, colourful
  icon overpowers two lines of type. The lockup's bounds are the ink: no padding is baked in, so CSS
  decides the spacing around it.
- **Accessible markup.** Both SVGs have `role="img"`, `aria-labelledby` pointing at a `<title>` that
  reads `acestream-suite`, and a `viewBox`. Neither has a fixed pixel width.

## Regenerating the PNG

From this directory, with the Chrome that is installed on the workstation:

```bash
cd /home/alberto/webserver/publish/acestream-suite/assets
/usr/bin/google-chrome --headless --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=1 --window-size=64,64 \
  --default-background-color=00000000 \
  --screenshot=/home/alberto/webserver/publish/acestream-suite/assets/favicon-64.png \
  file:///home/alberto/webserver/publish/acestream-suite/assets/logo-mark.svg
```

`--window-size=64,64` sets the raster to 64 × 64 (the SVG fills the viewport), and
`--default-background-color=00000000` keeps the corners of the rounded plate transparent, which is
why the result is RGBA. The command above was run on Google Chrome 155.0.8059.79.

## Trademark

The AceStream name and the AceStream mark belong to their owner. They are used here only to say what
acestream-suite plays through — a statement of fact about compatibility — and not to claim the brand,
suggest endorsement, or pass the suite off as AceStream's own product. acestream-suite and this logo
are ours; the mark inside it is not.
