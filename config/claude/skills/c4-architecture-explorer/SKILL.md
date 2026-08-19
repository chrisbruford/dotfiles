---
name: c4-architecture-explorer
description: >-
  Produce a single-file, interactive C4 architecture explorer from architecture
  documentation or existing C4/Mermaid diagrams. The output is a zoomable web
  app: System Context → Container → Component (click-to-drill), plus Code and
  Deployment tabs, with ELK orthogonal auto-layout, pan/zoom, breadcrumb,
  optional deep-links to a source doc, and URL hash routing. Use when asked to
  "build/produce an interactive C4 diagram", "architecture explorer",
  "zoomable architecture web page", or to turn architecture docs / Mermaid C4
  into a navigable diagram. Liberis-branded by default.
---

# C4 Architecture Explorer

Turns an architecture description into one self-contained `.html` file that
reproduces the proven explorer. **The engine, styling, layout and interaction
are already built and bug-fixed in `template.html`. You only author the
`DIAGRAMS` data and a few brand/link strings — do not rebuild the engine.**

## Output (what you deliver)

A single HTML file (e.g. `<name>-c4.html`) that opens directly in a browser:

- **Strict C4 zoom tree** — System Context (L1) → click the focus system →
  Container (L2) → click the focus container → Component (L3). Breadcrumb +
  L1/L2/L3 rail + "Zoom out"/Esc.
- **Code** and **Deployment** as top-level tabs.
- **ELK** layered + orthogonal edge routing with nested boundaries.
- Pan (drag/scroll), pinch / ⌘-scroll / `+ −` zoom, "Fit", legend.
- **Deep-links** (optional): each box opens its section in a hosted source doc.
- **Hash routing**: `#context|#container|#component|#code|#deployment` — views
  are shareable, back/forward and reload work.
- Dependencies: brand fonts + ELK from CDN, with a bezier + authored-coordinate
  fallback if ELK is blocked. Liberis dark theme (Archivo / Plus Jakarta Sans /
  Fragment Mono); the palette is the Liberis house style.

## Input (what you accept)

Any of: architecture docs (Markdown / ADRs / prose), existing Mermaid C4
diagrams, a service/infra inventory, or a verbal description. You extract a C4
model from them. If the input is thin, ask which system is the **focus** and
which of its containers is worth a Component view.

## Procedure (no redundant steps)

1. **Copy the template** to the target path:
   `cp <skill_dir>/template.html ./<name>-c4.html`
2. **Author `DIAGRAMS`** — replace the example object (the only `EDIT HERE`
   block) with the extracted model. Keep the five keys
   `context, container, component, code, deployment`.
3. **Set brand strings**: the `<span class="brand-eyebrow">SYSTEM NAME</span>`
   (→ the system's name) and `<title>`. Keep `brand-title` as "C4 Architecture"
   unless told otherwise. The `:root` palette is the Liberis house style (see
   the `liberis-brand` skill); for a non-Liberis system, swap those CSS tokens.
4. **Deep-links (optional)**: if a hosted source doc with heading anchors
   exists, set `DOC_URL` and fill `DOC_MAP` (`{diagramId:{nodeId:slug}}`).
   Slug rule: lowercase, collapse runs of non-alphanumerics to `-`
   (`### 2.9 Credential isolation & egress` → `2-9-credential-isolation-egress`).
   Avoid headings with intra-word apostrophes (ambiguous slug). Leave persons
   and external systems unlinked.
5. **Verify** in a browser (see checklist). Fix data, not the engine.

## DIAGRAMS data model

Each of the five entries:

```js
context: {
  title, kicker:'C4 · Level 1', summary:'one line, <b> allowed',
  w, h,            // rough virtual-canvas px; ELK re-lays-out (seeds fit + fallback)
  dir:'RIGHT',     // ELK flow: RIGHT (default) | DOWN | LEFT | UP
  parent:'context',// container/component only: the diagram you drilled from
  groups:[ { id, tag, x, y, w, h, accent?:'var(--neon)', solid?:true } ],
  nodes:[  { id, name, tech, kind, icon, role, x, y, w, h, zoomTo? } ],
  edges:[  { from, to, label?, dashed?:true, undirected?:true } ],
}
```

- **role** (accent + meaning): `person | focus | system | external | data |
  gov | event | eval | infra`. `focus` = the in-spotlight box; the drillable
  one carries `zoomTo:'<child diagram id>'`.
- **icon**: `person agent box cloud db bolt shield gateway graph code network
  key repo chat doc clock check`.
- **Containment is inferred from geometry**: a node belongs to the *smallest
  group whose rectangle contains the node's centre*. Place each node's centre
  inside its boundary's rectangle; otherwise exact positions don't matter
  (ELK redoes layout). Nested groups work (group rect inside group rect).
- **Strict tree**: exactly one `context` node has `zoomTo:'container'` (the
  focus system) and one `container` node has `zoomTo:'component'` (the focus
  container). Code/Deployment are tabs, not drill targets.
- If a level doesn't apply: give it a minimal diagram, OR delete its entry and
  remove its `<button class="tab">` (code/deployment) and any `zoomTo` to it.

## Mapping inputs → the five C4 views

- **Context (L1)**: people/roles + external systems + the one focus software
  system. Edges = who-uses-what / system-to-external.
- **Container (L2)**: inside the focus system — its deployable units (services,
  apps, workers) + datastores + the externals it calls, wrapped in one boundary
  `group` (accent `var(--neon)`). One container is the focus → Component.
- **Component (L3)**: internals of that *one* most important container only.
- **Code (L4)**: repos / packages / modules and their dependencies; group the
  monorepo(s) as `solid` boundaries.
- **Deployment**: projects / networks / runtime services / managed infra;
  nest projects inside an org/account boundary.

When the source already has Mermaid C4 or §-numbered C4 views, map them
directly (subgraph → group, node → node, edge → edge) rather than inventing.

## Invariants (already in the template — keep them; they were hard-won)

Do **not** "simplify" these away when editing — each fixed a real bug:

- **Edge LCA offset**: ELK reports an edge's coordinates in the frame of its
  endpoints' lowest-common-ancestor group; the engine offsets each edge by that
  group's absolute origin. Without it, edges inside a boundary detach.
- **Font gate**: card sizes are measured only after `document.fonts` are loaded
  (`ensureFonts`); measuring with fallback fonts makes ELK reserve wrong sizes
  and boxes overlap.
- **Measure-then-layout**: cards render hidden, are measured, fed to ELK, then
  positioned. Cards are auto-height (no `overflow:hidden`) so text never clips.
- **Click vs pan**: panning starts only past a movement threshold and never
  captures the pointer on press, so box clicks (drill / deep-link) still fire.
- **Async `showDiagram`** with a re-entry guard; ELK options `layered` +
  `ORTHOGONAL` + `INCLUDE_CHILDREN`; group padding leaves room for the tag.

## Verify (required before reporting done)

Open the file with the `agent-browser` skill and:

1. Load with `?debug` and read the console — every view must log
   `[C4 audit] …: all N edges attached ✓` (zero detached). This is the
   regression guard for the edge-offset bug.
2. Screenshot each view; confirm no overlapping boxes and clean routing.
3. Click the focus system → Container, the focus container → Component; use the
   breadcrumb / Esc to go back.
4. Confirm hash routing: navigation updates the URL; loading at `#component`
   restores the Context›Container›Component stack.
5. If deep-links are set: a `↗` badge appears on hover and opens
   `DOC_URL#slug` in a new tab.

Then report the output path and offer to open or screenshot it.
