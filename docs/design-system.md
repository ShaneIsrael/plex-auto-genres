# Design system — "Master Control"

The UI is a monitoring console for a media library's metadata, not a marketing
dashboard. The reference image is a projectionist's booth or a broadcast master-control
room at night: dark, warm, precise, every number legible from across the room. Dense
where data lives, quiet everywhere else.

## Colour

Warm near-black, never blue-black. Two functional accents, chosen so that colour *means*
something: **amber is the Plex side (writes)**, **teal is the provider side (reads)**.
That maps onto the tool's actual data flow — read from TMDB/MAL, write to Plex — and it
nods to both brands (Plex orange, TMDB teal) without copying either.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0e0d0b` | Page |
| `--surface-1/2/3` | `#151311` / `#1c1916` / `#242019` | Cards, raised, hover |
| `--ink` | `#f2ede4` | Primary text (14.7:1 on bg) |
| `--ink-2` | `#b3aa9c` | Secondary (7.4:1) |
| `--ink-3` | `#776f66` | Tertiary, labels (3.9:1 — large/mono only) |
| `--line` | `rgba(242,237,228,.09)` | Hairlines |
| `--amber` | `#f0a52a` | Plex / writes / warning / running |
| `--teal` | `#3ec9b0` | Providers / reads / links |
| `--ok` | `#5ccf7a` | Success |
| `--fail` | `#ff5c5c` | Error |

Status is never conveyed by colour alone: every status dot has a text label.

## Type

- **Bricolage Grotesque** — display: page titles and the big numerals. Its optical-size
  axis is set explicitly so counts at 72px and titles at 28px do not look like the same
  font stretched.
- **IBM Plex Mono** — every label, id, timestamp, count, table cell. A monospaced UI
  *is* the console feel, and tabular figures come free. (Yes, "Plex".)
- **IBM Plex Sans** — running text only.

Sizes: 12 / 13 / 14 / 16 / 20 / 28 / 44 / 72. Mono UI labels are uppercase at 12px with
`letter-spacing: .08em`; body is 15px/1.55.

## Surface and texture

Hairline borders instead of shadows for elevation (shadows read as mud on warm black).
A fixed scanline + grain overlay at ~3% gives the phosphor feel; it is static and is
removed entirely under `prefers-reduced-motion`. Corners are 6px — tight, industrial.

## Layout

Left rail (232px) at ≥1024px with numbered mono section labels (`01 · Overview`);
collapses to a top bar below that. A status strip along the top of the content area
carries the three things an operator glances at: Plex link, last run, doctor verdict.
Content maxes at 1240px. Spacing on a 4px grid; section rhythm 16 / 24 / 40.

## Motion

One orchestrated page-load: elements fade-up 8px with a 35ms stagger, 320ms ease-out.
Running jobs pulse the amber dot. Overview counts tick up over 600ms. Transitions
150–250ms. Nothing animates width/height. All of it is disabled by
`prefers-reduced-motion`.

## Signature

The **run tape** on Overview: the last forty runs as a strip of thin vertical blocks,
coloured by outcome, newest at the right — the console's tape counter. It is the one
thing to remember the page by, and it is also the fastest way to see "has something been
failing all week".

## Not doing

Purple gradients, glassmorphism, Inter, drop-shadow elevation, emoji as icons, light mode
(deferred, not refused — the tokens are semantic so it is additive).
