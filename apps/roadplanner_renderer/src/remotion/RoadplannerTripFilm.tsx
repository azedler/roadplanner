/**
 * A whole trip, as a film, from the TravelStoryManifest and nothing else.
 *
 * The arc is deliberately plain: an opening card that says which trip this
 * is, one chapter per day, a closing card that adds up what happened. That
 * is a real beginning, middle and end built from facts the manifest
 * already holds - no dramaturgy was invented, because inventing one would
 * mean inventing the material to fill it.
 *
 * **A gap is drawn as a gap.** A day with no photos gets a card that says
 * so. A day with no distance shows no distance. The point of this film is
 * to find out where the manifest is too thin, and a composition that
 * quietly skipped thin days would hide exactly the answer we are after.
 *
 * Monotony is the real risk over twenty-five days, and it is fought with
 * the only material available: the chapter index. The day card alternates
 * which side it is anchored to, the accent colour walks through a small
 * palette, and the slow push on a photo alternates direction. All of it is
 * a pure function of the index, so the film stays reproducible - the same
 * package always renders to the same video.
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
export const INTRO_FRAMES = 105;
export const CHAPTER_CARD_FRAMES = 66;
export const STORY_FRAMES = 84;
export const EXTRA_PHOTO_FRAMES = 42;
export const OUTRO_FRAMES = 105;

export type FilmPhoto = { path: string; sizeBytes: number; sha256: string };

export type FilmChapter = {
  chapterId: string;
  index: number;
  date: string;
  title: string;
  story: string;
  storySource: string;
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

export type RoadplannerTripFilmProps = {
  trip: FilmTrip;
  chapters: FilmChapter[];
};

export const chapterFrames = (photoCount: number): number =>
  CHAPTER_CARD_FRAMES + STORY_FRAMES + Math.max(0, photoCount - 1) * EXTRA_PHOTO_FRAMES;

export const filmDurationInFrames = (chapters: { photos: unknown[] }[]): number =>
  INTRO_FRAMES +
  chapters.reduce((sum, chapter) => sum + chapterFrames(chapter.photos.length), 0) +
  OUTRO_FRAMES;

const INK = "#f5f7fa";
const MUTED = "#9fb3c8";
const BACKDROP = "#101725";
// Four accents rather than one. Twenty-five identical cards is the
// monotony this film is being built to detect, so the one dimension that
// can vary without inventing content does.
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

const formatDuration = (minutes: number): string => {
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
};

const Intro: React.FC<{ trip: FilmTrip }> = ({ trip }) => {
  const opacity = useFade(INTRO_FRAMES);
  const frame = useCurrentFrame();
  const rule = interpolate(frame, [10, 45], [0, 220], { extrapolateRight: "clamp" });
  const span = [trip.startDate, trip.endDate].filter(Boolean).join(" – ");
  return (
    <AbsoluteFill style={{ ...base, justifyContent: "center", padding: 120, opacity }}>
      <div style={{ fontSize: 30, color: MUTED, letterSpacing: 5 }}>ROADPLANNER</div>
      <div style={{ fontSize: 76, fontWeight: 700, marginTop: 22, lineHeight: 1.08 }}>
        {trip.title || "Eine Reise"}
      </div>
      <div style={{ height: 6, width: rule, backgroundColor: ACCENTS[0], margin: "26px 0" }} />
      <div style={{ fontSize: 30, color: MUTED }}>
        {[span, `${trip.chapterCount} Tage`, trip.distanceKm ? `${Math.round(trip.distanceKm)} km` : ""]
          .filter(Boolean)
          .join("   ·   ")}
      </div>
    </AbsoluteFill>
  );
};

const ChapterCard: React.FC<{ chapter: FilmChapter }> = ({ chapter }) => {
  const opacity = useFade(CHAPTER_CARD_FRAMES);
  const accent = ACCENTS[chapter.index % ACCENTS.length];
  // Alternating anchor: the only layout variation the data supports.
  const right = chapter.index % 2 === 1;
  const facts = [
    chapter.distanceKm ? `${Math.round(chapter.distanceKm)} km` : "",
    chapter.durationMinutes ? formatDuration(chapter.durationMinutes) : "",
    chapter.stopCount ? `${chapter.stopCount} Stopps` : "",
  ].filter(Boolean);
  return (
    <AbsoluteFill
      style={{
        ...base,
        justifyContent: "center",
        alignItems: right ? "flex-end" : "flex-start",
        textAlign: right ? "right" : "left",
        padding: 110,
        opacity,
      }}
    >
      <div style={{ fontSize: 28, color: MUTED, letterSpacing: 3 }}>
        {[`TAG ${chapter.dayNumber}`, chapter.date].filter(Boolean).join("   ·   ")}
      </div>
      <div style={{ fontSize: 58, fontWeight: 700, marginTop: 16, maxWidth: 960, lineHeight: 1.12 }}>
        {chapter.title || "Ohne Titel"}
      </div>
      <div style={{ height: 5, width: 120, backgroundColor: accent, margin: "22px 0" }} />
      {facts.length ? (
        <div style={{ fontSize: 30, color: accent }}>{facts.join("   ·   ")}</div>
      ) : (
        // Said out loud rather than left blank: this is a finding about the
        // manifest, and the film is how it becomes visible.
        <div style={{ fontSize: 24, color: MUTED }}>Keine Fahrtdaten hinterlegt</div>
      )}
      {chapter.stops.length ? (
        <div style={{ fontSize: 26, color: MUTED, marginTop: 14, maxWidth: 940 }}>
          {chapter.stops.slice(0, 4).join("  ›  ")}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/** A photo with a slow push, or an honest note that there is none. */
