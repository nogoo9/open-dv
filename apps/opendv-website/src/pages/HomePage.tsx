import type React from "react";
import { DocumentShell } from "../components/Layout";
import { EcosystemCliSection } from "../sections/EcosystemCliSection";
import { HeroSection } from "../sections/HeroSection";
import { HowItWorksSection } from "../sections/HowItWorksSection";
import { QuickStartSection } from "../sections/QuickStartSection";
import { VerifierDeepDiveSection } from "../sections/VerifierDeepDiveSection";
import { VideoTourSection } from "../sections/VideoTourSection";
import { WhyOpenDvSection } from "../sections/WhyOpenDvSection";
import { WorkbenchShowcaseSection } from "../sections/WorkbenchShowcaseSection";

export function HomePage({
	basePath,
}: {
	readonly basePath?: string | undefined;
}): React.JSX.Element {
	return (
		<DocumentShell
			title="open-dv v6.0 — Neuro-Symbolic Data Vault 2.0 Verified Before One SQL Line Runs"
			description="Open-source Apache-2.0 neuro-symbolic Data Vault 2.0 modelling, 6-gate axi-dv verification, DuckDB shadow sandbox, Ontology Studio, and multi-dialect SQL compiler."
			canonicalPath="/"
			activeRoute="home"
			{...(basePath !== undefined ? { basePath } : {})}
		>
			<HeroSection />
			<WhyOpenDvSection />
			<HowItWorksSection />
			<VerifierDeepDiveSection />
			<WorkbenchShowcaseSection
				{...(basePath !== undefined ? { basePath } : {})}
			/>
			<VideoTourSection {...(basePath !== undefined ? { basePath } : {})} />
			<EcosystemCliSection />
			<QuickStartSection />
		</DocumentShell>
	);
}
