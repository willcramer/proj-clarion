import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ComponentType } from "react";

import { Layout } from "@/components/Layout";
import { SetupGate } from "@/components/SetupGate";
import { ToastProvider } from "@/components/Toast";
import { AboutPage } from "@/pages/About";
import { DocsPage } from "@/pages/Docs";

// The leadership one-pager is internal-only and git-ignored. Load it
// optionally: present locally → route mounts; absent on a clean checkout →
// the glob is empty and the build still succeeds.
const onePagerMods = import.meta.glob("./pages/OnePager.tsx", { eager: true }) as Record<
  string,
  { OnePagerPage?: ComponentType }
>;
const OnePagerPage = Object.values(onePagerMods)[0]?.OnePagerPage;
import { AuditPage } from "@/pages/Audit";
import { DashboardPage } from "@/pages/Dashboard";
import { ProfilesListPage, ProfileDetailPage } from "@/pages/Profiles";
import { ProfileDeliverablePage } from "@/pages/ProfileDeliverable";
import { PlansListPage, PlanDetailPage } from "@/pages/Plans";
import { RunsPage } from "@/pages/Runs";
import { NewDemoPage } from "@/pages/NewDemo";
import { PipelinesPage } from "@/pages/Pipelines";
import { PipelineRedirect } from "@/pages/PipelineRedirect";
import { SettingsRoute } from "@/pages/SettingsRoute";
import { PipelineProvider } from "@/lib/PipelineContext";
import { ThemeProvider } from "@/lib/ThemeContext";
import { AssistantProvider } from "@/lib/AssistantContext";

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      // SE-facing dashboard, staleness OK, but refetch on focus so they
      // see fresh state when they switch back.
      staleTime: 5_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <ThemeProvider>
          <ToastProvider>
            {/*
             * SetupGate wraps the router so we can short-circuit to the
             * setup wizard before any of these pages tries to fetch data
             * (and gets a 503 setup-required). Once setup is complete,
             * the gate transparently renders its children, including
             * the explicit `/setup` route that's reachable from the
             * UserMenu for token rotation / re-upload.
             */}
            <SetupGate>
              <PipelineProvider>
                {/*
                 * AssistantProvider sits inside the router (so the drawer
                 * can derive scope from the current route via useLocation)
                 * and inside PipelineProvider (so assistant-triggered builds
                 * can latch into the followed-pipeline context). Any page can
                 * call useAssistant().openAssistant({ scope, seedPrompt }).
                 */}
                <AssistantProvider>
                <Routes>
                  <Route element={<Layout />}>
                    <Route index element={<DashboardPage />} />
                    <Route path="/new" element={<NewDemoPage />} />
                    <Route path="/profiles" element={<ProfilesListPage />} />
                    <Route path="/profiles/:profileId" element={<ProfileDetailPage />} />
                    {/* Hidden deliverable page — reached from a button on the
                        profile page (wiring added separately). */}
                    <Route path="/profiles/:profileId/deliverable" element={<ProfileDeliverablePage />} />
                    <Route path="/plans" element={<PlansListPage />} />
                    <Route path="/plans/:planId" element={<PlanDetailPage />} />
                    <Route path="/runs" element={<RunsPage />} />
                    <Route path="/pipelines" element={<PipelinesPage />} />
                    {/* Deep-linked single pipeline, loads it into
                        PipelineContext and forwards to /new which is
                        the canonical "watch a build" view. */}
                    <Route path="/pipelines/:pipelineId" element={<PipelineRedirect />} />
                    <Route path="/audit" element={<AuditPage />} />
                    <Route path="/about" element={<AboutPage />} />
                    {OnePagerPage && <Route path="/one-pager" element={<OnePagerPage />} />}
                    <Route path="/docs/ai-obs" element={<DocsPage />} />
                    {/* Legacy alias, keep so bookmarks to /demos still work. */}
                    <Route path="/demos" element={<AuditPage />} />
                    {/* Reachable from UserMenu → Settings. Same Setup
                        component the gate renders, but in-layout (so the
                        TopBar stays visible) and persistent (the gate
                        unmounts it once setup is complete). */}
                    <Route path="/setup" element={<SettingsRoute />} />
                  </Route>
                </Routes>
                </AssistantProvider>
              </PipelineProvider>
            </SetupGate>
          </ToastProvider>
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
