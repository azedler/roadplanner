/**
 * The camper's motion, checked as motion rather than as code shape.
 *
 * The map juddered, and the three causes were all in how movement was
 * computed: arc length in degrees instead of metres, a heading looked up
 * over vertex offsets instead of a distance, and a drawn route that ran
 * one vertex ahead of the vehicle and doubled back. None of those is
 * visible in a snapshot of a single frame - they only show up as a
 * relationship *between* frames, which is what these checks measure.
 *
 * The test route is deliberately nasty in exactly the ways real routing
 * data is: vertices bunched in the bends and hundreds of kilometres
 * apart on the straights, and a right-angle turn from an eastward leg to
 * a northward one, which is where degrees-as-metres did its worst.
 */
import assert from "node:assert/strict";

import {
  MODE_PACE,
  arcLengths,
  bearingAtDistance,
  bearingWindow,
  damped,
  distanceAtFraction,
  easeTravel,
  metres,
  pointAtDistance,
  prepareRoute,
  routeUpTo,
} from "../apps/roadplanner_renderer/src/movement.mjs";

// --- a route with the pathologies real data has ------------------------

const dense = [];
for (let step = 0; step <= 40; step += 1) {
  // A tight bend: forty vertices inside a few hundred metres.
  dense.push([13.0 + step * 0.0002, 56.0 + Math.sin(step / 6) * 0.0009]);
}
const clampFrame = (frame) => Math.max(0, frame);
// The composition damps the heading over the same number of frames; the
// constant lives in one place rather than being two different numbers.
const BEARING_DAMPING = 12;

const ROUTE = prepareRoute([
  ...dense,
  [13.5, 56.0],   // a long eastward straight kept by two points
  [14.2, 56.0],
  [14.2, 57.4],   // then a right angle, and a long northward one
  [14.2, 58.6],
]);

// --- metres, not degrees -----------------------------------------------

{
  // The bug, stated as a measurement: a degree of longitude at 56°N is
  // much shorter than a degree of latitude, and the old code counted
  // them the same.
  const east = metres([14.0, 56.0], [14.1, 56.0]);
  const north = metres([14.0, 56.0], [14.0, 56.1]);
  assert.ok(north > east * 1.7, `Norden ${north} muss deutlich weiter sein als Osten ${east}`);
  // Roughly right in absolute terms, not merely self-consistent.
  assert.ok(Math.abs(north - 11_132) < 60, `1/10 Grad Breite sind ~11.1 km, gemessen ${north}`);
}

// --- the camper stays on the route -------------------------------------

const distanceToRoute = (position) => {
  let best = Infinity;
  for (let index = 1; index < ROUTE.points.length; index += 1) {
    const a = ROUTE.points[index - 1];
    const b = ROUTE.points[index];
    // Distance from the point to this segment, in metres.
    const ab = metres(a, b);
    if (ab === 0) continue;
    const lat = (a[1] * Math.PI) / 180;
    const ax = 0;
    const ay = 0;
    const bx = (b[0] - a[0]) * Math.cos(lat) * 111_320;
    const by = (b[1] - a[1]) * 111_320;
    const px = (position[0] - a[0]) * Math.cos(lat) * 111_320;
    const py = (position[1] - a[1]) * 111_320;
    const t = Math.max(0, Math.min(1, ((px - ax) * bx + (py - ay) * by) / (bx * bx + by * by)));
    best = Math.min(best, Math.hypot(px - bx * t, py - by * t));
  }
  return best;
};

for (let frame = 0; frame <= 200; frame += 1) {
  const distance = ROUTE.total * easeTravel(frame / 200);
  const { position } = pointAtDistance(ROUTE, distance);
  assert.ok(
    distanceToRoute(position) < 1,
    `Bild ${frame}: Camper ${distanceToRoute(position).toFixed(1)} m neben der Route`,
  );
}

// --- the movement is steady, and vertices are not felt ------------------

