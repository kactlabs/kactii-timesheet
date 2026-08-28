# Theme Maker

## When to Use

This steering is activated when the user provides a web page URL or a screenshot and asks to convert it into a static HTML/CSS theme.

## Process

1. If a URL is provided, fetch the page and analyze its visual structure, layout, and design. If a screenshot is provided, analyze the image directly to extract visual structure, layout, and design.
2. Identify the key visual elements:
   - Layout structure (header, nav, sidebar, main content, footer)
   - Color scheme
   - Typography
   - Spacing and grid patterns
   - UI components (cards, buttons, lists, etc.)
3. Recreate the page as static HTML + CSS with this folder structure:
   ```
   html/
   ├── index.html
   └── static/
       └── style.css
   ```
4. The `index.html` should link to `static/style.css`.
5. Use semantic HTML5 elements where appropriate.
6. All styling goes in `static/style.css` — no inline styles.
7. Use placeholder text/images where dynamic content exists on the original page.
8. Make the layout responsive where the original design suggests it.

## Rules

- Do NOT copy any JavaScript functionality — this is purely a visual/HTML/CSS recreation.
- Do NOT use any CSS frameworks (Bootstrap, Tailwind, etc.) — write plain CSS.
- Do NOT fetch or hotlink images/assets from the original site. Use placeholder boxes or generic placeholder images instead.
- Keep the CSS organized with comments separating sections (header, nav, main, footer, etc.).
- Match the color scheme, font choices, and spacing as closely as possible from the original page.
- All output goes under the `html/` folder in the project root.

## Notes

- If the page is complex, focus on the above-the-fold layout and primary sections first.
- If a URL cannot be fetched, ask the user for a screenshot or description of the design.
- When working from a screenshot, extract colors, spacing, and layout as accurately as possible from the image.
