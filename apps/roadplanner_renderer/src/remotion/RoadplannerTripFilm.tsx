/**
 * The trip film, cut from a scene plan.
 *
 * Film v0 gave every day the same card and the same slideshow, so a
 * three-week journey came out as twenty-three interchangeable blocks.
 * The story director now decides which days matter; this composition is
 * where that judgement becomes something you can see.
 *
 * **It decides nothing.** The plan arrives in the package with every
 * scene, its type, its length in frames and which photos it uses. This
 * file owns a fixed library of scene components and looks each one up by
 * name. Two consequences, both wanted: the same package renders to the
 * same film down to the frame, and a scene type nobody implemented is a
 * refused package rather than a broken video.
 *
 * The library is deliberately small - card, photo, hero, collage, text,
 * intro, outro, closing collage, and the three map scenes.
 * `visual_style` chooses between them; it cannot describe a layout. A
 * model that could invent shapes would eventually invent one that cannot
 * be drawn.
 *
 * The map is the one part that needs data the manifest does not carry.
 * It arrives as its own `map_context`, built from Roadplanner's stored
 * routing by its own module, and it is optional: a trip whose roadbook
 * has no coordinates renders every map scene as the thing it would
 * otherwise have been.
 */
import React from "react";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

import {
  Camper,
  TripMap,
  buildView,
  travelled,
  type MapContext,
  type MapPoint,
} from "./TripMap";

export const FILM_FPS = 30;

export type FilmPhoto = {
  path: string;
  sizeBytes: number;
  sha256: string;
  width: number;
  height: number;
  orientation: string;
};

export type FilmChapter = {
  chapterId: string;
  index: number;
  date: string;
  title: string;
  story: string;
  storySource: string;
  importance: string;
  storyRole: string;
  dayNumber: number;
  distanceKm: number | null;
  durationMinutes: number | null;
  stopCount: number;
  photoCount: number;
  stops: string[];
  photos: FilmPhoto[];
};

export type FilmTrip = {
  title: string;
  startDate: string;
  endDate: string;
  chapterCount: number;
  distanceKm: number | null;
  photoCount: number;
};

export type FilmNarrative = {
  titleVariant: string;
  subtitle: string;
  opening: string;
  closing: string;
  motifs: string[];
};

export type FilmScene = {
  type: string;
  chapterIndex: number;
  chapterId: string;
  frames: number;
  enter: string;
  photos: number[];
  paths: string[];
};

export type RoadplannerTripFilmProps = {
  trip: FilmTrip;
  chapters: FilmChapter[];
  narrative: FilmNarrative | null;
  scenes: FilmScene[];
  mapContext?: MapContext | null;
};

export const filmDurationInFrames = (scenes: { frames: number }[]): number =>
  scenes.reduce((sum, scene) => sum + scene.frames, 0);

const INK = "#f5f7fa";
const MUTED = "#9fb3c8";
const BACKDROP = "#101725";
// Four accents rather than one, keyed to the chapter, so consecutive days
// do not look like reprints of each other.
const ACCENTS = ["#e07a3f", "#3d9a8b", "#c2607f", "#5f7fc4"];

const base: React.CSSProperties = {
  fontFamily: "sans-serif",
  color: INK,
  backgroundColor: BACKDROP,
};

const useFade = (length: number): number => {
  const frame = useCurrentFrame();
  return Math.min(
    interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" }),
    interpolate(frame, [length - 8, length], [1, 0], { extrapolateLeft: "clamp" }),
  );
};

/**
 * How a scene arrives, from `story_role`.
 *
 * This is the entire job of that field, on purpose. Letting it also
 * change durations would put it in a fight with `importance`, and two
 * systems sizing the same thing is how a rule set becomes unexplainable.
 */
const useEnter = (enter: string, length: number): React.CSSProperties => {
  const frame = useCurrentFrame();
  const opacity = useFade(length);
  const eased = interpolate(frame, [0, 18], [0, 1], { extrapolateRight: "clamp" });
  if (enter === "rise") {
    return { opacity, transform: `translateY(${(1 - eased) * 46}px)` };
  }
  if (enter === "push") {
    return { opacity, transform: `translateX(${(1 - eased) * 60}px)` };
  }
  if (enter === "settle") {
    return { opacity, transform: `scale(${0.97 + eased * 0.03})` };
  }
  if (enter === "cut") {
    // No movement at all - a transfer day should feel like a beat, not a
    // production number.
    return { opacity: Math.min(1, opacity * 1.6) };
  }
  return { opacity };
};

