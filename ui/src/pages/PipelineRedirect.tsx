/**
 * PipelineRedirect, `/pipelines/:pipelineId`.
 *
 * Thin route wrapper that loads a pipeline into PipelineContext (snapshot-
 * replay if terminal, live SSE attach if running) and then redirects to
 * `/new`, which already auto-renders `PipelineRunView` when the context
 * has a non-idle pipeline.
 *
 * Why it exists:
 *   - Multiple surfaces deep-link to pipelines via `/pipelines/:id`
 *     (Dashboard recent-builds rows, PipelineStatusPill in the topbar,
 *     LiveDemoCard etc.). Before this route existed those links 404'd
 *     because no route matched.
 *   - Keeping `/pipelines/:id` as a stable URL means shared links keep
 *     working even though the canonical "watch a build" view lives at
 *     `/new` (which auto-renders based on context state).
 *
 * The redirect uses `replace: true` so the user's back button takes them
 * back to wherever they clicked from, not back to this loader page.
 */
import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { usePipeline } from "@/lib/PipelineContext";

export function PipelineRedirect() {
  const { pipelineId } = useParams();
  const navigate = useNavigate();
  const pipeline = usePipeline();

  useEffect(() => {
    if (!pipelineId) {
      navigate("/", { replace: true });
      return;
    }
    // Already loaded? Just redirect.
    if (pipeline.pipelineId === pipelineId) {
      navigate("/new", { replace: true });
      return;
    }
    void pipeline.loadPipeline(pipelineId).then(() => {
      navigate("/new", { replace: true });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineId]);

  return (
    <div className="py-16 text-center">
      <div className="text-sm text-[var(--color-text-muted)]">
        Loading pipeline {pipelineId?.slice(0, 8)}&hellip;
      </div>
    </div>
  );
}
