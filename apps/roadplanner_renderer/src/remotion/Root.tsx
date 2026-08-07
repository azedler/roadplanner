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

import { RoadplannerTest } from "./RoadplannerTest";
import {
  RoadplannerTripDay,
  type RoadplannerTripDayProps,
  tripDayDurationInFrames,
} from "./RoadplannerTripDay";

export const COMPOSITION_ID = "roadplanner-remotion-test";
export const TRIP_DAY_COMPOSITION_ID = "roadplanner-trip-day";

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
  </>
);
