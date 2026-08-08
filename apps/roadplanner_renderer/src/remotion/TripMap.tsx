/**
 * The travel map: one projection for the whole journey, and a camera.
 *
 * This is a *reduced* travel map on purpose. Land, water, national
 * borders, the route, a few names, the camper - and nothing else. A
 * navigation map is a tool for deciding where to turn; a travel map in a
 * film is a picture of a journey, and every road and point of interest
 * that is not the journey is noise on top of it.
 *
 * The architecture, and why it changed
 * ------------------------------------
 *
 * The first version fitted a fresh projection to each day and cached the
 * result. That made every day its own island: the camera could not move
 * between the whole-trip view and today's leg, because the two were
 * different coordinate systems. It also meant a zoom would have cost a
 * re-projection of Natural Earth on every frame - which is precisely the
 * thing that once ran the CI render past its time limit.
 *
 * Now the trip is projected **once**, at whole-journey scale, and the
 * camera is an SVG transform over that single picture. Panning and
 * zooming are then free: the browser is scaling a path it already has.
 * Overview and detail are the same map at two magnifications, which is
 * what lets the film glide between them instead of cutting.
 *
 * Two consequences are handled explicitly. Stroke widths would grow with
 * the zoom, so every stroke is non-scaling. Text would grow too, so
 * labels are placed outside the transform and positioned by applying the
 * camera by hand.
 *
 * Where the geography comes from
 * ------------------------------
 *
 * Natural Earth, shipped inside the image (`world-atlas`, public
 * domain). No tile server, no API key, no request while a job runs.
 * Country names come from that same file, so a name on screen is read
 * rather than invented.
 *
 * The route is not geography at all: it arrives in the render package,
 * built from the routing Roadplanner already stored. Nothing here
 * computes a route, snaps a point to a road or guesses a crossing.
 */
import React from "react";
import { geoMercator, geoPath, geoContains } from "d3-geo";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection } from "topojson-specification";
import world from "world-atlas/countries-50m.json";
import coarse from "world-atlas/countries-110m.json";

import { Camper } from "./CharacterAssets";
import {
  bearingAtDistance,
  bearingWindow,
  damped,
  easeTravel,
  pointAtDistance,
  prepareRoute,
  routeUpTo,
} from "../movement.mjs";

export type MapPoint = [number, number];

export type MapSegment = { mode: string; points: MapPoint[] };

export type MapChapter = {
  chapterId: string;
  index: number;
  segments: MapSegment[];
  start: MapPoint;
  end: MapPoint;
  bbox: [number, number, number, number];
  hasFerry: boolean;
  estimated: boolean;
};

export type MapContext = {
  bbox: [number, number, number, number];
  start: MapPoint;
  end: MapPoint;
  hasFerry: boolean;
  pointCount: number;
  chapters: MapChapter[];
};

// The palette. Muted on purpose: the current day's route and the camper
// are the only two things on this map allowed to be bright.
const WATER = "#0e1723";
const LAND = "#33404f";
const BORDER = "#5b6d81";
const ROUTE_DONE = "#6d7b8d";
const ROUTE_LIVE = "#e8823f";
const FERRY = "#5fb3c4";
const LABEL = "#cfdae6";
const COUNTRY_LABEL = "#8296ab";

const worldTopology = world as unknown as {
  objects: { land: GeometryCollection; countries: GeometryCollection };
};
// Computed once for the process, not once per scene: the outline of the
// world is the same in every frame of every film.
const landFeature = feature(worldTopology as never, worldTopology.objects.land as never);
/**
 * Borders come from the coarse atlas, coastlines from the fine one.
 *
 * Measured, because the guess was wrong twice. Rendering the closing
 * scene cost 689 ms per frame; blanking the border mesh alone took it to
 * 201 ms. So national borders were 71 % of the cost of drawing a map -
 * a stroked mesh has to be re-rasterised in device space every time the
 * camera moves, and the camera now moves in most frames rather than in
 * a few.
 *
 * The first fix attempt made it worse: replacing `non-scaling-stroke`
 * with a stroke width divided by the zoom went to 1183 ms. It is the
 * volume of geometry, not the way it is stroked.
 *
 * So the geometry shrinks - but only where it can be afforded visually.
 * A coastline is what the eye reads on a travel map and stays at the
 * fine resolution; a national border is a thin grey line saying "another
 * country starts here", and at the scale a trip is drawn the coarse
 * version is indistinguishable. That took the closing scene to 253 ms.
 */
