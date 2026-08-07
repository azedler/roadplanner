/**
 * The travel map: where the camper is, and how far it has come.
 *
 * This is a *reduced* map on purpose. Land, water, national borders, the
 * route, a couple of place names and the vehicle - and nothing else. A
 * navigation map is a tool for deciding where to turn; a travel map in a
 * film is a picture of a journey, and every road, label and point of
 * interest that is not the journey is noise on top of it.
 *
 * Where the geography comes from
 * ------------------------------
 *
 * The coastlines and borders are Natural Earth, shipped inside the image
 * as a TopoJSON file (`world-atlas`, public domain). No tile server, no
 * API key, no request while a job runs - which is what makes a render
 * reproducible and free.
 *
 * The route is not geography at all: it arrives in the render package,
 * built from the same routing data Roadplanner already stored. Nothing
 * here computes a route, snaps a point to a road or guesses a crossing.
 * A ferry is drawn as a ferry because the roadbook recorded it as one.
 *
 * Why the camera does not move within a scene
 * -------------------------------------------
 *
 * Projecting a world outline is the expensive part, and re-projecting it
 * on every one of thirty frames a second would multiply the cost of the
 * film by the size of the map. So a scene picks its view once - close for
 * a short leg, wide for a long one - and animates only the things that
 * are cheap: the growing route, the camper, and a slow drift applied as a
 * transform. The result looks like a camera move and costs almost
 * nothing.
 */
import React from "react";
import { geoMercator, geoPath } from "d3-geo";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection } from "topojson-specification";
import world from "world-atlas/countries-50m.json";

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

// The palette. Muted on purpose: the route and the camper are the only
// two things on this map that are allowed to be bright.
const WATER = "#0e1723";
const LAND = "#33404f";
const BORDER = "#4b5b6e";
const ROUTE_DONE = "#7f8ea3";
const ROUTE_LIVE = "#e07a3f";
const FERRY = "#5fb3c4";
const LABEL = "#c6d2df";

const worldTopology = world as unknown as {
  objects: { land: GeometryCollection; countries: GeometryCollection };
};
// Computed once for the whole process, not once per scene: the outline of
// the world is the same in every frame of every film.
const landFeature = feature(worldTopology as never, worldTopology.objects.land as never);
const borderMesh = mesh(
  worldTopology as never,
  worldTopology.objects.countries as never,
  (a: unknown, b: unknown) => a !== b,
);

/** A view that never zooms closer than a place you could drive across. */
const MIN_SPAN_DEGREES = 0.55;

export type MapView = {
  width: number;
  height: number;
  project: (point: MapPoint) => [number, number];
  landPath: string;
  borderPath: string;
};

const padBbox = (
  bbox: [number, number, number, number],
  pad: number,
): [number, number, number, number] => {
  const [west, south, east, north] = bbox;
  const spanX = Math.max(east - west, MIN_SPAN_DEGREES);
  const spanY = Math.max(north - south, MIN_SPAN_DEGREES);
  const midX = (west + east) / 2;
  const midY = (south + north) / 2;
  const growX = (spanX * (1 + pad)) / 2;
  const growY = (spanY * (1 + pad)) / 2;
  return [midX - growX, midY - growY, midX + growX, midY + growY];
};

/**
 * Build the view for one scene.
 *
 * Mercator, because a travel map is read the way a road atlas is read and
 * north stays up. The distortion that makes Mercator wrong for a world
 * map is irrelevant across a few hundred kilometres.
 */
export const buildView = (
  bbox: [number, number, number, number],
  width: number,
  height: number,
  pad = 0.55,
): MapView => {
  const [west, south, east, north] = padBbox(bbox, pad);
  // The corners as points, NOT as a polygon. d3-geo reads a polygon
  // spherically, where the ring's winding order decides which side is
  // "inside" - and a ring wound the wrong way means the whole planet
  // except the box. That is exactly what happened here first: every map
  // came out as a view of the entire world with a camper on Germany. Four
  // points have no winding and cannot be turned inside out.
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
  const path = geoPath(projection);
  return {
    width,
    height,
    project: (point: MapPoint) => {
      const projected = projection(point);
      return projected ? [projected[0], projected[1]] : [-9999, -9999];
    },
    landPath: path(landFeature as never) ?? "",
    borderPath: path(borderMesh as never) ?? "",
  };
};

