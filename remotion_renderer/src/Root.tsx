import React from "react";
import { Composition } from "remotion";
import { RoadplannerTest } from "./RoadplannerTest";

/**
 * Exactly one composition. Its id is on the Python side's whitelist
 * (`remotion_job.ALLOWED_COMPOSITIONS`); the two must stay in step.
 *
 * Duration and dimensions are placeholders - the render script overrides
 * them from the validated job, so the numbers that reach ffmpeg always
 * come from the bounded contract rather than from this file.
 */
export const RemotionRoot: React.FC = () => (
  <Composition
    id="roadplanner-test"
    component={RoadplannerTest}
    durationInFrames={150}
    fps={30}
    width={1280}
    height={720}
    defaultProps={{
      title: "Roadplanner Remotion Test",
      subtitle: "Lokaler Unterprozess",
    }}
  />
);
