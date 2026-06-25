import type { Metadata } from "next";
import ResourcesClient from "./ResourcesClient";

export const metadata: Metadata = {
  title: "Community Resources — TokenAnalytics",
  description:
    "Curated guides, MCP servers, Claude Code hooks, skills, and workflow patterns for people building with AI coding agents.",
  alternates: { canonical: "https://tokenanalytics.app/resources" },
  robots: { index: true, follow: true },
};

export default function ResourcesPage() {
  return <ResourcesClient />;
}
