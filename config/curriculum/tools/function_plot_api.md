# function-plot.js API Reference & Style Guide

Use function-plot for mathematical function graphs: parabolas, sine waves, regression lines, error curves, distributions.

## Setup Pattern

```html
<div id="plot" style="width:800px;height:400px;margin:0 auto;"></div>
<script>
document.addEventListener('DOMContentLoaded', function() {
    if (typeof functionPlot === 'undefined') return;
    functionPlot({
        target: '#plot',
        width: 800,
        height: 400,
        xAxis: { domain: [0, 10], label: 'x' },
        yAxis: { domain: [0, 20], label: 'y' },
        data: [
            { fn: '-x^2 + 4*x + 5', color: '#e74c3c' }
        ]
    });
});
</script>
```

## ⚠️ Function Expression Syntax

The `fn` field uses a built-in math evaluator, NOT JavaScript. Write math function names directly — do not use the `Math.` prefix:

| ✅ Correct | ❌ Wrong |
|-----------|---------|
| `exp(x)` | `Math.exp(x)` |
| `sin(x)` | `Math.sin(x)` |
| `cos(x)` | `Math.cos(x)` |
| `log(x)` | `Math.log(x)` |
| `sqrt(x)` | `Math.sqrt(x)` |
| `PI` | `Math.PI` |

Other JS diagram libraries (JSXGraph, D3) use standard JavaScript — this rule applies only to function-plot's `fn` strings.

## ⚠️ MANDATORY Style Rules (for classroom readability)

### 1. Font Size — MUST override defaults (defaults are too small!)

After calling `functionPlot()`, apply these CSS overrides in a `<style>` block:

```css
/* Place this INSIDE the slide_html <style> or as inline <style> */
#plot .x.axis text, #plot .y.axis text {
    font-size: 16px !important;
    font-family: 'Segoe UI', Arial, sans-serif !important;
}
#plot .x.axis .label, #plot .y.axis .label {
    font-size: 18px !important;
    font-weight: 600 !important;
    font-family: 'Segoe UI', Arial, sans-serif !important;
}
```

### 2. Y-Axis Label — Do NOT use rotated text

function-plot rotates y-axis labels 90° by default — this is hard to read on projected slides. Instead:
- Use a **short** y-axis label (1-2 words max): `yAxis: { label: 'Height' }` not `yAxis: { label: 'Height of the ball in meters' }`
- Or **remove** the y-axis label and put it as a text annotation above the chart: `<div style="text-align:center;font-size:16px;margin-bottom:4px;">Height (meters) vs Time (seconds)</div>`

### 3. Domain — Set explicitly, don't rely on auto

Always set both `xAxis.domain` and `yAxis.domain` to ensure the important features (vertex, intercepts, key points) are visible:

```javascript
// BAD: auto domain might cut off the vertex
functionPlot({ target: '#plot', data: [{ fn: '-(x-2)^2+9' }] });

// GOOD: domain shows the full parabola including vertex
functionPlot({
    target: '#plot',
    width: 800, height: 400,
    xAxis: { domain: [-1, 5], label: 'x' },
    yAxis: { domain: [-2, 11], label: 'y' },
    data: [{ fn: '-(x-2)^2+9', color: '#e74c3c' }]
});
```

### 4. Colors — Use our standard palette

| Purpose | Color |
|---------|-------|
| Primary curve | `#e74c3c` (red) |
| Secondary curve | `#2196f3` (blue) |
| Tertiary curve | `#4caf50` (green) |
| Reference line | `#9e9e9e` (gray) |
| Highlight | `#ff9800` (orange) |

### 5. Grid — Enable for readability

```javascript
functionPlot({
    target: '#plot',
    grid: true,  // Shows grid lines
    // ...
});
```

## Annotations (key points, vertex, intercepts)

**Grid default style**: function-plot's grid lines are `rgb(238,238,238)` (light gray), 1px. Annotation lines use the SAME default style — making them invisible against the grid. Always override annotation color and width so they stand out.

function-plot supports annotations for marking specific points. **Always add a visible data line** alongside annotations, since annotation lines blend into the grid:

```javascript
functionPlot({
    target: '#plot',
    width: 800, height: 400,
    xAxis: { domain: [-1, 5], label: 'Time (s)' },
    yAxis: { domain: [-2, 22], label: 'Height' },
    grid: true,
    data: [
        { fn: '-5*x^2 + 20*x', color: '#e74c3c', graphType: 'polyline' }
    ],
    annotations: [
        { x: 2, text: 'Vertex (2, 20)' },      // Vertical line at x=2 (WARNING: default color is invisible against grid!)
        { y: 20, text: 'Max height = 20m' }     // Horizontal line at y=20 (same warning)
    ]
});
```

**Making annotations visible**: Since annotation lines are invisible against the grid, add a matching data entry as a visible reference line whenever you use an annotation:

```javascript
// BAD: annotation line invisible (same color as grid)
annotations: [{ x: 2, text: 'Axis of symmetry' }]

// GOOD: add a visible dashed data line alongside the annotation
data: [
    { fn: 'main curve...', color: '#e74c3c' },
    { fn: '2', color: '#ff9800', graphType: 'polyline', attr: { 'stroke-dasharray': '5,5', 'stroke-width': 2 } }  // visible vertical reference
],
annotations: [{ x: 2, text: 'Axis of symmetry' }]
```

For vertical reference lines, use a constant x-function: `fn: '2'` won't work (that's y=2). Instead, use the annotation for the line and add a colored data line using `fnType: 'points'` with two points at the same x but different y values:
```javascript
{ points: [[2, -2], [2, 22]], fnType: 'points', graphType: 'polyline', color: '#ff9800' }
```

## Multiple Functions

```javascript
data: [
    { fn: '-x^2 + 4*x + 5', color: '#e74c3c', graphType: 'polyline' },
    { fn: '2*x + 1', color: '#2196f3' },
    { fn: '0', color: '#9e9e9e', skipTip: true }  // x-axis reference
]
```

## Scatter Points (for worked examples)

```javascript
data: [
    { fn: '-5*x^2+20*x', color: '#e74c3c' },
    {
        points: [[0,0], [1,15], [2,20], [3,15], [4,0]],
        fnType: 'points',
        graphType: 'scatter',
        color: '#1565c0'
    }
]
```

## Complete Slide Example (classroom-ready)

```html
<div style="text-align:center;font-size:18px;font-weight:600;color:#37474f;margin-bottom:8px;">
    Basketball Height vs Time: h(t) = -5t² + 20t
</div>
<div id="physics-plot" style="width:800px;height:380px;margin:0 auto;"></div>
<style>
#physics-plot .x.axis text, #physics-plot .y.axis text { font-size: 16px !important; }
#physics-plot .x.axis .label, #physics-plot .y.axis .label { font-size: 18px !important; font-weight: 600 !important; }
</style>
<script>
document.addEventListener('DOMContentLoaded', function() {
    if (typeof functionPlot === 'undefined') return;
    functionPlot({
        target: '#physics-plot',
        width: 800, height: 380,
        grid: true,
        xAxis: { domain: [-0.5, 4.5], label: 'Time (seconds)' },
        yAxis: { domain: [-2, 22], label: 'Height (m)' },
        data: [
            { fn: '-5*x^2 + 20*x', color: '#e74c3c', graphType: 'polyline' },
            { points: [[2, 20]], fnType: 'points', graphType: 'scatter', color: '#1565c0' }
        ],
        annotations: [
            { x: 2, text: 'Peak at t = 2s' }
        ]
    });
});
</script>
```
