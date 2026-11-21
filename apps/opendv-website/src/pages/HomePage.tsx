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
			title="open-dv — Agent-First 6-Step Data Vault 2.0 Studio & 5-Zone Compiler"
			description="Open-source Apache-2.0 Agent-first 6-Step Data Vault 2.0 wizard, 4-View DV Schema Explorer, 6-gate axi-dv verifier, DuckDB shadow sandbox, and 5-Zone (Z0-Z4) SQL compiler."
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
