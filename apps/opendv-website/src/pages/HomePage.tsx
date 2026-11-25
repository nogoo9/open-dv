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
			title="open-dv — Agent-First 7-Step Data Vault 2.0 Workbench & 11-Target Compiler"
			description="Open-source Apache-2.0 Agent-first 7-Step Guided ⇄ Observatory Data Vault 2.0 Workbench, 4-View DV Schema Explorer, Run Explorer, L1 + V1–V8 CEGAR verifier, and 11-target 5-Zone (Z0-Z4) compiler."
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
