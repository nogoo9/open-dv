/**
 * Artifact Visualizer Router & Dispatcher (User Directive R9 Modularity).
 *
 * Dispatches to specialized ontology visualizers and standard visual renderers.
 */

import type { Artifact, HoldResolutionParams } from "@opendv/protocol-ts";
import { ChecksVisualizer } from "./ChecksVisualizer";
import { CodeVisualizer } from "./CodeVisualizer";
import { DocumentVisualizer } from "./DocumentVisualizer";
import { DvModelVisualizer } from "./DvModelVisualizer";
import { EventsVisualizer } from "./EventsVisualizer";
import { KeyValueVisualizer } from "./KeyValueVisualizer";
import { OntologyCoverageVisualizer } from "./OntologyCoverageVisualizer";
import { OntologyDiffVisualizer } from "./OntologyDiffVisualizer";
import { OntologyProposalVisualizer } from "./OntologyProposalVisualizer";
import { OntologyVisualizer } from "./OntologyVisualizer";
import { TableVisualizer } from "./TableVisualizer";

export * from "./ChecksVisualizer";
export * from "./CodeVisualizer";
export * from "./DocumentVisualizer";
export * from "./DvModelVisualizer";
export * from "./EventsVisualizer";
export * from "./KeyValueVisualizer";
export * from "./OntologyCoverageVisualizer";
export * from "./OntologyDiffVisualizer";
export * from "./OntologyProposalVisualizer";
export * from "./OntologyVisualizer";
export * from "./TableVisualizer";

export interface ArtifactVisualizerProps {
	artifact: Artifact;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
}

export function ArtifactVisualizer({
	artifact,
	onResolveHold,
}: ArtifactVisualizerProps) {
	// 0a. Specialized: Ontology Grounding & Coverage Visualizer (Phase P7 / P9)
	if (
		artifact.type === "ontology_coverage" ||
		(artifact.viz as string) === "ontology_coverage" ||
		artifact.id === "ontology_cov" ||
		artifact.id.includes("coverage") ||
		artifact.name.endsWith("ontology_coverage.json")
	) {
		return <OntologyCoverageVisualizer artifact={artifact} />;
	}

	// 0b. Specialized: Ontology Delta Proposal Visualizer (Phase P7 / P9)
	if (
		artifact.type === "ontology_proposal" ||
		(artifact.viz as string) === "ontology_proposal" ||
		artifact.id === "ontology_prop" ||
		artifact.id.includes("proposal") ||
		artifact.name.endsWith("ontology_proposal.json")
	) {
		return <OntologyProposalVisualizer artifact={artifact} />;
	}

	// 0c. Specialized: Ontology Evolution Diff Visualizer (Phase P7 / P9)
	if (
		artifact.type === "ontology_diff" ||
		(artifact.viz as string) === "ontology_diff" ||
		artifact.id === "ontology_diff_art" ||
		artifact.id.includes("diff") ||
		artifact.name.endsWith("ontology_diff.json")
	) {
		return <OntologyDiffVisualizer artifact={artifact} />;
	}

	// 1. viz === 'ontology'
	if (artifact.viz === "ontology" && artifact.ontology) {
		return <OntologyVisualizer ontology={artifact.ontology} />;
	}

	// 2. viz === 'dv'
	if (artifact.viz === "dv") {
		return <DvModelVisualizer artifact={artifact} />;
	}

	// 3. viz === 'table'
	if (artifact.viz === "table" && artifact.cols && artifact.rows) {
		return (
			<TableVisualizer
				name={artifact.name}
				cols={artifact.cols}
				rows={artifact.rows}
				foot={artifact.foot}
			/>
		);
	}

	// 4. viz === 'kv'
	if (artifact.viz === "kv" && artifact.kv) {
		return <KeyValueVisualizer kv={artifact.kv} />;
	}

	// 5. viz === 'checks'
	if (artifact.viz === "checks" && artifact.checks) {
		return <ChecksVisualizer checks={artifact.checks} />;
	}

	// 6. viz === 'events'
	if (artifact.viz === "events" && artifact.events) {
		return <EventsVisualizer events={artifact.events} />;
	}

	// 7. viz === 'doc'
	if (artifact.viz === "doc" && artifact.doc) {
		return (
			<DocumentVisualizer
				doc={artifact.doc}
				onResolveHold={onResolveHold}
			/>
		);
	}

	// 8. viz === 'code'
	if (artifact.viz === "code") {
		return (
			<CodeVisualizer
				raw={artifact.raw}
				outline={artifact.outline}
			/>
		);
	}

	return null;
}