const polyline = (view: MapView, points: MapPoint[]): string => {
  if (points.length < 2) return "";
  return points
    .map((point, index) => {
      const [x, y] = view.project(point);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
};

/** Every point of a chapter in travel order, with the mode it belongs to. */
const flatten = (chapter: MapChapter): { point: MapPoint; mode: string }[] => {
  const out: { point: MapPoint; mode: string }[] = [];
  chapter.segments.forEach((segment) => {
    segment.points.forEach((point) => out.push({ point, mode: segment.mode }));
  });
  return out;
};

/**
 * Where the camper is when a leg is `fraction` done, and which way it
 * faces.
 *
 * The heading is taken over a window rather than between two neighbouring
 * points. Route geometry has hairpins and roundabouts in it, and a camper
 * that faithfully followed every one of them would spin on the spot -
 * true to the data and unwatchable.
 */
export const travelled = (
  points: MapPoint[],
  fraction: number,
): { index: number; position: MapPoint; heading: number } => {
  if (points.length < 2) {
    return { index: 0, position: points[0] ?? [0, 0], heading: 0 };
  }
  const clamped = Math.max(0, Math.min(1, fraction));
  const lengths: number[] = [0];
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i][0] - points[i - 1][0];
    const dy = points[i][1] - points[i - 1][1];
    total += Math.hypot(dx, dy);
    lengths.push(total);
  }
  if (total <= 0) {
    return { index: points.length - 1, position: points[points.length - 1], heading: 0 };
  }
  const target = total * clamped;
  let index = 1;
  while (index < points.length - 1 && lengths[index] < target) index += 1;
  const span = lengths[index] - lengths[index - 1] || 1;
  const t = Math.max(0, Math.min(1, (target - lengths[index - 1]) / span));
  const position: MapPoint = [
    points[index - 1][0] + (points[index][0] - points[index - 1][0]) * t,
    points[index - 1][1] + (points[index][1] - points[index - 1][1]) * t,
  ];
  // Smoothing window: look back and ahead a few points, so the heading is
  // the direction of travel rather than the tangent of a slip road.
  const back = points[Math.max(0, index - 4)];
  const ahead = points[Math.min(points.length - 1, index + 3)];
  const heading =
    (Math.atan2(ahead[1] - back[1], ahead[0] - back[0]) * 180) / Math.PI;
  return { index, position, heading };
};

/** The camper. Drawn, not photographed - eight shapes and a wheelbase. */
export const Camper: React.FC<{
  x: number;
  y: number;
  heading: number;
  scale?: number;
}> = ({ x, y, heading, scale = 1 }) => {
  // Mercator y grows downward and the bearing was computed in map space,
  // so the screen angle is the negative of it.
  const screen = -heading;
  // The camper is drawn from the side, and a side view rotated to follow
  // a heading points straight up when the road goes north - which reads
  // as a van standing on its bumper. So it never turns: it faces the way
  // it is travelling, and only leans with the slope.
  const westward = Math.abs(screen) > 90;
  const slope = westward ? (screen > 0 ? 180 - screen : -180 - screen) : screen;
  const lean = Math.max(-14, Math.min(14, slope));
  return (
    <g transform={`translate(${x} ${y}) rotate(${lean}) scale(${scale})`}>
      <ellipse cx="0" cy="9" rx="19" ry="4" fill="rgba(0,0,0,0.35)" />
      <g transform={`scale(${westward ? -1 : 1} 1)`}>
        <rect x="-17" y="-11" width="27" height="15" rx="3" fill="#f2f4f7" />
        <rect x="-17" y="-11" width="27" height="4" rx="2" fill="#e07a3f" />
        <path d="M10 -8 L17 -2 L17 4 L10 4 Z" fill="#f2f4f7" />
        <path d="M10.5 -7 L15.5 -2.5 L10.5 -2.5 Z" fill="#8fa6bd" />
        <rect x="-13" y="-6" width="8" height="6" rx="1" fill="#8fa6bd" />
        <circle cx="-9" cy="5" r="3.6" fill="#1b2330" />
        <circle cx="9" cy="5" r="3.6" fill="#1b2330" />
      </g>
    </g>
  );
};

