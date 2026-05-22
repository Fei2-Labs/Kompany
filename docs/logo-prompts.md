# Kompany Logo Generation Prompts

**Brand palette (from desktop UI):**
- Background: `#000000` pure black
- Primary: `#00ff41` matrix green
- Accent A: `#66ffff` cyan
- Accent B: `#ffaa00` amber
- Error: `#ff4444` red (avoid in logo)

**Color rule:** black bg + ONE accent from the palette above. No off-palette colors.

---

## Style 1 — Terminal K (app icon, 首选)

```
Minimal logo mark, letter K constructed from thin straight lines, pure black background #000000, single color #00ff41 matrix green, stroke only no fill, phosphor screen glow, monospace grid, flat vector, nothing else, no gradients, no other colors
```

**Midjourney params:** `--ar 1:1 --style raw --v 6 --no gradient --no color`

---

## Style 2 — Cyan Command Mark

```
Single color logo, capital letter K formed by angular slash shapes like a terminal cursor, pure black background #000000, only #66ffff cyan color, pixel grid, flat vector, CRT glow, ultra minimal, one ink only
```

**Midjourney params:** `--ar 1:1 --style raw --v 6 --no gradient --no color`

---

## Style 3 — Amber Crest (pitch deck)

```
Monochrome corporate emblem, letter K inside sharp angular hexagon frame, pure black background #000000, single color #ffaa00 amber line art, stroke only no fill, no shading, flat vector, executive tech company seal
```

**Midjourney params:** `--ar 1:1 --style raw --v 6 --no gradient --no color`

---

## Style 4 — Green Wordmark (header / banner)

```
Wordmark "KOMPANY" in bold condensed monospace typeface, pure black background #000000, single color #00ff41 matrix green letters, no glow, no shadow, no decoration, brutalist flat, tech brand
```

**Midjourney params:** `--ar 3:1 --style raw --v 6 --no gradient --no color`

---

## Style 5 — Scan Line Mark (最赛博)

```
Logo mark letter K, pure black background #000000, #00ff41 matrix green, horizontal scan line texture overlay, thin outline K letterform, phosphor terminal aesthetic, slight bloom glow, flat vector base, single color only
```

**Midjourney params:** `--ar 1:1 --style raw --v 6 --no gradient --no color`

---

## Print / light bg variant

Swap: `black background #000000` → `white background #ffffff`, color → `#007a1f` (darkened green for contrast)

---

## Notes

- App icon → Style 1 or 5
- Header → Style 4
- Deck / docs → Style 3 (amber = authority)
- Cyan variant → Style 2 (use where green already dominant in context)
- Assets → `docs/assets/logo/`
