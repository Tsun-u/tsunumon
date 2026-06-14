# D3.js API Reference for Diagram Rendering (v7)

Use D3 for biology/natural science/physics phenomena diagrams: cell cross-sections, vascular bundles, phylogenetic trees, organ schematics, energy diagrams, bouncing ball states, force arrows, pendulums, circuit schematics, wave propagation.

## Setup Pattern

```html
<svg id="diagram" width="500" height="400"></svg>
<script>
const svg = d3.select('#diagram');
// Build diagram with svg.append(...)
</script>
```

## Core Selection & Append

```javascript
const svg = d3.select('#id');          // Select element
svg.append('circle')                    // Append SVG element
   .attr('cx', 100).attr('cy', 100)    // Set attributes
   .attr('r', 50)
   .attr('fill', '#e53935')
   .attr('stroke', '#b71c1c')
   .attr('stroke-width', 1);

svg.append('line')
   .attr('x1', 0).attr('y1', 0)
   .attr('x2', 100).attr('y2', 100)
   .attr('stroke', '#333').attr('stroke-width', 2);

svg.append('rect')
   .attr('x', 10).attr('y', 10)
   .attr('width', 80).attr('height', 60)
   .attr('rx', 8)                       // Rounded corners
   .attr('fill', '#e3f2fd');

svg.append('text')
   .attr('x', 100).attr('y', 50)
   .attr('text-anchor', 'middle')       // center, start, end
   .attr('font-size', '16px')
   .attr('fill', '#333')
   .attr('stroke', '#fff')              // Text outline
   .attr('stroke-width', '3')
   .attr('paint-order', 'stroke fill')
   .text('Label');

svg.append('path')
   .attr('d', 'M10,80 Q50,10 90,80')   // Quadratic curve
   .attr('fill', 'none')
   .attr('stroke', '#2196f3');

svg.append('ellipse')
   .attr('cx', 150).attr('cy', 150)
   .attr('rx', 80).attr('ry', 50)
   .attr('fill', 'rgba(200,230,201,0.3)')
   .attr('stroke', '#2e7d32');
```

## Data-Driven Patterns (loops)

```javascript
// Ring arrangement (e.g., dicot vascular bundles)
const bundles = [];
for (let i = 0; i < 8; i++) {
    const angle = (i / 8) * Math.PI * 2;
    bundles.push({
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius
    });
}
svg.selectAll('.bundle')
   .data(bundles).enter()
   .append('circle')
   .attr('cx', d => d.x).attr('cy', d => d.y)
   .attr('r', 6).attr('fill', '#e53935');

// Scattered arrangement (e.g., monocot vascular bundles)
const scattered = [];
for (let i = 0; i < 20; i++) {
    let x, y, dist;
    do {
        x = cx + (Math.random() - 0.5) * range;
        y = cy + (Math.random() - 0.5) * range;
        dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
    } while (dist > maxR || dist < minR);
    scattered.push({ x, y });
}

// Tree / branching (e.g., leaf veins, phylogenetic trees)
function drawBranch(svg, x1, y1, angle, length, depth) {
    if (depth === 0) return;
    const x2 = x1 + Math.cos(angle) * length;
    const y2 = y1 + Math.sin(angle) * length;
    svg.append('line')
       .attr('x1', x1).attr('y1', y1)
       .attr('x2', x2).attr('y2', y2)
       .attr('stroke', '#2e7d32')
       .attr('stroke-width', depth);
    drawBranch(svg, x2, y2, angle - 0.4, length * 0.7, depth - 1);
    drawBranch(svg, x2, y2, angle + 0.4, length * 0.7, depth - 1);
}
```

## Text Label Rules

All `<text>` elements MUST have:
- `font-size`: 14-24px
- `stroke` + `paint-order="stroke fill"` for outline
- `text-anchor` for alignment
- **Z-order**: append text AFTER decorative elements (dots, circles, arrows) so text renders on top. SVG renders in document order — later elements appear above earlier ones. Labels must never be hidden behind decorative shapes.
- **No overlap**: every element (circle, rect, text, label) must have its own clear space. Overlapping elements look broken even if the content is related.
- **Summary/caption outside SVG**: use an HTML `<div>` below the `<svg>` for summary boxes, key takeaways, and captions. SVG should only contain the diagram itself. HTML elements flow naturally in the page layout and won't overlap the diagram.
- **Legend outside chart area**: place legends outside the data region (e.g., above or to the right with enough margin). Legends overlapping bars, circles, or data points make both unreadable.

```javascript
svg.append('text')
   .attr('font-size', '16px')
   .attr('stroke', '#fff')
   .attr('stroke-width', '3')
   .attr('paint-order', 'stroke fill')
   .attr('fill', '#333');
```

## Animation with D3 Transitions

Use D3's built-in `.transition()` for animations. Labels and their parent elements update together in the same transition — this prevents labels from drifting away from the element they describe.

```javascript
// Pendulum example: bob AND its labels move together
function swing(angle) {
    const x = pivotX + Math.sin(angle) * length;
    const y = pivotY + Math.cos(angle) * length;

    // Bob and string update together
    svg.select('#string').transition().duration(500)
       .attr('x2', x).attr('y2', y);
    svg.select('#bob').transition().duration(500)
       .attr('cx', x).attr('cy', y);

    // Labels follow in the SAME transition
    svg.select('#length-label').transition().duration(500)
       .attr('x', (pivotX + x) / 2 + 10)
       .attr('y', (pivotY + y) / 2);
}
```

Key rule: every label that annotates a moving element must be in the same `.transition()` call, so positions update atomically. Static labels (axis titles, legends) stay fixed.

## Physics Simulation on Screen Coordinates

When simulating physics (pendulums, projectiles, springs) using pixel coordinates, normalize the constants instead of using real-world SI values. Screen pixels are not meters — using `g = 9.8` with `L = 240` pixels gives a 31-second period, far too slow for a teaching animation.

```javascript
// BAD: real SI constants with pixel lengths → period ≈ 31s
var g = 9.8;   // m/s²
var L = 240;   // pixels (NOT meters!)
var alpha = -(g / L) * Math.sin(angle);  // way too slow

// GOOD: normalized ratio → period ≈ 3s, visually clear
var gOverL = 4;  // tuned for ~3s period: T = 2π/√(gOverL) ≈ 3.14s
var alpha = -gOverL * Math.sin(angle);
```

Target a period of 2–4 seconds so the viewer sees multiple complete cycles within the narration time.

**Pixel lengths must reflect real-world ratios**: When comparing two objects side by side (e.g., short vs long pendulum, small vs large cell), their pixel sizes must be proportional to the stated real-world values. If one string is L=0.5m and another is L=2m, the pixel lengths should be roughly 1:4 (e.g., 60px vs 240px). Equal-looking lengths with different labels mislead the viewer — the visual comparison IS the teaching point.

Animation duration: use `requestAnimationFrame` without a hard frame limit — let it run for the full slide duration. If you need a stop condition, base it on elapsed real time (e.g., `Date.now() - startTime < durationMs`), not a fixed frame count. A hard `frame < 600` cap stops the animation at ~10 seconds regardless of how long the slide plays.
