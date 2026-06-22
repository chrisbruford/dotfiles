---
name: liberis-brand
description: "Apply the Liberis brand identity — colours, typography, tone of voice, and visual style — to Liberis artifacts intended for human readers. Apply automatically for: HTML pages, web UIs, React components, SVG graphics, presentations (pptx), Word documents, PDFs, spreadsheets, emails, Slack canvases, dashboards, data visualisations, Mermaid diagrams in non-technical documents, internal reports, and any written content aimed at business stakeholders. DO NOT apply for: technical documentation (READMEs, CLAUDE.md, ADRs, API specs, architecture docs, changelogs), source code files, git commit messages, PR descriptions, inline code comments, shell scripts, config files, or engineering-to-engineering conversations in Claude Code. This skill layers brand rules on top of format-specific skills (pptx, docx, xlsx, etc.). Always read this skill alongside the format-specific skill when producing Liberis output."
---

# Liberis Brand Identity — Organisation Style Guide

This skill defines how every artifact Claude creates for Liberis should look, read, and feel. It covers colour, typography, tone of voice, layout principles, data visualisation, logo usage, and visual identity.

The brand idea is **"Never Stand Still"** — everything should feel forward-moving, energised, and clear. The positioning: *"We are the world's financial interscape, putting business in flow."*

---

## Colour Palette

The Liberis palette is predominantly **Ash** (dark), **Light Grey**, and **Neon Yellow**. Secondary colours are reserved for data visualisation and small highlights — never for large background fills.

### Primary Palette

| Name | HEX | RGB | Use |
|------|-----|-----|-----|
| **Neon Yellow** | `#E7FF7C` | 231, 255, 124 | Primary accent, hero backgrounds, highlights, CTA buttons |
| **Ash** | `#232C2F` | 35, 44, 47 | Primary dark background, primary text colour on light backgrounds |
| **Light Ash** | `#4F5659` | 79, 86, 89 | Secondary dark text, muted headings |
| **True Black** | `#000000` | 0, 0, 0 | Use sparingly — prefer Ash |
| **White** | `#FFFFFF` | 255, 255, 255 | Light backgrounds, text on dark |
| **Light Grey** | `#F4F4EC` | 244, 244, 236 | Default light background, text on dark backgrounds |
| **Mid-Light Grey** | `#E9E9DE` | 233, 233, 222 | Subtle background variation, hover states |
| **Mid Grey** | `#D4D4C3` | 212, 212, 195 | Borders, dividers, subtle separators |
| **Dark Grey** | `#B3B3AA` | 179, 179, 170 | Muted text, captions, metadata |

### Secondary Palette (data visualisation and small accents only)

| Name | HEX | 60% Tint | 20% Tint |
|------|-----|----------|----------|
| **Teal** | `#52F6BF` | `#97FAD9` | `#DCFDF2` |
| **Olive** | `#A1A553` | `#C7C998` | `#ECEDDD` |
| **Powder** | `#ABB6F4` | `#CDD3F8` | `#EEF0FD` |
| **Indigo** | `#584BF0` | `#9B93F6` | `#DEDBFC` |
| **Tangerine** | `#FFB175` | `#FFD0AC` | `#FFEFE3` |
| **Dahlia** | `#E65CFD` | `#F09DFE` | `#FADEFF` |

### Status Colours

| Name | HEX | Use |
|------|-----|-----|
| **Dark Teal** | `#13D58F` | Success states |
| **Tangerine** | `#FFB175` | Pending / Warning states |
| **Red** | `#CD1900` | Error states |

### Colour Rules — follow these strictly