const coarseTopology = coarse as unknown as typeof worldTopology;
const borderMesh = mesh(
  coarseTopology as never,
  coarseTopology.objects.countries as never,
  (a: unknown, b: unknown) => a !== b,
);
const countryFeatures = feature(
  worldTopology as never,
  worldTopology.objects.countries as never,
) as unknown as { features: { properties?: { name?: string } }[] };

/**
 * German names for the countries a European trip crosses.
 *
 * The map file carries English names. Showing "Sweden" in a German film
 * would be a small wrongness in the one place the map speaks, so the
 * handful that matter are translated and anything unlisted falls back to
 * the name as it is stored - never to a guess.
 */
const COUNTRY_NAMES: Record<string, string> = {
  Germany: "Deutschland",
  Denmark: "Dänemark",
  Sweden: "Schweden",
  Norway: "Norwegen",
  Finland: "Finnland",
  Poland: "Polen",
  Estonia: "Estland",
  Latvia: "Lettland",
  Lithuania: "Litauen",
  Netherlands: "Niederlande",
  Belgium: "Belgien",
  France: "Frankreich",
  Austria: "Österreich",
  Switzerland: "Schweiz",
  Italy: "Italien",
  Spain: "Spanien",
  Portugal: "Portugal",
  "Czech Republic": "Tschechien",
  Czechia: "Tschechien",
  Slovakia: "Slowakei",
  Hungary: "Ungarn",
  Slovenia: "Slowenien",
  Croatia: "Kroatien",
  Russia: "Russland",
  Belarus: "Belarus",
  Ukraine: "Ukraine",
  Ireland: "Irland",
  "United Kingdom": "Vereinigtes Königreich",
  Iceland: "Island",
  Luxembourg: "Luxemburg",
  Romania: "Rumänien",
  Bulgaria: "Bulgarien",
  Greece: "Griechenland",
};

/** Which country a place is in, read from the map rather than guessed. */
export const countryAt = (point: MapPoint): string => {
  for (const candidate of countryFeatures.features) {
    if (geoContains(candidate as never, point)) {
      const name = String(candidate.properties?.name || "");
      return COUNTRY_NAMES[name] || name;
    }
  }
  return "";
};

export type Projection = {
  width: number;
  height: number;
  project: (point: MapPoint) => [number, number];
  landPath: string;
  borderPath: string;
};

const MIN_SPAN_DEGREES = 0.55;

const padBbox = (
  bbox: [number, number, number, number],
  pad: number,
): [number, number, number, number] => {
  const [west, south, east, north] = bbox;
  const spanX = Math.max(east - west, MIN_SPAN_DEGREES);
  const spanY = Math.max(north - south, MIN_SPAN_DEGREES);
  const midX = (west + east) / 2;
  const midY = (south + north) / 2;
  return [
    midX - (spanX * (1 + pad)) / 2,
    midY - (spanY * (1 + pad)) / 2,
    midX + (spanX * (1 + pad)) / 2,
    midY + (spanY * (1 + pad)) / 2,
  ];
};

/**
 * Projections already built, kept for the process rather than a component.
 *
 * There is normally exactly one per film. The cache exists because
 * Remotion renders frames across several browser tabs that each seek to
 * arbitrary frames, so a React memo almost never gets to reuse its own
 * previous result - and without this the world outline was rebuilt for
 * every one of four thousand frames, which ran the CI render past its
 * time limit.
 */
const PROJECTION_CACHE = new Map<string, Projection>();

/**
 * The one projection a film uses, fitted to the whole journey.
 *
 * Mercator, because a travel map is read the way a road atlas is and
 * north stays up. The distortion that makes Mercator wrong for a world
 * map is irrelevant across a few hundred kilometres.
 */
