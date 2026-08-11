/**
 * The composition registry.
 *
 * Two compositions exist, and their ids are the only values the worker
 * ever passes to the renderer. A whitelist rather than a free string,
 * because the name reaches a rendering engine.
 *
 * The test composition is fixed at five seconds; the trip-day composition
 * works out its own length from the number of photos it was given, which
 * is what `calculateMetadata` is for. The renderer reads that length back
 * from the selected composition instead of computing it again.
 */
import React from "react";
import { Composition } from "remotion";

import { DESIGN_HEIGHT, DESIGN_WIDTH, FILM_FPS, renderProfile } from "../render_profiles.mjs";

import { RoadplannerTest } from "./RoadplannerTest";
import {
  RoadplannerTripDay,
  type RoadplannerTripDayProps,
  tripDayDurationInFrames,
} from "./RoadplannerTripDay";
import {
  RoadplannerTripFilm,
  type RoadplannerTripFilmProps,
  filmDurationInFrames,
} from "./RoadplannerTripFilm";

export const COMPOSITION_ID = "roadplanner-remotion-test";
export const TRIP_DAY_COMPOSITION_ID = "roadplanner-trip-day";
export const TRIP_FILM_COMPOSITION_ID = "roadplanner-trip-film";

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id={COMPOSITION_ID}
      component={RoadplannerTest}
      durationInFrames={150}
      fps={30}
      width={1280}
      height={720}
      defaultProps={{ title: "Roadplanner Remotion Test" }}
    />
    <Composition
      id={TRIP_DAY_COMPOSITION_ID}
      component={RoadplannerTripDay}
      // Replaced by calculateMetadata for every real render; this value
      // only ever applies to the defaults below.
      durationInFrames={tripDayDurationInFrames(1)}
      fps={30}
      width={1280}
      height={720}
      calculateMetadata={({ props }: { props: RoadplannerTripDayProps }) => ({
        durationInFrames: tripDayDurationInFrames(props.photos?.length ?? 0),
      })}
      defaultProps={
        {
          tripTitle: "Roadplanner",
          day: {
            dayId: "vorschau",
            date: "",
            title: "Reisetag",
            summary: "",
            number: null,
            count: null,
            distanceKm: null,
            durationMinutes: null,
          },
          stops: [],
          photos: [],
        } satisfies RoadplannerTripDayProps
      }
    />
    <Composition
      id={TRIP_FILM_COMPOSITION_ID}
      component={RoadplannerTripFilm}
      durationInFrames={filmDurationInFrames([{ frames: 90 }])}
      // The design surface. Every real render replaces these through
      // calculateMetadata below; they are the profile-less defaults, and
      // they are the size the film is authored at.
      fps={FILM_FPS}
      width={DESIGN_WIDTH}
      height={DESIGN_HEIGHT}
      calculateMetadata={({ props }: { props: RoadplannerTripFilmProps }) => {
        // The size is the profile's; the content is the plan's. Both are
        // resolved here so the renderer reads one answer back from the
        // selected composition instead of computing either again.
        const profile = renderProfile(props.renderProfile);
        return {
          // The plan carries the length. The renderer reads it back from
          // the selected composition rather than recomputing it, so there
          // is exactly one place a frame count comes from.
          durationInFrames: Math.max(1, filmDurationInFrames(props.scenes ?? [])),
          width: profile.width,
          height: profile.height,
          fps: profile.fps,
        };
      }}
      defaultProps={
        {
          trip: {
            title: "Roadplanner",
            startDate: "",
            endDate: "",
            chapterCount: 1,
            distanceKm: null,
            photoCount: 0,
          },
          chapters: [
            {
              chapterId: "vorschau",
              index: 0,
              date: "",
              title: "Reisetag",
              story: "",
              storySource: "composed",
              dayNumber: 1,
              distanceKm: null,
              durationMinutes: null,
              stopCount: 0,
              photoCount: 0,
              importance: "normal",
              storyRole: "journey",
              stops: [],
              photos: [],
            },
          ],
          narrative: null,
          mapContext: null,
          crew: null,
          music: null,
          scenes: [
            {
              type: "chapter_card",
              chapterIndex: 0,
              chapterId: "vorschau",
              frames: 90,
              enter: "fade",
              photos: [],
              paths: [],
            },
          ],
        } satisfies RoadplannerTripFilmProps
      }
    />
  </>
);