{
  // A dead straight line whose vertices are spaced absurdly unevenly:
  // twenty metres apart, then two hundred kilometres. On a straight line
  // the chord between two positions IS the ground covered, so this
  // measures speed directly - and vertex-based movement would show up
  // instantly as the camper crawling through the crowded part and
  // leaping across the sparse one.
  const uneven = prepareRoute([
    [20.0, 60.0],
    [20.0004, 60.0],
    [20.0008, 60.0],
    [20.0012, 60.0],
    [24.0, 60.0],
    [28.0, 60.0],
    [28.0004, 60.0],
    [32.0, 60.0],
  ]);
  const frames = 400;
  const steps = [];
  let previous = pointAtDistance(uneven, 0).position;
  for (let frame = 1; frame <= frames; frame += 1) {
    const position = pointAtDistance(uneven, uneven.total * (frame / frames)).position;
    steps.push(metres(previous, position));
    previous = position;
  }
  const min = Math.min(...steps);
  const max = Math.max(...steps);
  assert.ok(max / min < 1.01, `Geschwindigkeit schwankt um Faktor ${(max / min).toFixed(3)}`);
}

{
  // The general invariant, on the nasty route: whatever distance is
  // asked for, exactly that much route has been travelled. This is the
  // one the old degree-based arc length could not satisfy - it would be
  // out by up to a factor of two depending on the direction of travel.
  for (const fraction of [0.05, 0.2, 0.37, 0.5, 0.66, 0.81, 0.95, 1]) {
    const distance = ROUTE.total * fraction;
    const travelled = arcLengths(routeUpTo(ROUTE, distance)).pop();
    assert.ok(
      Math.abs(travelled - distance) < 0.5,
      `bei ${fraction}: ${travelled.toFixed(1)} m gefahren, ${distance.toFixed(1)} m verlangt`,
    );
  }
}

// --- the ends ease, and the destination is exact ------------------------

{
  assert.equal(easeTravel(0), 0);
  assert.equal(easeTravel(1), 1);
  const first = easeTravel(1 / 200) - easeTravel(0);
  const middle = easeTravel(0.51) - easeTravel(0.5);
  assert.ok(first < middle / 10, "der Start muss sanft sein");
  const last = easeTravel(1) - easeTravel(199 / 200);
  assert.ok(last < middle / 10, "das Ziel muss sanft erreicht werden");

  // Exactly the day's endpoint, not near it.
  const end = pointAtDistance(ROUTE, ROUTE.total * easeTravel(1)).position;
  const target = ROUTE.points[ROUTE.points.length - 1];
  assert.ok(metres(end, target) < 0.01, `Endpunkt verfehlt um ${metres(end, target)} m`);
}

// --- the drawn route ends where the camper is ---------------------------

for (const fraction of [0, 0.01, 0.17, 0.5, 0.83, 0.999, 1]) {
  const distance = ROUTE.total * fraction;
  const drawn = routeUpTo(ROUTE, distance);
  const head = pointAtDistance(ROUTE, distance).position;
  const last = drawn[drawn.length - 1];
  assert.deepEqual(last, head, `bei ${fraction}: die Linie endet nicht am Camper`);
  // And never runs past him: this is the defect that made the line spring
  // forward to the next vertex and come back, once per vertex.
  const drawnLength = arcLengths(drawn).pop();
  assert.ok(
    drawnLength <= distance + 0.01,
    `bei ${fraction}: gezeichnete Länge ${drawnLength} > gefahrene ${distance}`,
  );
}

// --- the heading is smooth, including through the bend ------------------

{
  // The window is set from how far the camper travels per frame, exactly
  // as the composition sets it, and the result is damped over a handful
  // of frames the same way. Anything else would be testing a different
  // program than the one that renders.
  const frames = 240;
  const window = bearingWindow(ROUTE, frames);
  const bearingAt = (frame) =>
    bearingAtDistance(ROUTE, ROUTE.total * easeTravel(clampFrame(frame) / frames), window);
  const smoothed = (frame) => damped(bearingAt, clampFrame(frame), BEARING_DAMPING);

  let previous = smoothed(0);
  let worst = 0;
  let reversals = 0;
  let lastDelta = 0;
  for (let frame = 1; frame <= frames; frame += 1) {
    const bearing = smoothed(frame);
    let delta = bearing - previous;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    worst = Math.max(worst, Math.abs(delta));
    // Jitter is not "it turned", it is "it turned back and forth". A
    // reversal bigger than a degree is the thing the brief calls zittern.
    if (Math.abs(delta) > 1 && Math.abs(lastDelta) > 1 && Math.sign(delta) !== Math.sign(lastDelta)) {
      reversals += 1;
    }
    lastDelta = delta;
    previous = bearing;
  }
  // A right angle taken at forty-five kilometres a second cannot be
  // gentle, but it must arrive over several frames rather than in one.
  // A true right angle in one vertex, driven at forty-five kilometres a
  // second, cannot be gentle - and real routing data never contains one,
  // because a road that turns has vertices in the turn. What matters is
  // that it arrives over several frames instead of a single one: this is
  // 90° in about a third of a second, down from 55° in one frame.
  assert.ok(worst < 12, `Richtung springt um ${worst.toFixed(1)}° in einem Bild`);
  // The route has exactly one real change of direction plus a bend at the
  // start; anything beyond a couple of reversals is the camper wobbling.
  assert.ok(reversals <= 3, `Richtung wechselt ${reversals} mal die Drehrichtung`);
}

