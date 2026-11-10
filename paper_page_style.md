# Morning paper page: content and style

## Purpose
She will not read the paper. She skims this page on her phone in the morning,
in about five minutes, to learn something. Learning beats coverage: leave out
whatever doesn't teach her something.

## Sections, in order
1. **Header.** The paper title, authors (first three, then "et al."), year,
   venue and citation count if known, and a link to the paper. Under it, one
   plain sentence on what the paper does.
2. **In 60 seconds.** Three bullets: the problem, the idea, and the result.
   Use numbers from the paper where it has them.
3. **The linear algebra.** The objects (which matrices or operators, their
   shapes, and their structure) and the one decomposition or spectral fact
   that does the real work, with the actual equations in KaTeX. Mark each
   concept one of two ways:
   - **Known** (it's in known.txt): a small chip labelled "you know this" and
     one line on how *this paper* uses it. No re-explaining.
   - **New to her**: a highlighted card with the definition, a tiny worked
     example with real numbers (2×2 or 3×3), and one line of intuition.
     At most three of these. Pick the ones the paper actually depends on.
4. **One interactive.** Only if it truly illuminates the key idea: a small
   vanilla-JS widget, such as a slider, a matrix she can edit, or a
   rank-k approximation of a tiny image. Skip it rather than force it.
5. **Cool things.** Two to four surprising or clever points: a trick, a
   result that seems too good to be true, or a connection to something she
   knows (ML, graphics, quantum, PDEs, networks).
6. **If you remember one thing.** One sentence.
7. **Footer.** Credit (title, authors, year, link), plus a note if only the
   abstract was available.

## Look
- Clean and modern, like a claude.ai artifact. No hand-drawn or vintage
  styling.
- Colors as CSS custom properties on `:root`, with a dark theme under
  `@media (prefers-color-scheme: dark)`. `body` gets an explicit background.
- Use one Google Font family for text (e.g. Inter) and a monospace font for
  code. KaTeX from cdn.jsdelivr.net/npm/katex@0.16.11, with
  auto-render.
- Single column, max-width about 720px, with a 16px side gutter and no
  horizontal scroll at phone width. Cards have soft borders and rounded
  corners. Leave generous whitespace.
- Use one accent color. "Known" chips are muted; "new" cards use the accent.
- Must work both standalone and inside an iframe, the portfolio's Notebook
  modal. Don't use `position: fixed` headers.
- Self-contained: inline CSS and JS. The only external loads are the font
  and KaTeX.
- Keep it short. If a section runs long, cut it.
