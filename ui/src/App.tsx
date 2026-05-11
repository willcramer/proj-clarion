import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Layout } from "@/components/Layout";
import { SetupGate } from "@/components/SetupGate";
import { ToastProvider } from "@/components/Toast";
import { DashboardPage } from "@/pages/Dashboard";
import { ProfilesListPage, ProfileDetailPage } from "@/pages/Profiles";
import { PlansListPage, PlanDetailPage } from "@/pages/Plans";
import { RunsPage } from "@/pages/Runs";
import { NewDemoPage } from "@/pages/NewDemo";
import { PipelinesPage } from "@/pages/Pipelines";
import { SettingsRoute } from "@/pages/SettingsRoute";
import { PipelineProvider } from "@/lib/PipelineContext";
import { ThemeProvider } from "@/lib/ThemeContext";

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      // SE-facing dashboard — staleness OK, but refetch on focus so they
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
             * the gate transparently renders its children — including
             * the explicit `/setup` route that's reachable from the
             * UserMenu for token rotation / re-upload.
             */}
            <SetupGate>
              <PipelineProvider>
                <Routes>
                  <Route element={<Layout />}>
                    <Route index element={<DashboardPage />} />
                    <Route path="/new" element={<NewDemoPage />} />
                    <Route path="/profiles" element={<ProfilesListPage />} />
                    <Route path="/profiles/:profileId" element={<ProfileDetailPage />} />
                    <Route path="/plans" element={<PlansListPage />} />
                    <Route path="/plans/:planId" element={<PlanDetailPage />} />
                    <Route path="/runs" element={<RunsPage />} />
                    <Route path="/pipelines" element={<PipelinesPage />} />
                    {/* Reachable from UserMenu → Settings. Same Setup
                        component the gate renders, but in-layout (so the
                        TopBar stays visible) and persistent (the gate
                        unmounts it once setup is complete). */}
                    <Route path="/setup" element={<SettingsRoute />} />
                  </Route>
                </Routes>
              </PipelineProvider>
            </SetupGate>
          </ToastProvider>
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
