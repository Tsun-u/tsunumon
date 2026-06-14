# JSXGraph API Reference (v1.11.1)

All elements are created via `board.create('elementType', params, attributes)`.

## Geometry Elements

| Element | Parameters | Description |
|---------|-----------|-------------|
| `point` | `[x, y]` | Free or fixed point |
| `midpoint` | `[p1, p2]` | Midpoint between two points |
| `line` | `[p1, p2]` | Infinite line through two points |
| `segment` | `[p1, p2]` | Line segment between two points |
| `polygon` | `[p1, p2, p3, ...]` | Closed polygon |
| `circle` | `[center, radius]` or `[center, point]` | Circle |
| `circumcircle` | `[p1, p2, p3]` | Circumscribed circle through three points |
| `circumcenter` | `[p1, p2, p3]` | Center of circumscribed circle |
| `incircle` | `[p1, p2, p3]` | Inscribed circle of triangle |
| `incenter` | `[p1, p2, p3]` | Center of inscribed circle |
| `arc` | `[center, p1, p2]` | Circular arc |
| `sector` | `[center, p1, p2]` | Circular sector |
| `angle` | `[p1, vertex, p2]` | Angle marker |
| `perpendicular` | `[line, point]` | **Infinite line** perpendicular to `line` through `point` (NOT a segment!) |
| `perpendicularpoint` | `[point, line]` | Foot of perpendicular from `point` to `line` (the closest point on line) |
| `perpendicularsegment` | `[line, point]` | Segment from `point` to its foot on `line` |
| `parallel` | `[line, point]` | Line parallel to `line` through `point` |
| `bisector` | `[p1, vertex, p2]` | Angle bisector |
| `tangent` | `[curve, point]` | Tangent line |
| `functiongraph` | `[function]` | Graph of y=f(x) |
| `curve` | `[xFunc, yFunc, tStart, tEnd]` | Parametric curve |
| `text` | `[x, y, 'content']` | Text label — all three values in ONE array: `board.create('text', [x, y, fn], opts)`. Do NOT split into `[x, y], [fn]` (two arrays) — this crashes `board.update()`. |
| `slider` | `[[x1,y1], [x2,y2], [min, val, max]]` | Interactive slider |
| `glider` | `[x, y, element]` | Point constrained to element |
| `reflection` | `[element, line]` | Reflection across line |

## ⚠️ DOES NOT EXIST — Common Mistakes

| ❌ Wrong | ✅ Correct way |
|----------|---------------|
| `perpendicularbisector` | Combine `midpoint` + `perpendicular` (see below) |
| `perpendicularBisector` | Same — does not exist |
| Using `perpendicular` to draw a distance/radius line | `perpendicular` draws an **infinite line**, not a segment. Use `perpendicularsegment` or combine `perpendicularpoint` + `segment` (see below) |

## Perpendicular Bisector (correct pattern)

```javascript
// Perpendicular bisector of segment AB
var mid = board.create('midpoint', [A, B], { visible: false });
var line_AB = board.create('line', [A, B], { visible: false });
board.create('perpendicular', [line_AB, mid], {
    strokeColor: '#764ba2', strokeWidth: 1.5, dash: 2
});
```

## Perpendicular Distance / Inradius (correct pattern)

To draw the perpendicular distance from a point to a line (e.g., inradius from Incenter to a side):

```javascript
// WRONG: board.create('perpendicular', [line, I]) — draws an INFINITE line!
// CORRECT: use perpendicularsegment for a finite segment from point to foot
var lAB = board.create('line', [A, B], { visible: false });
board.create('perpendicularsegment', [lAB, I], {
    strokeColor: '#e74c3c', strokeWidth: 2, dash: 3
});
// Or manually: find foot point, then draw segment
var foot = board.create('perpendicularpoint', [I, lAB], { visible: false });
board.create('segment', [I, foot], { strokeColor: '#e74c3c', strokeWidth: 2, dash: 3 });
```

## Common Attributes

```javascript
// Point
{ name: 'A', size: 5, color: '#1976d2',
  label: { fontSize: 18, fontWeight: 'bold',
    cssStyle: 'text-shadow: -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff' } }

// Circle / Circumcircle
{ strokeColor: '#e65100', strokeWidth: 2.5, dash: 2,
  fillColor: 'rgba(230,81,0,0.05)' }

// Line (visible: false for construction lines)
{ visible: false }

// Polygon
{ fillColor: 'rgba(255,193,7,0.1)', fillOpacity: 0.5,
  borders: { strokeColor: '#1976d2', strokeWidth: 2 } }
```

## Board Setup

```javascript
var board = JXG.JSXGraph.initBoard('container-id', {
    boundingbox: [-2, 10, 10, -2],  // [xmin, ymax, xmax, ymin]
    showNavigation: false,
    showCopyright: false,
    keepaspectratio: true  // ALWAYS use true for geometry
});
```

**IMPORTANT**: `boundingbox` must have enough padding so all elements (especially circumscribed circles) fit without clipping. Add at least 2 units beyond the extreme coordinates.

## Label Spacing

When multiple points are close together, their labels overlap and become unreadable. Use `label.offset` to spread labels apart:

```javascript
board.create('point', [0.87, 0.5], { name: 'A', label: { offset: [10, 10] } });
board.create('point', [0.5, 0.87], { name: 'B', label: { offset: [-15, 10] } });
board.create('point', [0, 1],      { name: 'C', label: { offset: [10, -15] } });
```

Place labels in different directions (top-right, top-left, bottom) relative to their points. For unit circle diagrams with cardinal points (0°, 90°, 180°, 270°), offset labels outward from the circle center.

## Common Syntax Pitfalls