const PhotoStage: React.FC<{
  photo: FilmPhoto | undefined;
  chapter: FilmChapter;
  length: number;
  children?: React.ReactNode;
}> = ({ photo, chapter, length, children }) => {
  const frame = useCurrentFrame();
  const opacity = useFade(length);
  const forward = chapter.index % 2 === 0;
  const scale = interpolate(frame, [0, length], forward ? [1.0, 1.06] : [1.06, 1.0]);
  if (!photo) {
    return (
      <AbsoluteFill style={{ ...base, justifyContent: "center", padding: 110, opacity }}>
        <div style={{ fontSize: 26, color: MUTED, letterSpacing: 3 }}>
          {`TAG ${chapter.dayNumber}`}
        </div>
        <div style={{ fontSize: 34, color: MUTED, marginTop: 18 }}>
          {chapter.photoCount
            ? `${chapter.photoCount} Fotos an diesem Tag, keines im Film`
            : "Für diesen Tag gibt es keine Fotos"}
        </div>
        {children}
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000", opacity }}>
      <AbsoluteFill style={{ transform: `scale(${scale})` }}>
        {/* An absolute path so it resolves against the served bundle root
            regardless of which page URL Remotion is on. */}
        <Img src={`/${photo.path}`} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
      {children}
    </AbsoluteFill>
  );
};

const StoryOverlay: React.FC<{ chapter: FilmChapter; overPhoto: boolean }> = ({
  chapter,
  overPhoto,
}) => {
  if (!chapter.story) return null;
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end" }}>
      <div
        style={{
          background: overPhoto
            ? "linear-gradient(transparent, rgba(0,0,0,0.78))"
            : "transparent",
          padding: overPhoto ? "150px 90px 60px" : "0 110px 0",
          fontFamily: "sans-serif",
          color: INK,
          fontSize: 32,
          lineHeight: 1.42,
          whiteSpace: "pre-wrap",
        }}
      >
        {chapter.story}
      </div>
    </AbsoluteFill>
  );
};

const Outro: React.FC<{ trip: FilmTrip; chapters: FilmChapter[] }> = ({ trip, chapters }) => {
  const opacity = useFade(OUTRO_FRAMES);
  const shown = chapters.reduce((sum, chapter) => sum + chapter.photos.length, 0);
  const entries = [
    [`${trip.chapterCount}`, trip.chapterCount === 1 ? "Tag" : "Tage"],
    trip.distanceKm ? [`${Math.round(trip.distanceKm)}`, "Kilometer"] : null,
    [`${shown}`, shown === 1 ? "Bild" : "Bilder"],
  ].filter(Boolean) as [string, string][];
  return (
    <AbsoluteFill
      style={{ ...base, alignItems: "center", justifyContent: "center", opacity }}
    >
      <div style={{ fontSize: 46, fontWeight: 600, textAlign: "center", maxWidth: 980 }}>
        {trip.title || "Eine Reise"}
      </div>
      <div style={{ height: 5, width: 120, backgroundColor: ACCENTS[1], margin: "26px 0" }} />
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

export const RoadplannerTripFilm: React.FC<RoadplannerTripFilmProps> = ({ trip, chapters }) => {
  const { fps } = useVideoConfig();
  if (fps !== FILM_FPS) {
    throw new Error(`Der Film erwartet ${FILM_FPS} fps, bekommen hat er ${fps}.`);
  }
  const scenes: React.ReactNode[] = [];
  let cursor = 0;

  scenes.push(
    <Sequence key="intro" from={cursor} durationInFrames={INTRO_FRAMES}>
      <Intro trip={trip} />
    </Sequence>,
  );
  cursor += INTRO_FRAMES;

  chapters.forEach((chapter) => {
    scenes.push(
      <Sequence
        key={`card-${chapter.chapterId}`}
        from={cursor}
        durationInFrames={CHAPTER_CARD_FRAMES}
      >
        <ChapterCard chapter={chapter} />
      </Sequence>,
    );
    cursor += CHAPTER_CARD_FRAMES;

    // The story sits over the day's first picture - or on its own, when
    // there is none. Either way it is shown; a day without a photo is
    // still a day of the trip.
    scenes.push(
      <Sequence
        key={`story-${chapter.chapterId}`}
        from={cursor}
        durationInFrames={STORY_FRAMES}
      >
        <PhotoStage photo={chapter.photos[0]} chapter={chapter} length={STORY_FRAMES}>
          <StoryOverlay chapter={chapter} overPhoto={Boolean(chapter.photos[0])} />
        </PhotoStage>
      </Sequence>,
    );
    cursor += STORY_FRAMES;

    chapter.photos.slice(1).forEach((photo, position) => {
      scenes.push(
        <Sequence
          key={`photo-${chapter.chapterId}-${position}`}
          from={cursor}
          durationInFrames={EXTRA_PHOTO_FRAMES}
        >
          <PhotoStage photo={photo} chapter={chapter} length={EXTRA_PHOTO_FRAMES} />
        </Sequence>,
      );
      cursor += EXTRA_PHOTO_FRAMES;
    });
  });

  scenes.push(
    <Sequence key="outro" from={cursor} durationInFrames={OUTRO_FRAMES}>
      <Outro trip={trip} chapters={chapters} />
    </Sequence>,
  );

  return <AbsoluteFill style={{ backgroundColor: BACKDROP }}>{scenes}</AbsoluteFill>;
};