const formatDuration = (minutes: number): string => {
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
};

/**
 * Two lines over a photograph, and only once per day.
 *
 * The earlier version put the same sentence over every picture of a
 * chapter in three large lines. Repeating a caption does not make it more
 * legible, it makes the pictures less visible - so it is smaller now, it
 * is clamped to two lines, its scrim is lighter, and the caller only
 * shows it on the first picture of a day.
 */
const Caption: React.FC<{ text: string; overPhoto: boolean }> = ({ text, overPhoto }) => {
  if (!text) return null;
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end" }}>
      <div
        style={{
          // A scrim rather than a box: the picture stays visible and the
          // text stays readable over whatever happens to be underneath.
          background: overPhoto
            ? "linear-gradient(transparent, rgba(0,0,0,0.55) 60%, rgba(0,0,0,0.78))"
            : "transparent",
          padding: overPhoto ? "150px 96px 58px" : "0 110px 0",
          fontFamily: "sans-serif",
          color: INK,
          fontSize: 29,
          lineHeight: 1.36,
          maxWidth: 1030,
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          WebkitLineClamp: 2,
          overflow: "hidden",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const IntroScene: React.FC<{
  trip: FilmTrip;
  narrative: FilmNarrative | null;
  scene: FilmScene;
}> = ({ trip, narrative, scene }) => {
  const style = useEnter(scene.enter, scene.frames);
  const frame = useCurrentFrame();
  const rule = interpolate(frame, [10, 45], [0, 220], { extrapolateRight: "clamp" });
  const span = [trip.startDate, trip.endDate].filter(Boolean).join(" – ");
  // The editor's title when there is one. It is the same trip either way,
  // so this is a variant and not a second name.
  const headline = narrative?.titleVariant || trip.title || "Eine Reise";
  return (
    <AbsoluteFill style={{ ...base, justifyContent: "center", padding: 120, ...style }}>
      <div style={{ fontSize: 28, color: MUTED, letterSpacing: 5 }}>ROADPLANNER</div>
      <div style={{ fontSize: 74, fontWeight: 700, marginTop: 20, lineHeight: 1.06 }}>
        {headline}
      </div>
      {narrative?.subtitle ? (
        <div style={{ fontSize: 36, color: MUTED, marginTop: 14, fontStyle: "italic" }}>
          {narrative.subtitle}
        </div>
      ) : null}
      <div style={{ height: 6, width: rule, backgroundColor: ACCENTS[0], margin: "26px 0" }} />
      {narrative?.opening ? (
        <div style={{ fontSize: 34, lineHeight: 1.4, maxWidth: 1180 }}>{narrative.opening}</div>
      ) : null}
      <div style={{ fontSize: 27, color: MUTED, marginTop: 26 }}>
        {[span, `${trip.chapterCount} Tage`, trip.distanceKm ? `${Math.round(trip.distanceKm)} km` : ""]
          .filter(Boolean)
          .join("   ·   ")}
      </div>
      {narrative?.motifs?.length ? (
        <div style={{ fontSize: 25, color: ACCENTS[1], marginTop: 16 }}>
          {narrative.motifs.slice(0, 4).join("  ·  ")}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

const ChapterCardScene: React.FC<{ chapter: FilmChapter; scene: FilmScene }> = ({
  chapter,
  scene,
}) => {
  const style = useEnter(scene.enter, scene.frames);
  const accent = ACCENTS[chapter.index % ACCENTS.length];
  const anchor = chapter.index % 2 === 0 ? "flex-start" : "flex-end";
  const facts = [
    chapter.distanceKm ? `${Math.round(chapter.distanceKm)} km` : "",
    chapter.durationMinutes ? formatDuration(chapter.durationMinutes) : "",
  ].filter(Boolean);
  return (
    <AbsoluteFill
      style={{
        ...base,
        justifyContent: "center",
        alignItems: anchor,
        textAlign: anchor === "flex-end" ? "right" : "left",
        padding: 120,
        ...style,
      }}
    >
      <div style={{ fontSize: 26, color: MUTED, letterSpacing: 4 }}>
        {[`TAG ${chapter.dayNumber}`, chapter.date].filter(Boolean).join("   ·   ")}
      </div>
      <div style={{ fontSize: 62, fontWeight: 700, marginTop: 16, maxWidth: 1240, lineHeight: 1.1 }}>
        {chapter.title || `Tag ${chapter.dayNumber}`}
      </div>
      <div style={{ height: 5, width: 150, backgroundColor: accent, margin: "24px 0" }} />
      {facts.length ? (
        <div style={{ fontSize: 30, color: MUTED }}>{facts.join("   ·   ")}</div>
      ) : null}
      {chapter.stops.length ? (
        <div style={{ fontSize: 28, color: MUTED, marginTop: 14, maxWidth: 1100 }}>
          {chapter.stops.join("  ›  ")}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/** One photograph with a slow push. The workhorse. */
const PhotoScene: React.FC<{
  chapter: FilmChapter;
  scene: FilmScene;
  hero?: boolean;
}> = ({ chapter, scene, hero = false }) => {
  const frame = useCurrentFrame();
  const opacity = useFade(scene.frames);
  const photo = chapter.photos[scene.photos[0] ?? 0];
  const forward = (chapter.index + (scene.photos[0] ?? 0)) % 2 === 0;
  const span = hero ? 0.09 : 0.06;
  const scale = interpolate(
    frame,
    [0, scene.frames],
    forward ? [1, 1 + span] : [1 + span, 1],
  );
  if (!photo) return <TextScene chapter={chapter} scene={scene} />;
  // A portrait photograph filled into a 16:9 frame loses its top and its
  // bottom - which is where the sky and the person are. So it is shown
  // whole, and the space beside it is filled with the same picture,
  // blurred and darkened. Nothing is cropped away and nothing foreign is
  // introduced: the backdrop is the photograph itself.
  const upright = photo.orientation === "portrait";
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000", opacity }}>
      {upright ? (
        <AbsoluteFill>
          <Img
            src={`/${photo.path}`}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              filter: "blur(38px) brightness(0.42) saturate(0.8)",
              transform: "scale(1.16)",
            }}
          />
        </AbsoluteFill>
      ) : null}
      <AbsoluteFill style={{ transform: `scale(${scale})` }}>
        <Img
          src={`/${photo.path}`}
          style={{
            width: "100%",
            height: "100%",
            objectFit: upright ? "contain" : "cover",
          }}
        />
      </AbsoluteFill>
      {/* A hero image is allowed to stand alone: the strongest picture of
          a day does not need a sentence written across it. And the same
          sentence over the second and third picture of a day was never
          read twice - it only covered them. */}
      {hero || (scene.photos[0] ?? 0) !== 0 ? null : (
        <Caption text={chapter.story} overPhoto />
      )}
    </AbsoluteFill>
  );
};

/**
 * Several pictures at once, arranged rather than tiled.
 *
 * The first version was a grid of equal rectangles, which made four good
 * photographs look like a contact sheet and cropped every one of them to
 * the same shape. This lays them out as an overlapping wall: one picture
 * is clearly the main one, the others sit around it at their own sizes
 * and slight angles, and each keeps its own proportions.
 *
 * The positions are a fixed table, not a random arrangement. The same
 * package has to render to the same film, and "artfully scattered" and
 * "reproducible" are only compatible if the scattering is written down.
 */
type Tile = { left: number; top: number; width: number; tilt: number };

const COLLAGE_LAYOUTS: Tile[][] = [
  // two
  [
    { left: 4, top: 10, width: 50, tilt: -1.4 },
    { left: 50, top: 30, width: 46, tilt: 1.6 },
  ],
  // three
  [
    { left: 3, top: 8, width: 45, tilt: -1.6 },
    { left: 45, top: 4, width: 36, tilt: 1.2 },
    { left: 38, top: 48, width: 42, tilt: -0.8 },
  ],
  // four
  [
    { left: 2, top: 12, width: 42, tilt: -1.8 },
    { left: 40, top: 3, width: 34, tilt: 1.1 },
    { left: 68, top: 26, width: 30, tilt: -1.1 },
    { left: 30, top: 50, width: 38, tilt: 1.7 },
  ],
];

const CollageScene: React.FC<{ chapter: FilmChapter; scene: FilmScene }> = ({
  chapter,
  scene,
}) => {
  const frame = useCurrentFrame();
  const opacity = useFade(scene.frames);
  const photos = scene.photos
    .map((position) => chapter.photos[position])
    .filter(Boolean) as FilmPhoto[];
  if (!photos.length) return <TextScene chapter={chapter} scene={scene} />;
  if (photos.length === 1) {
    return <PhotoScene chapter={chapter} scene={scene} hero />;
  }
  const layout = COLLAGE_LAYOUTS[Math.min(photos.length, 4) - 2];
  const accent = ACCENTS[chapter.index % ACCENTS.length];
  return (
    <AbsoluteFill style={{ ...base, opacity, overflow: "hidden" }}>
      {/* A faint wash so the wall sits on something rather than floating
          on a flat rectangle. */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at 30% 30%, ${accent}22, transparent 62%)`,
        }}
      />
      {photos.slice(0, layout.length).map((photo, position) => {
        const tile = layout[position];
        // Each picture arrives on its own beat and drifts a little, so
        // the wall assembles instead of appearing as one block.
        const appear = interpolate(
          frame,
          [position * 7, position * 7 + 20],
          [0, 1],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
        );
        const drift = interpolate(
          frame,
          [0, scene.frames],
          position % 2 === 0 ? [0, -10] : [0, 8],
        );
        return (
          <div
            key={photo.path}
            style={{
              position: "absolute",
              left: `${tile.left}%`,
              top: `${tile.top}%`,
              width: `${tile.width}%`,
              // Its own proportions, not the grid's. A portrait
              // photograph in this wall stays a portrait photograph.
              aspectRatio: `${photo.width || 4} / ${photo.height || 3}`,
              opacity: appear,
              transform: `translateY(${drift + (1 - appear) * 18}px) rotate(${tile.tilt}deg)`,
              boxShadow: "0 22px 48px rgba(0,0,0,0.55)",
              borderRadius: 8,
              overflow: "hidden",
              backgroundColor: "#000000",
            }}
          >
            <Img
              src={`/${photo.path}`}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
          </div>
        );
      })}
      <Caption text={chapter.story} overPhoto />
    </AbsoluteFill>
  );
};

/**
 * A day with no pictures, written rather than reported.
 *
 * Film v0 put "Für diesen Tag gibt es keine Fotos" on screen. That is a
 * diagnostic - true, and addressed to the wrong audience. The day still
 * happened and the editor still wrote about it, so it gets a page.
 */
const TextScene: React.FC<{ chapter: FilmChapter; scene: FilmScene }> = ({
  chapter,
  scene,
}) => {
  const style = useEnter(scene.enter, scene.frames);
  const accent = ACCENTS[chapter.index % ACCENTS.length];
  return (
    <AbsoluteFill
      style={{ ...base, justifyContent: "center", padding: "0 150px", ...style }}
    >
      <div style={{ fontSize: 24, color: MUTED, letterSpacing: 4 }}>
        {[`TAG ${chapter.dayNumber}`, chapter.date].filter(Boolean).join("   ·   ")}
      </div>
      <div style={{ height: 5, width: 110, backgroundColor: accent, margin: "22px 0 30px" }} />
      <div style={{ fontSize: 42, lineHeight: 1.45, maxWidth: 1320 }}>
        {chapter.story || chapter.title || `Tag ${chapter.dayNumber}`}
      </div>
    </AbsoluteFill>
  );
};

// --- the map ------------------------------------------------------------

const MAP_WIDTH = 1280;
const MAP_HEIGHT = 720;

/** A quiet strip along the bottom, so the map itself stays uncovered. */
const MapLabel: React.FC<{ lead: string; text: string; accent: string }> = ({
  lead,
  text,
  accent,
}) => (
  <AbsoluteFill style={{ justifyContent: "flex-end", pointerEvents: "none" }}>
    <div
      style={{
        background: "linear-gradient(transparent, rgba(9,14,22,0.82) 62%, rgba(9,14,22,0.94))",
        padding: "120px 90px 54px",
        fontFamily: "sans-serif",
      }}
    >
      <div style={{ fontSize: 23, color: MUTED, letterSpacing: 4 }}>{lead}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 18, marginTop: 10 }}>
        <div style={{ height: 4, width: 46, backgroundColor: accent }} />
        <div style={{ fontSize: 38, fontWeight: 600, color: INK }}>{text}</div>
      </div>
    </div>
  </AbsoluteFill>
);

/**
 * "Here is where it begins."
 *
 * Deliberately without the route ahead. A map that shows the whole
 * journey in the first ten seconds has spent the one thing it had to
 * give: the film can no longer show you anywhere new.
 */
const MapStartScene: React.FC<{
  map: MapContext;
  trip: FilmTrip;
  scene: FilmScene;
  firstPlace: string;
}> = ({ map, trip, scene, firstPlace }) => {
  const frame = useCurrentFrame();
  const opacity = useFade(scene.frames);
  const [lon, lat] = map.start;
  const view = React.useMemo(
    () => buildView([lon - 1.6, lat - 1.0, lon + 1.6, lat + 1.0], MAP_WIDTH, MAP_HEIGHT, 0.2),
    [lon, lat],
  );
  const [x, y] = view.project(map.start);
  // The camper settles onto the map rather than being there already.
  const drop = interpolate(frame, [0, 26], [-40, 0], { extrapolateRight: "clamp" });
  const settle = interpolate(frame, [0, 26], [0, 1], { extrapolateRight: "clamp" });
  const zoom = interpolate(frame, [0, scene.frames], [1.1, 1.02]);
  return (
    <AbsoluteFill style={{ ...base, opacity }}>
      <TripMap
        view={view}
        past={[]}
        showCamper={false}
        markPoint={map.start}
        drift={{ scale: zoom, x: 0, y: 0 }}
      />
      <AbsoluteFill>
        <svg width={MAP_WIDTH} height={MAP_HEIGHT} style={{ display: "block" }}>
          <g opacity={settle}>
            <Camper x={x} y={y + drop} heading={0} scale={1.5} />
          </g>
        </svg>
      </AbsoluteFill>
      <MapLabel
        lead="START"
        text={firstPlace || trip.startDate || "Los geht es"}
        accent={ACCENTS[0]}
      />
    </AbsoluteFill>
  );
};

/**
 * One day of driving, drawn as it happens.
 *
 * Everything travelled before today is on the map already, in a quiet
 * grey; today's leg draws itself in front of the camper as the scene
 * runs. What comes after today is not on the map at all.
 */
const MapLegScene: React.FC<{
  map: MapContext;
  chapter: FilmChapter;
  scene: FilmScene;
}> = ({ map, chapter, scene }) => {
  const frame = useCurrentFrame();
  const opacity = useFade(scene.frames);
  const live = map.chapters.find((entry) => entry.chapterId === scene.chapterId) ?? null;
  const past = React.useMemo(
    () => (live ? map.chapters.filter((entry) => entry.index < live.index) : []),
    [map, live],
  );
  // The view is fitted to today's leg, so a short hop is close and a long
  // transfer is wide - the camera says how far the day was without a
  // single number on screen. The previous day's end is included so the
  // journey visibly continues rather than jumping.
  const bbox = React.useMemo<[number, number, number, number]>(() => {
    if (!live) return map.bbox;
    const previous = past[past.length - 1];
    const box: [number, number, number, number] = [...live.bbox];
    if (previous) {
      box[0] = Math.min(box[0], previous.end[0]);
      box[1] = Math.min(box[1], previous.end[1]);
      box[2] = Math.max(box[2], previous.end[0]);
      box[3] = Math.max(box[3], previous.end[1]);
    }
    return box;
  }, [live, past, map.bbox]);
  const view = React.useMemo(
    () => buildView(bbox, MAP_WIDTH, MAP_HEIGHT),
    [bbox],
  );
  if (!live) return <ChapterCardScene chapter={chapter} scene={scene} />;
  // The drive fills most of the scene and then holds, so the day ends on
  // its destination instead of cutting away mid-road.
  const progress = interpolate(frame, [8, scene.frames - 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const zoom = interpolate(frame, [0, scene.frames], [1.0, 1.05]);
  const destination = chapter.stops[chapter.stops.length - 1] || "";
  const labels: { text: string; point: MapPoint }[] = destination
    ? [{ text: destination, point: live.end }]
    : [];
  return (
    <AbsoluteFill style={{ ...base, opacity }}>
      <TripMap
        view={view}
        past={past}
        live={live}
        progress={progress}
        labels={progress > 0.72 ? labels : []}
        drift={{ scale: zoom, x: 0, y: 0 }}
      />
      <MapLabel
        lead={[`TAG ${chapter.dayNumber}`, chapter.date].filter(Boolean).join("   ·   ")}
        text={chapter.title || destination || `Tag ${chapter.dayNumber}`}
        accent={ACCENTS[chapter.index % ACCENTS.length]}
      />
    </AbsoluteFill>
  );
};

/** The whole route at last - the only scene that shows all of it. */
const MapFullScene: React.FC<{ map: MapContext; trip: FilmTrip; scene: FilmScene }> = ({
  map,
  trip,
  scene,
}) => {
  const frame = useCurrentFrame();
  const opacity = useFade(scene.frames);
  const view = React.useMemo(
    () => buildView(map.bbox, MAP_WIDTH, MAP_HEIGHT, 0.35),
    [map.bbox],
  );
  // The days appear one after another rather than all at once, so the
  // closing image is the journey being retraced.
  const shown = Math.max(
    1,
    Math.round(
      interpolate(frame, [10, scene.frames - 30], [1, map.chapters.length], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      }),
    ),
  );
  const past = map.chapters.slice(0, shown);
  const last = past[past.length - 1];
  const end = last ? view.project(last.end) : [0, 0];
  const heading = last
    ? travelled(
        last.segments.flatMap((segment) => segment.points),
        1,
      ).heading
    : 0;
  const zoom = interpolate(frame, [0, scene.frames], [1.02, 1.06]);
  return (
    <AbsoluteFill style={{ ...base, opacity }}>
      {/* The finished journey is the subject of this scene, not its
          background, so the route is warm here rather than grey. */}
      <TripMap
        view={view}
        past={past}
        showCamper={false}
        pastStroke="#e07a3f"
        drift={{ scale: zoom, x: 0, y: 0 }}
      />
      <AbsoluteFill>
        <svg width={MAP_WIDTH} height={MAP_HEIGHT} style={{ display: "block" }}>
          <Camper x={end[0]} y={end[1]} heading={heading} scale={1.15} />
        </svg>
      </AbsoluteFill>
      <MapLabel
        lead="DIE GANZE STRECKE"
        text={
          trip.distanceKm
            ? `${Math.round(trip.distanceKm)} km in ${trip.chapterCount} Tagen`
            : `${trip.chapterCount} Tage`
        }
        accent={ACCENTS[1]}
      />
    </AbsoluteFill>
  );
};

const OutroScene: React.FC<{
  trip: FilmTrip;
  chapters: FilmChapter[];
  narrative: FilmNarrative | null;
  scene: FilmScene;
}> = ({ trip, chapters, narrative, scene }) => {
  const style = useEnter(scene.enter, scene.frames);
  const shown = chapters.reduce((sum, chapter) => sum + chapter.photos.length, 0);
  // Only figures Roadplanner already holds. Nothing here is estimated.
  const entries = [
    [`${trip.chapterCount}`, trip.chapterCount === 1 ? "Tag" : "Tage"],
    trip.distanceKm ? [`${Math.round(trip.distanceKm)}`, "Kilometer"] : null,
    [`${shown}`, shown === 1 ? "Bild" : "Bilder"],
  ].filter(Boolean) as [string, string][];
  return (
    <AbsoluteFill
      style={{ ...base, alignItems: "center", justifyContent: "center", ...style }}
    >
      {narrative?.closing ? (
        <div
          style={{
            fontSize: 40,
            lineHeight: 1.42,
            textAlign: "center",
            maxWidth: 1180,
            marginBottom: 40,
          }}
        >
          {narrative.closing}
        </div>
      ) : (
        <div style={{ fontSize: 46, fontWeight: 600, textAlign: "center", maxWidth: 980 }}>
          {narrative?.titleVariant || trip.title || "Eine Reise"}
        </div>
      )}
      <div style={{ height: 5, width: 120, backgroundColor: ACCENTS[1], margin: "8px 0 30px" }} />
      <div style={{ display: "flex", gap: 56 }}>
        {entries.map(([value, label]) => (
          <div key={label} style={{ textAlign: "center" }}>
            <div style={{ fontSize: 52, fontWeight: 700 }}>{value}</div>
            <div style={{ fontSize: 24, color: MUTED }}>{label}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 24, color: MUTED, letterSpacing: 4, marginTop: 40 }}>
        ROADPLANNER
      </div>
    </AbsoluteFill>
  );
};

/** The last image of the film is the journey, not its final day. */
const OutroCollageScene: React.FC<{ scene: FilmScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const opacity = useFade(scene.frames);
  const paths = scene.paths.filter(Boolean);
  if (!paths.length) return <AbsoluteFill style={{ ...base, opacity }} />;
  const columns = paths.length >= 4 ? 3 : paths.length;
  return (
    <AbsoluteFill style={{ ...base, opacity, padding: 40 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${columns}, 1fr)`,
          gap: 14,
          width: "100%",
          height: "100%",
        }}
      >
        {paths.map((path, position) => {
          // They arrive one after another rather than all at once, which
          // is what makes this read as an ending.
          const appear = interpolate(
            frame,
            [position * 6, position * 6 + 22],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          );
          return (
            <div
              key={path}
              style={{ overflow: "hidden", borderRadius: 10, opacity: appear }}
            >
              <Img
                src={`/${path}`}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
              />
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const RoadplannerTripFilm: React.FC<RoadplannerTripFilmProps> = ({
  trip,
  chapters,
  narrative,
  scenes,
  mapContext = null,
}) => {
  const { fps } = useVideoConfig();
  if (fps !== FILM_FPS) {
    throw new Error(`Der Film erwartet ${FILM_FPS} fps, bekommen hat er ${fps}.`);
  }
  let cursor = 0;
  const rendered: React.ReactNode[] = [];
  scenes.forEach((scene, position) => {
    const chapter = chapters[scene.chapterIndex];
    let body: React.ReactNode = null;
    if (scene.type === "intro") {
      body = <IntroScene trip={trip} narrative={narrative} scene={scene} />;
    } else if (scene.type === "outro") {
      body = (
        <OutroScene trip={trip} chapters={chapters} narrative={narrative} scene={scene} />
      );
    } else if (scene.type === "outro_collage") {
      body = <OutroCollageScene scene={scene} />;
    } else if (scene.type === "map_start" || scene.type === "map_full") {
      // A map scene without map data cannot be drawn, and the plan only
      // contains one when the package carried a context - so this is the
      // same unreachable branch as the missing chapter below, and it is
      // skipped rather than rendered as an empty frame.
      if (mapContext) {
        body =
          scene.type === "map_start" ? (
            <MapStartScene
              map={mapContext}
              trip={trip}
              scene={scene}
              firstPlace={chapters[0]?.stops[0] ?? ""}
            />
          ) : (
            <MapFullScene map={mapContext} trip={trip} scene={scene} />
          );
      } else {
        cursor += scene.frames;
        return;
      }
    } else if (!chapter) {
      // A scene pointing at a chapter that is not there. The plan is
      // validated before it gets here, so this is unreachable - and
      // skipping beats rendering a blank frame nobody can explain.
      cursor += scene.frames;
      return;
    } else if (scene.type === "chapter_card") {
      body = <ChapterCardScene chapter={chapter} scene={scene} />;
    } else if (scene.type === "map_leg") {
      // Without geography the day still has to be shown, so it falls back
      // to its card. A style is a wish; a coordinate is a fact.
      body = mapContext ? (
        <MapLegScene map={mapContext} chapter={chapter} scene={scene} />
      ) : (
        <ChapterCardScene chapter={chapter} scene={scene} />
      );
    } else if (scene.type === "hero") {
      body = <PhotoScene chapter={chapter} scene={scene} hero />;
    } else if (scene.type === "collage") {
      body = <CollageScene chapter={chapter} scene={scene} />;
    } else if (scene.type === "text") {
      body = <TextScene chapter={chapter} scene={scene} />;
    } else {
      body = <PhotoScene chapter={chapter} scene={scene} />;
    }
    rendered.push(
      <Sequence
        key={`${scene.type}-${position}`}
        from={cursor}
        durationInFrames={scene.frames}
      >
        {body}
      </Sequence>,
    );
    cursor += scene.frames;
  });
  return <AbsoluteFill style={{ backgroundColor: BACKDROP }}>{rendered}</AbsoluteFill>;
};