Point coordinates use a **flat** array — do NOT nest:
```javascript
board.create('point', [0, 0], { name: 'O' });        // ✅ correct
board.create('point', [[0, 0]], { name: 'O' });       // ❌ silent error — kills all subsequent elements
```

Text content goes **inside** the coordinate array as the third element:
```javascript
board.create('text', [x, y, 'label'], { fontSize: 16 });              // ✅ correct
board.create('text', [x, y, function(){ return val; }], { });          // ✅ correct (dynamic)
board.create('text', [x, y], 'label', { fontSize: 16 });              // ❌ content outside array
```

When computing coordinates from array variables, **pre-compute into simple variables** first — array indexing like `p[0]` inside `board.create` brackets causes parsing issues:
```javascript
var labelX = p30[0] * 1.28;
var labelY = p30[1] * 1.28;
board.create('text', [labelX, labelY, 'π/6'], { fontSize: 13 });      // ✅ clean
board.create('text', [p30[0]*1.28, p30[1]*1.28, 'π/6'], { });         // ⚠️ works but fragile
board.create('text', [p30[0]*1.28, p30[1]*1.28], 'π/6', { });         // ❌ content outside + nested brackets
```

## Slider Auto-Play Animation

Viewers watch a video — they cannot drag the slider. Every slider MUST have a `requestAnimationFrame` auto-play loop with a separate accumulator variable (do NOT read back from `slider.Value()` — snapWidth rounding creates an infinite loop where the value never advances).

Working example — unit circle point rotating via slider. The auto-play code MUST be inside the same `DOMContentLoaded` callback as the board/slider init (same scope):

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
  if (typeof JXG === 'undefined') return;
  var board = JXG.JSXGraph.initBoard('angle-anim', {
    boundingbox: [-1.7, 1.7, 1.7, -1.7],
    showNavigation: false, showCopyright: false, keepaspectratio: true, axis: false
  });
  board.create('line', [[-1.6, 0], [1.6, 0]], { strokeColor: '#ddd', strokeWidth: 1.5, straightFirst: false, straightLast: false, lastArrow: true });
  board.create('line', [[0, -1.6], [0, 1.6]], { strokeColor: '#ddd', strokeWidth: 1.5, straightFirst: false, straightLast: false, lastArrow: true });
  var center = board.create('point', [0, 0], { name: '', size: 5, color: '#e74c3c', fixed: true, label: { visible: false } });
  board.create('circle', [center, 1], { strokeColor: '#2196f3', strokeWidth: 2.5, fillColor: 'rgba(33,150,243,0.04)' });
  var Pstart = board.create('point', [1, 0], { name: '', size: 0, fixed: true, label: { visible: false } });
  board.create('segment', [center, Pstart], { strokeColor: '#ddd', strokeWidth: 1, dash: 2 });

  // Slider in radians (0 to 2π)
  var angleVal = board.create('slider', [[-1.4, -1.5], [1.4, -1.5], [0, 0, 2 * Math.PI]], {
    name: 'θ', snapWidth: 0.01,
    label: { fontSize: 16, cssStyle: 'font-weight:700;' },
    baseline: { strokeColor: '#ccc' },
    highline: { strokeColor: '#ff9800', strokeWidth: 3 },
    face: 'o', size: 7, fillColor: '#ff9800', strokeColor: '#e65100'
  });

  // Dynamic point driven by slider value
  var Pdyn = board.create('point', [
    function() { return Math.cos(angleVal.Value()); },
    function() { return Math.sin(angleVal.Value()); }
  ], { name: 'P', size: 9, color: '#e74c3c', fixed: false, label: {
    fontSize: 18, fontWeight: 'bold', offset: [10, 4],
    cssStyle: 'color:#e74c3c;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;'
  }});
  board.create('segment', [center, Pdyn], { strokeColor: '#4caf50', strokeWidth: 3 });
  board.create('arc', [center, Pstart, Pdyn], { strokeColor: '#ff9800', strokeWidth: 3, lastArrow: true });

  // Dynamic coordinate label
  board.create('text', [
    function() { return Math.cos(angleVal.Value()) + 0.12; },
    function() { return Math.sin(angleVal.Value()) + 0.18; },
    function() {
      var x = Math.cos(angleVal.Value()).toFixed(2);
      var y = Math.sin(angleVal.Value()).toFixed(2);
      return '(' + x + ', ' + y + ')';
    }
  ], { fontSize: 16, color: '#1565c0',
    cssStyle: 'font-weight:700;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;' });

  // Auto-play loop — REQUIRED for video playback
  // Uses a separate accumulator `t` (not slider.Value()) to avoid snapWidth rounding loop.
  var t = 0;
  var dt = 0.018;
  function animate() {
    t += dt;
    if (t > 2 * Math.PI) t = 0;
    angleVal.setValue(t);
    board.update();
    requestAnimationFrame(animate);
  }
  animate();
});
</script>
```

Key rules:
- Use `requestAnimationFrame` with a separate accumulator variable `t` — never read back from `slider.Value()` (snapWidth rounding traps the value)
- Use radians for angle sliders (0 to 2π), not degrees — avoids coordinate bugs with dependent objects
- Keep `dt` small (0.01–0.02) for smooth animation
- All dependent points/text must use `function()` closures referencing `slider.Value()`
- Without the auto-play loop, the slider stays frozen and the diagram appears static in video

## Label Text Outline

JSXGraph labels are CSS-positioned `<div>` elements. Use `cssStyle` with `text-shadow` for outline:

```javascript
label: {
    fontSize: 18,
    fontWeight: 'bold',
    cssStyle: 'text-shadow: -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff'
}
```