export const buildProjection = (
  bbox: [number, number, number, number],
  width: number,
  height: number,
  pad = 0.35,
): Projection => {
  const key = [...bbox.map((value) => value.toFixed(4)), width, height, pad].join("|");
  const cached = PROJECTION_CACHE.get(key);
  if (cached) return cached;
  const [west, south, east, north] = padBbox(bbox, pad);
  // The corners as points, NOT as a polygon. d3-geo reads a polygon
  // spherically, where the ring's winding order decides which side is
  // "inside" - and a ring wound the wrong way means the whole planet
  // except the box. That happened here first: every map came out as a
  // view of the entire world with a camper on Germany.
  const extent = {
    type: "MultiPoint" as const,
    coordinates: [
      [west, south],
      [east, south],
      [east, north],
      [west, north],
    ],
  };
  const projection = geoMercator().fitExtent(
    [
      [0, 0],
      [width, height],
    ],
    extent,
  );
  // Clipped around the trip view: without this the land path covers the
  // planet, and rasterising a planet-sized path is work per frame.
  //
  // Half the frame is not a taste: every camera in the film is at least
  // as magnified as the overview, so each one shows a *subset* of this
  // frame, displaced by at most half of it. Anything beyond that margin
  // can never come into view. It used to be a whole frame's width, which
  // clipped an area four times larger than the widest camera can reach.
  const margin = Math.round(Math.max(width, height) * 0.5);
  const path = geoPath(
    projection.clipExtent([
      [-margin, -margin],
      [width + margin, height + margin],
    ]),
  );
  const built: Projection = {
    width,
    height,
    project: (point: MapPoint) => {
      const projected = projection(point);
      return projected ? [projected[0], projected[1]] : [-99999, -99999];
    },
    landPath: path(landFeature as never) ?? "",
    borderPath: path(borderMesh as never) ?? "",
  };
  if (PROJECTION_CACHE.size >= 24) {
    PROJECTION_CACHE.delete(PROJECTION_CACHE.keys().next().value as string);
  }
  PROJECTION_CACHE.set(key, built);
  return built;
};

/**
 * Where the camera is, and how it moves.
 *
 * The arithmetic lives in `../camera.mjs` so that a test can drive it
 * without a browser; it is re-exported here because the map is where
 * every caller already looks for it.
 */
export type { Camera } from "../camera.mjs";
import { cameraTransform, onScreen } from "../camera.mjs";
import type { Camera } from "../camera.mjs";

export { CAPTION_STRIP, blendCamera, cameraFor, onScreen } from "../camera.mjs";


