/**
 * Layout, UI Drawers, Search Filtering, and Toast Notifications Slice
 * (apps/opendv-web-workbench/src/ontology/store/layout-slice.ts)
 */

import type { StoreGet, StoreSet } from "./store-types";
import type { LayoutState, StudioErrorToast } from "./types";

export function createDefaultLayoutState(): LayoutState {
	return {
		leftDrawerTab: "sessions",
		leftDrawerOpen: true,
		rightDrawerOpen: false,
		rightDrawerTab: "concept",
		bottomDockOpen: false,
		activeModal: null,
		lodTier: 2,
		minimapVisible: true,
		searchTerm: "",
	};
}

export function createLayoutSlice(set: StoreSet, _get: StoreGet) {
	return {
		layout: createDefaultLayoutState(),
		sseEvents: [],
		toasts: [] as StudioErrorToast[],

		setLeftDrawerTab: (tab: LayoutState["leftDrawerTab"]) =>
			set((state) => ({
				layout: { ...state.layout, leftDrawerTab: tab, leftDrawerOpen: true },
			})),

		setRightDrawerTab: (tab: NonNullable<LayoutState["rightDrawerTab"]>) =>
			set((state) => ({
				selectedProposalId:
					tab === "proposal" &&
					!state.selectedProposalId &&
					state.proposals.length > 0
						? (state.proposals[0]?.proposal_id ?? null)
						: state.selectedProposalId,
				layout: {
					...state.layout,
					rightDrawerTab: tab,
					rightDrawerOpen: true,
				},
			})),

		toggleLeftDrawer: (open?: boolean) =>
			set((state) => ({
				layout: {
					...state.layout,
					leftDrawerOpen:
						open !== undefined ? open : !state.layout.leftDrawerOpen,
				},
			})),

		toggleRightDrawer: (open?: boolean) =>
			set((state) => ({
				layout: {
					...state.layout,
					rightDrawerOpen:
						open !== undefined ? open : !state.layout.rightDrawerOpen,
				},
			})),

		toggleBottomDock: (open?: boolean) =>
			set((state) => ({
				layout: {
					...state.layout,
					bottomDockOpen:
						open !== undefined ? open : !state.layout.bottomDockOpen,
				},
			})),

		setActiveModal: (modal: LayoutState["activeModal"]) =>
			set((state) => ({
				layout: { ...state.layout, activeModal: modal },
			})),

		setLodTier: (tier: 1 | 2 | 3) =>
			set((state) => ({
				layout: { ...state.layout, lodTier: tier },
			})),

		setMinimapVisible: (visible: boolean) =>
			set((state) => ({
				layout: { ...state.layout, minimapVisible: visible },
			})),

		setSearchTerm: (term: string) =>
			set((state) => ({
				layout: { ...state.layout, searchTerm: term },
			})),

		triggerRelayout: () =>
			set((state) => ({
				layout: {
					...state.layout,
					layoutTriggerCount: (state.layout.layoutTriggerCount || 0) + 1,
				},
			})),

		addToast: (toast: Omit<StudioErrorToast, "id" | "timestamp">) => {
			const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
			const newToast: StudioErrorToast = {
				...toast,
				id,
				timestamp: new Date().toISOString(),
			};
			set((state) => ({
				toasts: [newToast, ...state.toasts].slice(0, 8),
			}));
		},

		dismissToast: (id: string) =>
			set((state) => ({
				toasts: state.toasts.filter((t) => t.id !== id),
			})),
	};
}
