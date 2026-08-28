---
inclusion: manual
---

# Static HTML Maker

Convert all dynamic web pages into static HTML under the `html` folder.

## Structure

```
html/*.html
html/static/*.css
html/static/*.js
```

## Rules

- This is only for static HTML reference purposes. The `html` folder is NOT used by the project.
- All Jinja2 templates should be fully rendered (no template tags remaining).
- All includes (header, footer, toast, analytics) must be inlined.
- The homepage file should be named `home.html` (not `index.html`).
- All internal links (`href`) must be converted to relative `.html` paths so pages are navigable when opened directly from the filesystem.
- Admin subpages use `../` to link back to root-level pages.
- Dynamic routes map to static files:
  - `/` → `home.html`
  - `/shop` → `shop.html`
  - `/products/{slug}` → `product.html`
  - `/blogs` → `blogs.html`
  - `/blogs/{slug}` → `blog.html`
  - `/cart` → `cart.html`
  - `/checkout` → `checkout.html`
  - `/orders` → `orders.html`
  - `/orders/{id}` → `order_detail.html`
  - `/login` → `login.html`
  - `/profile` and `/profile/*` → `profile.html`
  - `/spin-wheel` → `spin_wheel.html`
  - `/admin/login` → `admin/login.html`
  - `/admin` and `/admin/*` → `admin/panel.html`
- Only copy `.css` and `.js` files into `html/static/` (no images).
- All static HTML files should be connectible by links (navigable between pages).