export type TripMapProps = {
  view: MapView;
  /** Chapters already finished. Drawn quiet and complete. */
  past: MapChapter[];
  /** The chapter being travelled, if any. */
  live?: MapChapter | null;
  /** How far through the live chapter we are, 0…1. */
  progress?: number;
  labels?: { text: string; point: MapPoint }[];
  showCamper?: boolean;
  /** The colour of the finished route. Quiet during the trip, warm at the end. */
  pastStroke?: string;
  /** A ring on one place, for the scene that says "it starts here". */
  markPoint?: MapPoint | null;
  /** A slow drift, applied as a transform so nothing is re-projected. */
  drift?: { scale: number; x: number; y: number };
};

export const TripMap: React.FC<TripMapProps> = ({
  view,
  past,
  live = null,
  progress = 1,
  labels = [],
  showCamper = true,
  pastStroke = ROUTE_DONE,
  markPoint = null,
  drift,
}) => {
  const livePoints = React.useMemo(() => (live ? flatten(live) : []), [live]);
  const head = React.useMemo(
    () => travelled(livePoints.map((entry) => entry.point), progress),
    [livePoints, progress],
  );
  const revealed = livePoints.slice(0, Math.max(2, head.index + 1));
  // Split the revealed part back into runs of one mode, so a ferry inside
  // a day keeps its own line style instead of being averaged away.
  const runs: { mode: string; points: MapPoint[] }[] = [];
  revealed.forEach((entry) => {
    const last = runs[runs.length - 1];
    if (last && last.mode === entry.mode) last.points.push(entry.point);
    else runs.push({ mode: entry.mode, points: [entry.point] });
  });
  if (runs.length && head.index > 0) {
    runs[runs.length - 1].points.push(head.position);
  }
  const [cameraX, cameraY] = showCamper ? view.project(head.position) : [0, 0];
  const transform = drift
    ? `translate(${drift.x} ${drift.y}) scale(${drift.scale})`
    : undefined;

  return (
    <svg
      width={view.width}
      height={view.height}
      viewBox={`0 0 ${view.width} ${view.height}`}
      style={{ display: "block", backgroundColor: WATER }}
    >
      <g transform={transform} style={{ transformOrigin: "center" }}>
        <path d={view.landPath} fill={LAND} stroke="none" />
        <path
          d={view.borderPath}
          fill="none"
          stroke={BORDER}
          strokeWidth={1}
          strokeLinejoin="round"
        />
        {past.map((chapter) =>
          chapter.segments.map((segment, position) => (
            <path
              key={`${chapter.chapterId}-${position}`}
              d={polyline(view, segment.points)}
              fill="none"
              stroke={segment.mode === "ferry" ? FERRY : pastStroke}
              strokeWidth={segment.mode === "ferry" ? 2.4 : 3.4}
              strokeLinecap="round"
              strokeLinejoin="round"
              // A ferry is dashed and an unverified straight line is
              // sparser still. Both say "this is not a road you drove".
              strokeDasharray={
                segment.mode === "ferry"
                  ? "9 7"
                  : segment.mode === "direct"
                    ? "3 8"
                    : undefined
              }
              opacity={pastStroke === ROUTE_DONE ? 0.75 : 0.95}
            />
          )),
        )}
        {runs.map((run, position) => (
          <path
            key={`live-${position}`}
            d={polyline(view, run.points)}
            fill="none"
            stroke={run.mode === "ferry" ? FERRY : ROUTE_LIVE}
            strokeWidth={run.mode === "ferry" ? 3 : 4.4}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={
              run.mode === "ferry" ? "10 8" : run.mode === "direct" ? "3 9" : undefined
            }
          />
        ))}
        {labels.map((label) => {
          const [x, y] = view.project(label.point);
          return (
            <g key={`${label.text}-${x.toFixed(0)}`}>
              <circle cx={x} cy={y} r={3.4} fill={LABEL} />
              <text
                x={x + 9}
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
        {markPoint ? (
          <g>
            <circle
              cx={view.project(markPoint)[0]}
              cy={view.project(markPoint)[1]}
              r={13}
              fill="none"
              stroke={ROUTE_LIVE}
              strokeWidth={2}
              opacity={0.6}
            />
            <circle
              cx={view.project(markPoint)[0]}
              cy={view.project(markPoint)[1]}
              r={4}
              fill={ROUTE_LIVE}
            />
          </g>
        ) : null}
        {showCamper && livePoints.length ? (
          <Camper x={cameraX} y={cameraY} heading={head.heading} />
        ) : null}
      </g>
    </svg>
  );
};
