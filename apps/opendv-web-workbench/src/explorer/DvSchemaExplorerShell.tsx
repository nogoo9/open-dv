/**
 * M4 Shell View for Tab 2 — DV Schema Explorer (`/dv-schema`).
 * Delegates directly to the 4-view `DvSchemaExplorerView` while preserving props compatibility.
 */

import { DvSchemaExplorerView } from "./dv-schema/DvSchemaExplorerView";

export interface DvSchemaExplorerShellProps {
	runId?: string | null | undefined;
	compareRunId?: string | null | undefined;
	sessionId?: string | null | undefined;
	onGoPipeline: () => void;
	onGoOntology: () => void;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
}

export function DvSchemaExplorerShell(props: DvSchemaExplorerShellProps) {
	return <DvSchemaExplorerView {...props} />;
}
