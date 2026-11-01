/**
 * Workbench Plugin Registry (`WorkbenchPluginRegistry`) for View Plugins (`WB-00`–`WB-11`)
 * and Co-Located Gate Card Plugins (`VIZ-GATE-*` & custom domain gate cards) (`docs/10` §3.3).
 */

import {
	ALL_WORKBENCH_VIEW_IDS,
	type VaultObservabilityEventDTO,
	type VizId,
	type WorkbenchViewId,
} from "@opendv/protocol-ts";
import type {
	WorkbenchGateCardPlugin,
	WorkbenchViewPlugin,
} from "./plugin-types";

export type PluginRegistryListener = () => void;

export class WorkbenchPluginRegistry {
	private readonly views = new Map<WorkbenchViewId, WorkbenchViewPlugin>();
	private readonly gateCards = new Map<string, WorkbenchGateCardPlugin>();
	private readonly listeners = new Set<PluginRegistryListener>();

	private validateAndNormalizeGateCard(
		plugin: WorkbenchGateCardPlugin,
	): WorkbenchGateCardPlugin {
		const trimmedGateId = plugin.gateId?.trim() ?? "";
		if (!trimmedGateId) {
			throw new Error(
				"WorkbenchGateCardPlugin must provide a non-empty gateId.",
			);
		}
		const trimmedTitle = plugin.title?.trim() ?? "";
		if (!trimmedTitle) {
			throw new Error(
				`WorkbenchGateCardPlugin '${trimmedGateId}' must provide a non-empty title.`,
			);
		}
		if (typeof plugin.component !== "function") {
			throw new Error(
				`WorkbenchGateCardPlugin '${trimmedGateId}' must provide a valid React component.`,
			);
		}

		return {
			...plugin,
			gateId: trimmedGateId,
			title: trimmedTitle,
			ruleIds: (plugin.ruleIds ?? []).map((r) => r.trim()).filter(Boolean),
		};
	}

	registerView(plugin: WorkbenchViewPlugin): WorkbenchViewPlugin {
		if (!plugin.id || !ALL_WORKBENCH_VIEW_IDS.includes(plugin.id)) {
			throw new Error(
				`Invalid WorkbenchViewPlugin id '${String(plugin.id)}'. Expected one of ${ALL_WORKBENCH_VIEW_IDS.join(", ")}.`,
			);
		}
		const trimmedTitle = plugin.title?.trim() ?? "";
		if (!trimmedTitle) {
			throw new Error(
				`WorkbenchViewPlugin '${plugin.id}' must define a non-empty title.`,
			);
		}
		if (typeof plugin.component !== "function") {
			throw new Error(
				`WorkbenchViewPlugin '${plugin.id}' must provide a valid React component.`,
			);
		}

		const normalizedGateCards = plugin.gateCards?.map((gateCard) =>
			this.validateAndNormalizeGateCard(gateCard),
		);

		const normalizedView: WorkbenchViewPlugin = {
			...plugin,
			title: trimmedTitle,
			shortTitle: plugin.shortTitle?.trim() || trimmedTitle,
			...(normalizedGateCards ? { gateCards: normalizedGateCards } : {}),
		};

		this.views.set(plugin.id, normalizedView);

		if (normalizedGateCards) {
			for (const gateCard of normalizedGateCards) {
				this.gateCards.set(gateCard.gateId, gateCard);
			}
		}

		this.notifyListeners();
		return normalizedView;
	}

	getView(id: WorkbenchViewId): WorkbenchViewPlugin | undefined {
		return this.views.get(id);
	}

	listViews(): WorkbenchViewPlugin[] {
		return Array.from(this.views.values()).sort(
			(a, b) =>
				ALL_WORKBENCH_VIEW_IDS.indexOf(a.id) -
				ALL_WORKBENCH_VIEW_IDS.indexOf(b.id),
		);
	}

	registerGateCard(plugin: WorkbenchGateCardPlugin): WorkbenchGateCardPlugin {
		const normalized = this.validateAndNormalizeGateCard(plugin);
		this.gateCards.set(normalized.gateId, normalized);
		this.notifyListeners();
		return normalized;
	}

	getGateCard(gateId: VizId | string): WorkbenchGateCardPlugin | undefined {
		return this.gateCards.get(gateId.trim());
	}

	listGateCards(): WorkbenchGateCardPlugin[] {
		return Array.from(this.gateCards.values());
	}

	findGateCardsForEvent(
		event: VaultObservabilityEventDTO,
	): WorkbenchGateCardPlugin[] {
		const eventVizId = event.viz_id.trim();
		const evaluated = new Set<string>(
			(event.rule_ids_evaluated ?? []).map((r) => r.trim()),
		);
		if (event.rule_code) {
			evaluated.add(event.rule_code.trim());
		}

		const matched: WorkbenchGateCardPlugin[] = [];
		for (const card of this.gateCards.values()) {
			const matchesViz = card.gateId === eventVizId;
			const matchesRule = card.ruleIds.some((r) => evaluated.has(r));
			if (matchesViz || matchesRule) {
				matched.push(card);
			}
		}
		return matched;
	}

	subscribe(listener: PluginRegistryListener): () => void {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	clearForTesting(): void {
		this.views.clear();
		this.gateCards.clear();
		this.notifyListeners();
	}

	private notifyListeners(): void {
		for (const listener of this.listeners) {
			listener();
		}
	}

	// Static façade delegating to defaultWorkbenchPluginRegistry for ergonomic global registration
	static registerView(plugin: WorkbenchViewPlugin): WorkbenchViewPlugin {
		return defaultWorkbenchPluginRegistry.registerView(plugin);
	}

	static getView(id: WorkbenchViewId): WorkbenchViewPlugin | undefined {
		return defaultWorkbenchPluginRegistry.getView(id);
	}

	static listViews(): WorkbenchViewPlugin[] {
		return defaultWorkbenchPluginRegistry.listViews();
	}

	static registerGateCard(
		plugin: WorkbenchGateCardPlugin,
	): WorkbenchGateCardPlugin {
		return defaultWorkbenchPluginRegistry.registerGateCard(plugin);
	}

	static getGateCard(
		gateId: VizId | string,
	): WorkbenchGateCardPlugin | undefined {
		return defaultWorkbenchPluginRegistry.getGateCard(gateId);
	}

	static listGateCards(): WorkbenchGateCardPlugin[] {
		return defaultWorkbenchPluginRegistry.listGateCards();
	}

	static findGateCardsForEvent(
		event: VaultObservabilityEventDTO,
	): WorkbenchGateCardPlugin[] {
		return defaultWorkbenchPluginRegistry.findGateCardsForEvent(event);
	}

	static subscribe(listener: PluginRegistryListener): () => void {
		return defaultWorkbenchPluginRegistry.subscribe(listener);
	}

	static clearForTesting(): void {
		defaultWorkbenchPluginRegistry.clearForTesting();
	}
}

export const defaultWorkbenchPluginRegistry = new WorkbenchPluginRegistry();
export const workbenchPluginRegistry = defaultWorkbenchPluginRegistry;
