/**
 * Agent Harness Provider & Model Routing Settings Section (`HarnessModelSettingsSection.tsx`)
 *
 * Thin settings adapter delegating to shared `<AgentHarnessConfigCard />` (`P2`).
 */

import type {
	HarnessConfigDTO,
	HarnessProviderId,
	ProviderReadinessDTO,
} from "@opendv/protocol-ts";
import { AgentHarnessConfigCard } from "./AgentHarnessConfigCard";
import {
	type CanonicalPersonaId,
	createDefaultPlatformConfig,
} from "./usePlatformConfigActions";

export interface HarnessModelSettingsSectionProps {
	harness: HarnessConfigDTO;
	providers: readonly ProviderReadinessDTO[];
	resolvedPersonaModels: Readonly<Record<string, string>>;
	onProviderChange: (provider: HarnessProviderId) => void;
	onTierModelChange: (tier: "reasoning" | "fast", modelId: string) => void;
	onPersonaModelChange: (persona: CanonicalPersonaId, modelId: string) => void;
}

export function HarnessModelSettingsSection({
	harness,
	providers,
	resolvedPersonaModels,
	onProviderChange,
	onTierModelChange,
	onPersonaModelChange,
}: HarnessModelSettingsSectionProps) {
	const base = createDefaultPlatformConfig();
	return (
		<AgentHarnessConfigCard
			variant="settings"
			config={{
				...base,
				harness,
				providers: [...providers],
				resolved_persona_models: { ...resolvedPersonaModels },
			}}
			showActionBar={false}
			showPersonaRouting={true}
			onProviderChange={onProviderChange}
			onTierModelChange={onTierModelChange}
			onPersonaModelChange={onPersonaModelChange}
		/>
	);
}
