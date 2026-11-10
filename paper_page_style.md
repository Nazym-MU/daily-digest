# Morning paper page: content and style

## Purpose
She will not read the paper. She skims this page on her phone in the morning,
in about five minutes, to learn something. Learning beats coverage: leave out
whatever doesn't teach.

## How to explain (her feedback; the most important part)
- **Never use a symbol, abbreviation or term before saying what it is in plain
  words.** Bad: "learn ΔΦ with |Θ| ≪ |Φ₀|". Good: "Φ is every weight in the
  pretrained model and ΔΦ the change fine-tuning makes to them; LoRA instead
  learns a much smaller set of numbers Θ that generates ΔΦ". Benchmark and
  model names get a clause too, or get dropped.
- **Explanatory, not basic.** She is a math-track CS student who TA'd linear
  algebra. Don't explain matrix multiplication, rank, gradients or
  eigenvalues. Do explain the paper's setup, its objects, and every step of
  its argument, so a cold reader can follow.
- **Don't guess what she knows.** No "you know this" or "new to you" labels,
  and no recaps of her background. Just explain.
- The "In 60 seconds" section must make sense on its own to someone who has
  never heard of the paper.

## Sections, in order
1. **Header.** The paper title, authors (first three, then "et al."), year,
   venue and citation count if known, and a link to the paper. Under it, one
   plain sentence on what the paper does.
2. **In 60 seconds.** Three short paragraphs: the problem, the idea, and the
   result. Use numbers from the paper where it has them.
3. **The linear algebra.** The objects (which matrices or operators, what
   they represent, their shapes and structure) and the one decomposition or
   spectral fact that does the real work. Include the actual equations in
   KaTeX, and introduce each symbol in words. Where a step is subtle, add a
   tiny worked example with real numbers (2×2 or 3×3).
4. **Try it.** One or two small vanilla-JS interactives that make the key
   idea tangible, such as a slider, a matrix she can edit, or a rank-k
   approximation of a tiny image. She loves these, so always include at least
   one. Each gets a caption on what to try and what to notice.
5. **Cool things.** Two to four surprising or clever points: a trick, a
   result that seems too good to be true, or a connection to ML, graphics,
   quantum, PDEs or networks.
6. **If you remember one thing.** One sentence.
7. **Footer.** Credit (title, authors, year, link), plus a note if only the
   abstract was available.

## Look
Match the Notebook's existing pages exactly: the hand-drawn, black-and-white
vintage broadside design (cream paper, pen-and-ink line art, condensed caps
headers, brush-script title).
- Read `public/learning/combinatorial-surfaces.html` (in the portfolio repo)
  and reuse its `<head>`: fonts, CSS variables, palette, typography, section
  headers, rules, and how it loads and renders KaTeX. Also skim the newest
  `public/learning/paper-*.html` as a model of a paper page in this design.
- The design system is the handdrawn-explainer skill
  (`~/.claude/skills/handdrawn-explainer/SKILL.md`). Diagrams are inline-SVG
  pen-and-ink line art, never coloured.
- Interactives are in the ink look too: black, cream, and hatching.
- Single column, readable at phone width (375px) with a 16px side gutter and
  no horizontal scroll.
- It must work standalone and inside the Notebook iframe. No `position: fixed`.
- Self-contained: inline CSS/JS. External loads are only what the reference
  page already loads (Google Fonts, KaTeX).
- Keep it short. If a section runs long, cut it.
