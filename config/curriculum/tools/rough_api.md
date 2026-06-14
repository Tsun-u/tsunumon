# Rough.js API Reference (v4.6)

Use Rough.js for hand-drawn-feel concept diagrams: neural network architecture, abstract system diagrams, flowcharts, concept maps.

## Setup Pattern

```html
<canvas id="diagram" width="500" height="400"></canvas>
<script>
const rc = rough.canvas(document.getElementById('diagram'));
// Draw with rc.circle(), rc.rectangle(), rc.line(), etc.
</script>
```

Or with SVG:
```html
<svg id="diagram" width="500" height="400"></svg>
<script>
const rc = rough.svg(document.getElementById('diagram'));
const node = rc.circle(100, 100, 80);  // returns SVG node
document.getElementById('diagram').appendChild(node);
</script>
```

## Drawing Methods

```javascript
// Circle: rc.circle(x, y, diameter, options)
rc.circle(100, 100, 80, {
    stroke: '#333',
    strokeWidth: 2,
    fill: 'rgba(66,165,245,0.2)',
    fillStyle: 'solid',       // solid, hachure, zigzag, cross-hatch, dots
    roughness: 1.2             // 0 = smooth, 3 = very rough
});

// Rectangle: rc.rectangle(x, y, width, height, options)
rc.rectangle(50, 50, 200, 100, {
    stroke: '#1565c0',
    fill: '#e3f2fd',
    fillStyle: 'solid'
});

// Line: rc.line(x1, y1, x2, y2, options)
rc.line(50, 50, 200, 200, {
    stroke: '#333',
    strokeWidth: 2,
    roughness: 0.8
});

// Ellipse: rc.ellipse(x, y, width, height, options)
rc.ellipse(150, 150, 200, 120, {
    stroke: '#2e7d32',
    fill: 'rgba(200,230,201,0.3)',
    fillStyle: 'solid'
});

// Arc: rc.arc(x, y, width, height, startAngle, endAngle, closed, options)
rc.arc(150, 150, 200, 200, 0, Math.PI, false, {
    stroke: '#e65100'
});

// Path: rc.path(svgPath, options)
rc.path('M 10 80 Q 50 10 90 80', {
    stroke: '#9c27b0',
    strokeWidth: 2
});

// Polygon: rc.polygon([[x1,y1], [x2,y2], [x3,y3], ...], options)
rc.polygon([[100,20], [180,80], [140,160], [60,160], [20,80]], {
    stroke: '#f44336',
    fill: 'rgba(244,67,54,0.1)',
    fillStyle: 'solid'
});
```

## Fill Styles

| Style | Effect |
|-------|--------|
| `hachure` | Default, parallel lines (hand-drawn feel) |
| `solid` | Solid fill |
| `zigzag` | Zigzag lines |
| `cross-hatch` | Crossed parallel lines |
| `dots` | Dot pattern |

## Options Reference

| Option | Default | Description |
|--------|---------|-------------|
| `roughness` | 1 | 0=smooth, higher=rougher |
| `bowing` | 1 | How much lines bow |
| `stroke` | `'black'` | Stroke color |
| `strokeWidth` | 1 | Stroke width |
| `fill` | none | Fill color |
| `fillStyle` | `'hachure'` | Fill pattern |
| `fillWeight` | 0.5 | Weight of fill lines |
| `seed` | random | Fixed seed for reproducible output |

## Text Labels

Rough.js does NOT draw text. Use Canvas 2D API for labels:

```javascript
const canvas = document.getElementById('diagram');
const ctx = canvas.getContext('2d');
ctx.font = '16px sans-serif';
ctx.fillStyle = '#333';
ctx.textAlign = 'center';
ctx.fillText('Label', x, y);
```

Or use SVG mode and append `<text>` elements with D3/DOM API (with stroke outline rules: 14-24px, paint-order).

### Canvas Text Layout Rules

Canvas text is positioned by absolute pixel coordinates — overlapping text is invisible until rendered. Follow these rules:

1. **Minimum 24px vertical gap** between any two `fillText` calls. Text at y=48 and y=52 will overlap — space them at least 24px apart.
2. **Annotations go outside shapes** — place labels with a 20px+ margin from the nearest circle/rectangle edge. Text sitting on a shape border becomes unreadable.
3. **Keep text 30px+ from canvas edges** — labels at x=10 or y=canvas.height-5 get clipped or crowd the frame.
4. **One label position per element** — if a node needs both a title and a subtitle, stack them vertically with 16px gap (e.g. y and y+16), not crammed into 4px.
5. **Verify before drawing**: before writing coordinates, sketch the layout mentally — list every `fillText` call with its (x, y) and confirm no two are within 24px vertically at similar x positions.

## Neural Network Example

```javascript
const rc = rough.canvas(document.getElementById('nn'));
const layers = [3, 4, 4, 2];  // nodes per layer
const layerX = [80, 200, 320, 440];
const startY = 60, gapY = 80;

layers.forEach((n, li) => {
    for (let i = 0; i < n; i++) {
        const y = startY + i * gapY + (4 - n) * gapY / 2;
        rc.circle(layerX[li], y, 30, {
            fill: ['#bbdefb','#c8e6c9','#fff9c4','#ffcdd2'][li],
            fillStyle: 'solid', roughness: 0.8
        });
        // Connect to next layer
        if (li < layers.length - 1) {
            for (let j = 0; j < layers[li + 1]; j++) {
                const ny = startY + j * gapY + (4 - layers[li+1]) * gapY / 2;
                rc.line(layerX[li] + 15, y, layerX[li + 1] - 15, ny, {
                    stroke: '#90a4ae', strokeWidth: 0.5
                });
            }
        }
    }
});
```
