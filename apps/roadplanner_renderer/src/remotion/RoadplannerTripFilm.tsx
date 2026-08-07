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
 * intro, outro, closing collage. `visual_style` chooses between them; it
 * cannot describe a layout. A model that could invent shapes would
 * eventually invent one that cannot be drawn.
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

export const FILM_FPS = 30;

export type FilmPhoto = { path: string; sizeBytes: number; sha256: string };

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

/** Two lines, three at the very most - a card is not a page. */
const Caption: React.FC<{ text: string; overPhoto: boolean }> = ({ text, overPhoto }) => {
  if (!text) return null;
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end" }}>
      <div
        style={{
          // A scrim rather than a box: the picture stays visible and the
          // text stays readable over whatever happens to be underneath.
          background: overPhoto
            ? "linear-gradient(transparent, rgba(0,0,0,0.72) 55%, rgba(0,0,0,0.88))"
            : "transparent",
          padding: overPhoto ? "170px 92px 66px" : "0 110px 0",
          fontFamily: "sans-serif",
          color: INK,
          fontSize: 34,
          lineHeight: 1.38,
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          WebkitLineClamp: 3,
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
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000", opacity }}>
      <AbsoluteFill style={{ transform: `scale(${scale})` }}>
        <Img src={`/${photo.path}`} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
      {/* A hero image is allowed to stand alone: the strongest picture of
          a day does not need a sentence written across it. */}
      {hero ? null : <Caption text={chapter.story} overPhoto />}
    </AbsoluteFill>
  );
};

/** Several pictures at once, for a day that had a lot going on. */
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
  const columns = photos.length >= 3 ? 2 : photos.length;
  return (
    <AbsoluteFill style={{ ...base, opacity, padding: 44 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${columns}, 1fr)`,
          gap: 18,
          width: "100%",
          height: "100%",
        }}
      >
        {photos.map((photo, position) => {
          // Each tile drifts a little, and each starts at a different
          // moment, so the grid breathes instead of pulsing as one block.
          const offset = interpolate(
            frame,
            [0, scene.frames],
            position % 2 === 0 ? [0, -14] : [-14, 0],
          );
          return (
            <div key={photo.path} style={{ overflow: "hidden", borderRadius: 10 }}>
              <Img
                src={`/${photo.path}`}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  transform: `translateY(${offset}px) scale(1.05)`,
                }}
              />
            </div>
          );
        })}
      </div>
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
    } else if (!chapter) {
      // A scene pointing at a chapter that is not there. The plan is
      // validated before it gets here, so this is unreachable - and
      // skipping beats rendering a blank frame nobody can explain.
      cursor += scene.frames;
      return;
    } else if (scene.type === "chapter_card") {
      body = <ChapterCardScene chapter={chapter} scene={scene} />;
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
