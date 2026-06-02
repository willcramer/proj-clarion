/**
 * Profile discovery deliverable — a clean, shareable artifact of Clarion's
 * research on a company, separate from the app's profile detail page (which
 * has live/app-only controls). This is what you hand to someone NOT using
 * Clarion: an AE prepping a discovery call, an account team, another AI
 * assistant, or pipeline-growth work.
 *
 * The body is the same self-contained HTML used for the PDF export (rendered
 * in an iframe so the preview is exactly what prints). "Copy for AI / notes"
 * yields clean Markdown for pasting elsewhere. Hidden route — reached from a
 * button on the profile page (wired separately).
 */
import { useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileDown, Copy, Check, Download, ArrowLeft, Loader2 } from "lucide-react";

import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { getProfile } from "@/lib/api";
import {
  buildDiscoveryBriefHtml,
  buildDiscoveryBriefMarkdown,
  downloadDiscoveryBrief,
  printDeliverable,
  type BriefProfile,
} from "@/lib/discoveryBrief";

function hostOf(url?: string): string {
  if (!url) return "";
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

export function ProfileDeliverablePage() {
  const { profileId = "" } = useParams<{ profileId: string }>();
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);

  const profileQ = useQuery({
    queryKey: ["profile", profileId],
    queryFn: () => getProfile(profileId),
    enabled: !!profileId,
  });

  const profile = profileQ.data as BriefProfile | undefined;
  const html = useMemo(() => (profile ? buildDiscoveryBriefHtml(profile) : ""), [profile]);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const name = profile?.company?.name || hostOf(profile?.company?.primary_url) || "Profile";

  async function copyForAi() {
    if (!profile) return;
    try {
      await navigator.clipboard.writeText(buildDiscoveryBriefMarkdown(profile));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      window.alert("Couldn't copy to clipboard — your browser may be blocking clipboard access on this page.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Discovery deliverable"
        title={name}
        em="discovery brief."
        lede="A clean, shareable summary of Clarion's research — hand it to an AE for a discovery call, drop it into meeting notes, or paste it into another AI assistant. No app chrome, just the findings."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate(`/profiles/${profileId}`)}>
              <ArrowLeft size={14} /> Back to profile
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!profile}
              onClick={() => profile && downloadDiscoveryBrief(profile)}
            >
              <Download size={14} /> Download .html
            </Button>
            <Button variant="secondary" size="sm" disabled={!profile} onClick={copyForAi}>
              {copied ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy for AI / notes</>}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!profile}
              onClick={() => printDeliverable(iframeRef.current, html)}
            >
              <FileDown size={14} /> Export as PDF
            </Button>
          </div>
        }
      />

      {profileQ.isLoading ? (
        <Card className="grid place-items-center p-12 text-[var(--color-text-muted)]">
          <Loader2 className="animate-spin" size={20} />
        </Card>
      ) : !profile ? (
        <Card className="p-12 text-center text-[var(--color-text-muted)]">
          Couldn't load this profile.{" "}
          <button
            type="button"
            className="text-[var(--color-accent)] hover:underline"
            onClick={() => navigate("/profiles")}
          >
            Back to profiles
          </button>
        </Card>
      ) : (
        <Card className="p-3 sm:p-4">
          <iframe
            ref={iframeRef}
            title={`${name} — discovery brief preview`}
            srcDoc={html}
            className="w-full rounded-lg border border-[var(--color-border)] bg-white"
            style={{ height: "1180px" }}
          />
        </Card>
      )}
    </div>
  );
}