const polyline = (projection: Projection, points: MapPoint[]): string => {
  if (points.length < 2) return "";
  return points
    .map((point, index) => {
      const [x, y] = projection.project(point);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
};

/** Every point of a chapter in travel order, with the mode it belongs to. */
export const flatten = (chapter: MapChapter): { point: MapPoint; mode: string }[] => {
  const out: { point: MapPoint; mode: string }[] = [];
  chapter.segments.forEach((segment) => {
    segment.points.forEach((point) => out.push({ point, mode: segment.mode }));
  });
  return out;
};

/**
 * Where the camper is on a chapter, and which way it faces.
 *
 * The distance work lives in `movement.mjs`, which is plain JavaScript so
 * that the tests can drive it without a browser or a bundler. This is the
 * thin layer that turns "how far through the day are we" into something
 * to draw.
 */
export const driveState = (chapter: MapChapter, fraction: number, frames: number) => {
  const flat = flatten(chapter);
  const route = prepareRoute(flat.map((entry) => entry.point));
  const window = bearingWindow(route, frames);
  const distanceAt = (f: number) => route.total * easeTravel(Math.max(0, Math.min(frames, f)) / frames);
  const bearingAt = (f: number) => bearingAtDistance(route, distanceAt(f), window);
  const frame = Math.round(Math.max(0, Math.min(1, fraction)) * frames);
  const distance = distanceAt(frame);
  const head = pointAtDistance(route, distance);
  // Damped over frames rather than accumulated, so a renderer drawing
  // frames out of order and in parallel still gets the same answer.
  const heading = damped(bearingAt, frame, 12);
  const drawn = routeUpTo(route, distance);
  // The drawn line has to be split back into runs of one mode, so a ferry
  // inside a day keeps its own style instead of being averaged away.
  const runs: { mode: string; points: MapPoint[] }[] = [];
  for (let index = 0; index < drawn.length; index += 1) {
    const mode = flat[Math.min(index, flat.length - 1)]?.mode || "driving";
    const last = runs[runs.length - 1];
    if (last && last.mode === mode) last.points.push(drawn[index] as MapPoint);
    else runs.push({ mode, points: [drawn[index] as MapPoint] });
  }
  return { position: head.position as MapPoint, heading, runs, route };
};

export type MapLabel = { text: string; point: MapPoint; kind?: "place" | "country" };

export type TripMapProps = {
  projection: Projection;
  camera: Camera;
  /** Chapters already finished. Drawn quiet and complete. */
  past: MapChapter[];
  /** The route being driven right now, already cut to the camper. */
  live?: { mode: string; points: MapPoint[] }[];
  labels?: MapLabel[];
  camper?: { position: MapPoint; heading: number; scale?: number } | null;
  markPoint?: MapPoint | null;
  pastStroke?: string;
  children?: React.ReactNode;
};

export const TripMap: React.FC<TripMapProps> = ({
  projection,
  camera,
  past,
  live = [],
  labels = [],
  camper = null,
  markPoint = null,
  pastStroke = ROUTE_DONE,
  children,
}) => {
  const transform = cameraTransform(projection, camera);
  return (
    <svg
      width={projection.width}
      height={projection.height}
      viewBox={`0 0 ${projection.width} ${projection.height}`}
      style={{ display: "block", backgroundColor: WATER }}
    >
      <g transform={transform}>
        <path d={projection.landPath} fill={LAND} stroke="none" />
        <path
          d={projection.borderPath}
          fill="none"
          stroke={BORDER}
          strokeWidth={1.4}
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          opacity={0.85}
        />
        {past.map((chapter) =>
          chapter.segments.map((segment, position) => (
            <path
              key={`${chapter.chapterId}-${position}`}
              d={polyline(projection, segment.points)}
              fill="none"
              stroke={segment.mode === "ferry" ? FERRY : pastStroke}
              strokeWidth={segment.mode === "ferry" ? 2.2 : 3}
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
              strokeDasharray={
                segment.mode === "ferry" ? "9 7" : segment.mode === "direct" ? "3 8" : undefined
              }
              opacity={pastStroke === ROUTE_DONE ? 0.7 : 0.95}
            />
          )),
        )}
        {live.map((run, position) => (
          <path
            key={`live-${position}`}
            d={polyline(projection, run.points)}
            fill="none"
            stroke={run.mode === "ferry" ? FERRY : ROUTE_LIVE}
            strokeWidth={run.mode === "ferry" ? 3 : 4.4}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
            strokeDasharray={
              run.mode === "ferry" ? "10 8" : run.mode === "direct" ? "3 9" : undefined
            }
          />
        ))}
      </g>
      {/* Outside the transform: text and the vehicle keep their size at
          every magnification, so a zoom changes the map and not the
          typography. */}
      {markPoint ? <Mark projection={projection} camera={camera} point={markPoint} /> : null}
      {labels.map((label) => {
        const [x, y] = onScreen(projection, camera, projection.project(label.point));
        if (x < -200 || y < -200 || x > projection.width + 200 || y > projection.height + 200) {
          return null;
        }
        return label.kind === "country" ? (
          <text
            key={`${label.text}-country`}
            x={x}
            y={y}
            fill={COUNTRY_LABEL}
            fontSize={26}
            fontFamily="sans-serif"
            letterSpacing={6}
            textAnchor="middle"
            opacity={0.75}
          >
            {label.text.toUpperCase()}
          </text>
        ) : (
          <g key={`${label.text}-${Math.round(x)}`}>
            <circle cx={x} cy={y} r={3.6} fill={LABEL} />
            <text
              x={x + 10}
              y={y + 5}
              fill={LABEL}
              fontSize={19}
              fontFamily="sans-serif"
              stroke={WATER}
              strokeWidth={4}
              paintOrder="stroke"
            >
              {label.text}
            </text>
          </g>
        );
      })}
      {camper ? (
        <Camper
          {...(() => {
            const [x, y] = onScreen(projection, camera, projection.project(camper.position));
            return { x, y };
          })()}
          heading={camper.heading}
          scale={camper.scale ?? 1}
        />
      ) : null}
      {children}
    </svg>
  );
};

const Mark: React.FC<{ projection: Projection; camera: Camera; point: MapPoint }> = ({
  projection,
  camera,
  point,
}) => {
  const [x, y] = onScreen(projection, camera, projection.project(point));
  return (
    <g>
      <circle cx={x} cy={y} r={13} fill="none" stroke={ROUTE_LIVE} strokeWidth={2} opacity={0.6} />
      <circle cx={x} cy={y} r={4} fill={ROUTE_LIVE} />
    </g>
  );
};

export { Camper };
