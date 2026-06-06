# Handoff — "Protocols, coming soon" teaser (hero-style floating image)

**For:** an LLM coding agent working in this repo (`knowledge-graph/`), React +
Vite + TypeScript, `frontend/src/`.
**Goal:** a small, understated teaser at the **bottom of the home page** that hints
at a future "Protocols" feature (assignable programs like *Handstand*, *Brady*,
*Biles*), ending in an **ellipsis** — and whose image is rendered with the **same
treatment as the hero image** at the top: a figure that *floats* (its edge fades
into the page, no hard rectangle) with editorial type sitting over it.

The user will provide the image. Put it at `frontend/public/protocols-teaser.png`
(or `.webp`/`.jpg`) and reference it as `/protocols-teaser.png`.

---

## The look to match — how the hero works today

The hero is a **fixed, edge-masked figure** with content scrolling over it. The two
mechanics that create the "floating, no box" feel:

1. **A soft mask that fades one edge to transparent**, so the body meets the page
   instead of a rectangle.
2. **A scrim gradient** in the page color over the image, so text stays legible.

### Exact CSS in use (`frontend/src/index.css`) — replicate this technique

```css
/* the athlete is a FIXED background — content scrolls over it */
.bg-figure { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
.bg-figure img {
  position: absolute; right: 0; top: 0; height: 100vh;
  width: min(66vw, 920px); object-fit: cover; object-position: 56% 18%;
  /* fade the empty edge so the body — not a box — meets the page */
  -webkit-mask-image: linear-gradient(to right, transparent 0%, #000 42%);
  mask-image: linear-gradient(to right, transparent 0%, #000 42%);
}
.bg-scrim {
  position: absolute; inset: 0;
  background: linear-gradient(to right,
    var(--bg) 0%, rgba(237,238,241,0.5) 32%, rgba(237,238,241,0.1) 58%, transparent 80%);
}
```

### Markup pattern (`frontend/src/App.tsx`)

```jsx
<div className="bg-figure" aria-hidden="true">
  <img src="/athlete.jpg" alt="" />
  <div className="bg-scrim" />
</div>
{/* content sits above with position: relative; z-index: 1 */}
```

### Design tokens (use these — do not introduce new colors/fonts)

```
--bg:    #edeef1   /* pale cool gray-white page */
--ink:   #16161a
--sub:   #6f7178
--blue:  #7c2f3d   /* the ONLY accent (burgundy) */
--serif: "Cormorant Garamond", Georgia, serif   /* big editorial headlines */
--sans:  "Inter", system-ui, sans-serif         /* everything else */
```
Editorial rules: oversized light-weight serif headline, tiny uppercase tracked
labels, generous whitespace, burgundy used sparingly.

---

## What to build

A new component `frontend/src/components/ProtocolsTeaser.tsx`, mounted at the very
**bottom of the page in `App.tsx`** (after the existing `.sheet` content / footer,
as the last element before the floating copilot launcher). Keep it **small and
quiet** — a "leading little hint," not a full section.

### Composition (mirror the hero, scoped to a bottom band)

- A **contained band** (e.g. `min-height: 42vh`, full bleed within the page
  max-width), `position: relative; overflow: hidden`.
- The teaser **image, right-aligned**, with the **same left-fade mask** so it
  dissolves into the page:
  `mask-image: linear-gradient(to right, transparent 0%, #000 45%)` (tune the %).
  Plus a scrim in `--bg` like `.bg-scrim` so the copy is legible.
- **Editorial copy on the left, over the image:**
  - tiny uppercase tracked eyebrow: `COMING SOON`
  - large serif headline (`--serif`, weight 300, ~clamp(36px,5vw,64px)):
    `Protocols`
  - a `--sub` line naming the vision, ending in an **ellipsis**, e.g.
    *"Assign the Handstand protocol — or train like Brady, like Biles — then tailor
    it to this member…"* (the trailing `…` is required; it's the "leading hint").
- Optional, on-brand: three small **disabled** chips — `Handstand` · `Brady` ·
  `Biles` — styled like the existing `.chip` but muted (not clickable), to make
  "assignable programs" legible.

### "Scrolling text over the image" option
If the user wants the hero's literal effect (text scrolls over a *pinned* image),
give the band's image `position: sticky; top: 0` or reuse the `.bg-figure` fixed
technique scoped to this band (a wrapper with its own stacking context). Default to
the simpler contained band unless they ask for the pinned/parallax version.

### Don'ts
- No new color or font. No hard image rectangle (the mask is mandatory — that's the
  "float"). Don't make the chips clickable (it's coming soon). Don't push it to a
  separate route — it lives at the bottom of the existing home page.

### Acceptance
- Bottom-of-page teaser, image floats (edge-masked, no box), matches the hero's
  palette/type, copy ends in an ellipsis, reads as a quiet hint. `tsc --noEmit`
  passes; the existing hero, dashboard, and copilot are untouched.
```