// --- the same plan renders the same motion ------------------------------

{
  const once = [];
  const twice = [];
  for (let frame = 0; frame <= 120; frame += 1) {
    const distance = prepareRoute(ROUTE.points).total * easeTravel(frame / 120);
    once.push(pointAtDistance(prepareRoute(ROUTE.points), distance).position);
    twice.push(pointAtDistance(prepareRoute(ROUTE.points), distance).position);
  }
  assert.deepEqual(once, twice, "derselbe Plan muss dieselbe Bewegung ergeben");
}

// --- degenerate input is survivable -------------------------------------

{
  const single = prepareRoute([[10, 55]]);
  assert.deepEqual(pointAtDistance(single, 500).position, [10, 55]);
  assert.equal(bearingAtDistance(single, 500), 0);
  const repeated = prepareRoute([[10, 55], [10, 55], [10, 55]]);
  assert.equal(repeated.points.length, 1, "wiederholte Koordinaten sind kein Weg");
  assert.deepEqual(prepareRoute([]).points, []);
}

// --- a crossing is slower than a road ------------------------------------

{
  // A day of two equal halves: drive east, then take a ferry the same
  // distance. Spread over metres they would take the same screen time,
  // which is what made the camper shoot across the Baltic and then crawl
  // to the campsite.
  const half = [];
  for (let step = 0; step <= 20; step += 1) half.push([12 + step * 0.02, 55]);
  const second = [];
  for (let step = 1; step <= 20; step += 1) second.push([12.4 + step * 0.02, 55]);
  const points = [...half, ...second];
  const modes = points.map((_, index) => (index >= half.length ? "ferry" : "driving"));
  const paced = prepareRoute(points, modes);
  const plain = prepareRoute(points);

  // Halfway through the scene the paced camper has not yet reached the
  // quay, while the unpaced one is exactly at it.
  const midPaced = distanceAtFraction(paced, 0.5);
  const midPlain = distanceAtFraction(plain, 0.5);
  assert.ok(
    Math.abs(midPlain - plain.total / 2) < 1,
    "ohne Faehre muss die Haelfte der Zeit die Haelfte des Weges sein",
  );
  // More than half the *distance* by half the *scene*: the fast half is
  // over quickly and the crossing is what the rest of the time is spent
  // on. Reading this the other way round is easy, and wrong.
  assert.ok(
    midPaced > paced.total * 0.55,
    `die Faehre kostet keine Zeit - bei halber Szene erst ${(midPaced / paced.total).toFixed(3)}`,
  );

  // And the share of the scene the crossing gets is the pace ratio, not
  // a number somebody liked: half the metres at 2.2x cost is 2.2/3.2.
  let crossingStart = 0;
  for (let f = 0; f <= 1000; f += 1) {
    if (distanceAtFraction(paced, f / 1000) >= plain.total / 2) {
      crossingStart = f / 1000;
      break;
    }
  }
  const expected = 1 / (1 + MODE_PACE.ferry);
  assert.ok(
    Math.abs(crossingStart - expected) < 0.02,
    `die Fahrt endet bei ${crossingStart.toFixed(3)}, erwartet ${expected.toFixed(3)}`,
  );

  // Both ends still land exactly, which is what the pacing must not break.
  assert.ok(Math.abs(distanceAtFraction(paced, 0)) < 1e-9);
  assert.ok(Math.abs(distanceAtFraction(paced, 1) - paced.total) < 1e-6);
  // And progress never goes backwards.
  let previous = -1;
  for (let f = 0; f <= 200; f += 1) {
    const here = distanceAtFraction(paced, f / 200);
    assert.ok(here >= previous - 1e-9, `Fortschritt springt bei ${f} zurueck`);
    previous = here;
  }
  // A route with no modes at all behaves exactly as it did before.
  assert.ok(Math.abs(distanceAtFraction(plain, 0.25) - plain.total * 0.25) < 1);
}

console.log("Camper movement tests passed.");