- Text is **only ever Ash (#232C2F) or Light Grey (#F4F4EC)**. Never use coloured text.
- On dark backgrounds (Ash, Indigo), use Light Grey or White text.
- On light backgrounds (White, Light Grey), use Ash text.
- Neon Yellow elements on light backgrounds need a 1px border/keyline (#D4D4C3) for contrast.
- Secondary colours are for data visualisation and small accents. Never use them as large backgrounds.
- Never create gradients. The brand uses flat, solid colours.
- Never invent new colour pairings outside this system.

---

## Typography

Liberis uses custom typefaces. For digital artifacts where custom fonts aren't available, use the Google alternative stack. When the user's system has Liberis brand fonts installed, prefer those.

### Digital / Web / Artifact Type Stack (Google Alternatives)

| Role | Font | Weight | Line Height | Letter Spacing | Case |
|------|------|--------|-------------|----------------|------|
| **Headline 1** | Archivo (125 width) | Regular | 100% | -0.03em | Sentence or UPPERCASE |
| **Headline 2** | Archivo (125 width) | Regular | 110% | -0.03em | Sentence or UPPERCASE |
| **Body 1** | Plus Jakarta Sans | Regular | 135% | -0.01em | Sentence case |
| **Body 2** | Plus Jakarta Sans | Regular | 140% | -0.01em | Sentence case |
| **Subtitles / Labels** | Fragment Mono | Regular | 130% | 0 | UPPERCASE always |
| **Details / Buttons** | Fragment Mono | Regular | 130% | 0 | UPPERCASE always |

### Liberis Brand Fonts (when available)

| Role | Font | Weight |
|------|------|--------|
| **Headlines** | Liberis Display | Regular |
| **Body** | Modern Gothic | Light |
| **Subtitles / Labels / Details** | Foundry Plek | Medium |

### Typography Rules

- Left-align headlines when there's body copy beneath them.
- Centre-align headlines only when heroing a message with no body copy.
- Subtitles (Fragment Mono / Foundry Plek) are **always UPPERCASE**.
- Never use the headline font at small sizes — it's designed for big, bold key stats and titles only.
- Never mix UPPERCASE and sentence case within the same text element.
- Respect the tracking/letter-spacing values — never set type too tight.
- For HTML/React artifacts: import Google Fonts via `@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@125,400;125,700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=Fragment+Mono&display=swap');`

---

## Layout Principles

- **Left-aligned layouts** are the default. Centre-align only for hero messages.
- **Generous whitespace** — the brand is clean and modern. Give content room to breathe.
- **Rounded corners** — use subtle border-radius (8px–12px) on cards, containers, and UI modules.
- **Dark/light rhythm** — alternate between Ash and Light Grey sections for visual contrast.
- For documents and pages, use a "sandwich" structure: dark opener, light content, dark closer.
- Avoid visual clutter. Every element should earn its place.

---

## Visual Elements & Graphic Style

- Every section benefits from a visual element — avoid text-only blocks when possible.
- Use the Liberis "Interscape" graphic asset (geometric, layered shapes in teal/purple/yellow) for hero areas.
- For icons, prefer simple line-style icons in Ash or Light Grey.
- For photography, use professional lifestyle imagery showing real small business owners.
- Never place text directly on top of complex graphics — use a transparent panel or clear space.
- Pill-shaped badges/tags (Neon Yellow background with Ash text, rounded-full corners) are a key brand element — use them for section labels, categories, and CTAs.

---

## Data Visualisation

When creating charts, graphs, or data displays:

### Colour Allocation

- **Up to 3 data series**: Use 1 secondary colour at 100%, 60%, and 20% tint
- **4–6 data series**: Use 2 secondary colours, each at 100%, 60%, and 20%
- **7–9 data series**: Use 2 secondary colours + 1 neutral, each at 100%, 60%, and 20%

### Preferred Chart Colour Pairings

- Teal + Indigo (default pairing)
- Powder + Tangerine
- Teal + Dahlia

### Chart Rules

- Prefer solid bar charts over line charts. Use flat colour blocks.
- Charts should go edge-to-edge within their container — no gaps between bars.
- Use Fragment Mono (UPPERCASE) for axis labels and data labels.
- Large stat callouts work well — display big numbers in the headline font.
- Use the Neon Yellow accent for highlighting key data points or the most important metric.

---

## Tone of Voice (for all written content)

### 1. Be Radically Clear
- Be explicit about benefits — use concrete numbers and specifics.
- Keep sentences short and punchy.
- Use consistent terminology across all materials.

### 2. Bring the Energy
- Share opinions with attitude — have a point of view.
- Use calls-to-action that create momentum (start with action verbs).
- Play with alliteration and turns of phrase.
- Choose words with momentum: "transform", "power", "build", "launch", "unlock", "accelerate".

### 3. Lighten It Up
- Speak conversationally — use "you" and "your".
- Point to benefits beyond finance — "thrive", "grow", "flow".
- Use fresh, unexpected word choices to avoid sounding corporate.

### Key Brand Lines

- Tag line: **"A new era for embedded finance"**
- Descriptor: **"Start offering transformative funding to your small business customers, seamlessly through your platform."**
- Brand idea: **"Never Stand Still"**

### Proof Points (use as framing devices)

- Active in 18+ countries, 30+ partners
- 5-second decisions, funding in 24 hours, one API
- 17 years in business, $1bn+ provided, 94% recommend

---

## Logo Usage

- Use the **horizontal lock-up** for most applications.
- On Ash / dark backgrounds: use the **Light Grey** or **Neon Yellow** logo.
- On light backgrounds: use the **Ash** logo.
- Never distort, rotate, outline, or apply effects to the logo.
- Never use the logo in secondary colours.
- Give the logo clear space equal to the width of one ribbon of the marque icon.
- In digital artifacts, position the logo in the top-right or top-left corner.

---

## Co-Branding (Partner Materials)

When creating content featuring a partner brand alongside Liberis:
- Use **Plus Jakarta Sans** (or Modern Gothic) for all type — not the headline display font.
- Adopt the **partner's colour scheme** with Liberis neutral colours as complements.
- Place partner logo and Liberis logo on the same baseline at matching height.
- Data visualisation follows Liberis styling but can use partner colours.
- Never use Liberis Display / Archivo headlines or Liberis brand accent colours in co-branded materials — keep it neutral.

---

## Applying the Brand to Specific Artifact Types

### HTML & React Artifacts
- Set `background-color: #F4F4EC` for light pages or `#232C2F` for dark pages.
- Use the Google Fonts import for Archivo, Plus Jakarta Sans, and Fragment Mono.
- Style buttons with `background: #E7FF7C; color: #232C2F; border-radius: 999px; font-family: 'Fragment Mono'; text-transform: uppercase;`
- Cards: `background: #FFFFFF; border: 1px solid #D4D4C3; border-radius: 12px;` (on light) or `background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px;` (on dark).
- Section tags/pills: `background: #E7FF7C; color: #232C2F; padding: 6px 20px; border-radius: 999px; font-family: 'Fragment Mono'; text-transform: uppercase; font-size: 12px;`

### Markdown Documents
- Use Ash-coloured headings (conceptually — the rendering handles it).
- Keep the tone energised and clear following voice guidelines above.
- Use "---" dividers to create visual breathing room.
- Prefer tables over long bullet lists.
- Use bold sparingly for key stats and metrics.

### Mermaid Diagrams
- Use Ash (`#232C2F`) for node backgrounds and Light Grey (`#F4F4EC`) for text.
- Use Neon Yellow (`#E7FF7C`) for highlighted/active nodes.
- Use Teal (`#52F6BF`) and Indigo (`#584BF0`) for connecting lines.

### SVG Graphics
- Primary fill: `#232C2F` (Ash) and `#E7FF7C` (Neon Yellow).
- Secondary fills from the data viz palette only.
- Rounded rectangles with `rx="12"`.
- Text in Plus Jakarta Sans or Fragment Mono (uppercase for labels).

### Spreadsheets (via xlsx skill)
- Header row: Ash background (#232C2F) with Light Grey text (#F4F4EC).
- Accent rows/highlights: Neon Yellow (#E7FF7C) with Ash text.
- Alternating row colours: White and Light Grey (#F4F4EC).
- Use Fragment Mono for numeric data columns.

### Word Documents (via docx skill)
- Heading colour: Ash (#232C2F).
- Body font: Plus Jakarta Sans (or fallback to Calibri).
- Accent elements (table headers, callout boxes): Neon Yellow (#E7FF7C) background.
- Page background: White.
- Table style: Ash header row, Light Grey alternating rows.

### PDFs (via pdf skill)
- Follow the same colour and typography rules as Word documents.
- Use Neon Yellow pill badges for section markers.
- Cover page: Ash background with centred Liberis logo and Neon Yellow accents.

### Integration Guides (via integration-guide skill)
- These already follow Liberis branding — this skill reinforces the colour and type system.

### Presentations (via liberis-pptx skill)
- The liberis-pptx skill has its own detailed rules — defer to it for slide-specific guidance.
- This skill provides the underlying colour and typography values that feed into it.

---

## Don'ts Checklist — verify before delivering ANY artifact

Before finalising any Liberis-branded output, check for these violations:

- No secondary colours used for text — text is only Ash or Light Grey
- No secondary colours used as large/full backgrounds
- No new colour pairings invented outside the defined system
- No gradients anywhere
- No inaccessible colour pairings (check contrast)
- No headline font used at small body-text sizes
- No Fragment Mono / Foundry Plek in sentence case (always UPPERCASE)
- No mixing of UPPERCASE and sentence case in one element
- No accent underlines beneath titles (this is an AI writing hallmark — use whitespace instead)
- No logo distortion, rotation, or effects
- No generic "corporate" tone — keep it energised and clear
- No overly formal language — Liberis speaks conversationally
