import { getStudioAuthHeaders } from "../../ontology/store/studio-store";

export interface SyncOntologyDeltaOptions {
	sessionId: string;
	apiUrl: string;
	fetchImpl: typeof fetch;
	operations: Array<Record<string, unknown>>;
	onSyncOntologyDelta?: ((sessionId: string) => Promise<void>) | undefined;
}

export interface SyncResult {
	success: boolean;
	message?: string;
	error?: string;
}

export async function executeSyncOntologyDelta(
	opts: SyncOntologyDeltaOptions,
): Promise<SyncResult> {
	const { sessionId, apiUrl, fetchImpl, operations, onSyncOntologyDelta } = opts;
	if (onSyncOntologyDelta) {
		await onSyncOntologyDelta(sessionId);
		return {
			success: true,
			message: `Ontology Deltas synced to Studio Session ${sessionId}!`,
		};
	}

	let headSha = "";
	const existingConceptUris = new Set<string>();
	const existingHubs = new Set<string>();
	const existingRelUris = new Set<string>();

	try {
		const expUrl = `${apiUrl}/ontology/studio/sessions/${encodeURIComponent(sessionId)}/export?export_format=json`;
		const expRes = await fetchImpl(
			expUrl,
			{ credentials: "include", headers: getStudioAuthHeaders(undefined, expUrl) },
		);
		if (expRes.ok) {
			const ontData = (await expRes.json()) as any;
			const concepts = ontData?.canonical_business_concepts || ontData?.concepts || [];
			for (const c of concepts) {
				if (c.concept_uri) existingConceptUris.add(String(c.concept_uri).toLowerCase());
				if (c.target_hub) existingHubs.add(String(c.target_hub).toLowerCase());
				if (c.concept_name) {
					existingConceptUris.add(String(c.concept_name).toLowerCase().replace(/ /g, "_"));
				}
			}
			const rels = ontData?.canonical_relationships || ontData?.relationships || [];
			for (const r of rels) {
				if (r.relationship_uri) existingRelUris.add(String(r.relationship_uri).toLowerCase());
				if (r.relationship_id) existingRelUris.add(String(r.relationship_id).toLowerCase());
				if (r.relationship_name) {
					existingRelUris.add(String(r.relationship_name).toLowerCase().replace(/ /g, "_"));
				}
			}
		}
	} catch {
		// Proceed without exported ontology concepts
	}

	try {
		const sessionUrl = `${apiUrl}/ontology/studio/sessions/${encodeURIComponent(sessionId)}`;
		const sRes = await fetchImpl(
			sessionUrl,
			{ credentials: "include", headers: getStudioAuthHeaders(undefined, sessionUrl) },
		);
		if (sRes.ok) {
			const sData = (await sRes.json()) as { head_sha256?: string };
			headSha = sData.head_sha256 || "";
		}
	} catch {
		// Proceed with empty headSha
	}

	const filteredOperations = operations.filter((rawOp: any) => {
		const rawType = (rawOp.op_type || rawOp.op || rawOp.action || "add_concept").toString().toLowerCase();
		let opType = rawType;
		if (opType.endsWith("op")) {
			opType = opType.slice(0, -2);
		}
		if (opType === "add_concept_attribute") {
			opType = "add_attribute";
		}

		if (opType === "add_attribute" || opType.includes("attribute")) {
			// Never filter out add_attribute because its parent concept exists!
			return true;
		}

		if (opType === "add_relationship" || opType.includes("relationship") || opType.includes("link")) {
			const rawRelUri = String(rawOp.relationship_uri || rawOp.relationship_id || rawOp.uri || rawOp.name || "").toLowerCase();
			const relSlug = rawRelUri.split(":").pop() || "";
			if (
				(rawRelUri && existingRelUris.has(rawRelUri)) ||
				(relSlug && existingRelUris.has(relSlug))
			) {
				return false;
			}
			return true;
		}

		// Deduplicate ONLY add_concept (by concept URI/hub)
		const rawUri = String(rawOp.concept_uri || rawOp.concept_id || rawOp.uri || "").toLowerCase();
		const rawHub = String(rawOp.target_hub || "").toLowerCase();
		const slug = rawUri.split(":").pop() || "";
		const slugNorm = slug.replace(/s$/, "");
		if (
			(rawUri && existingConceptUris.has(rawUri)) ||
			(rawHub && existingHubs.has(rawHub)) ||
			(slug && existingConceptUris.has(slug)) ||
			(slugNorm && existingConceptUris.has(slugNorm)) ||
			(slugNorm && existingHubs.has(`hub_${slugNorm}`))
		) {
			return false;
		}
		return true;
	});

	if (filteredOperations.length === 0) {
		return {
			success: false,
			error: "No new operations to sync (all operations already exist in session)",
		};
	}

	const formattedOps = filteredOperations.map((rawOp: any, idx: number) => {
		const op = { ...rawOp };
		const rawType = (op.op_type || op.op || op.action || "add_concept").toString();
		let opType = rawType.toLowerCase();
		if (opType.endsWith("op")) {
			opType = opType.slice(0, -2);
		}
		if (opType === "add_concept_attribute") {
			opType = "add_attribute";
		}

		if (opType === "add_attribute" || opType.includes("attribute")) {
			const rawConcept = op.concept_uri || op.concept_id || op.concept || op.target_concept || op.entity || "concept";
			const slug = rawConcept.split(":").pop() || "concept";
			return {
				op_type: "add_attribute",
				op_id: op.op_id || `op-attr-${slug}-${idx}`,
				concept_uri: rawConcept.startsWith("urn:ontology:entity:") || rawConcept.startsWith("urn:opendv:")
					? rawConcept
					: `urn:ontology:entity:${slug}`,
				attribute_name: op.attribute_name || op.attribute || op.name || "attribute",
				data_type: op.data_type || "string",
				description: op.description || "Ontology attribute evolution delta",
				is_optional: op.is_optional ?? true,
			};
		}

		if (opType === "add_relationship" || opType.includes("relationship") || opType.includes("link")) {
			const rawRel = op.relationship_uri || op.relationship_id || op.name || "rel";
			const slug = rawRel.split(":").pop() || "rel";
			return {
				op_type: "add_relationship",
				op_id: op.op_id || `op-rel-${slug}-${idx}`,
				relationship_uri: rawRel.startsWith("urn:ontology:relationship:") || rawRel.startsWith("urn:opendv:")
					? rawRel
					: `urn:ontology:relationship:${slug}`,
				relationship_name: op.relationship_name || op.label || slug,
				target_link: op.target_link || (slug.startsWith("lnk_") ? slug : `lnk_${slug}`),
				...(Array.isArray(op.endpoints) && op.endpoints.length > 0 ? { endpoints: op.endpoints } : {}),
			};
		}

		const rawConcept = op.concept_uri || op.concept_id || op.concept || op.name || "concept";
		const slug = rawConcept.split(":").pop() || "concept";
		return {
			op_type: "add_concept",
			op_id: op.op_id || `op-concept-${slug}-${idx}`,
			concept_uri: rawConcept.startsWith("urn:ontology:entity:") || rawConcept.startsWith("urn:opendv:")
				? rawConcept
				: `urn:ontology:entity:${slug}`,
			concept_name: op.concept_name || op.label || slug.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()),
			target_hub: op.target_hub || (slug.startsWith("hub_") ? slug : `hub_${slug}`),
		};
	});

	const deltaUrl = `${apiUrl}/ontology/studio/sessions/${encodeURIComponent(sessionId)}/delta`;
	const res = await fetchImpl(
		deltaUrl,
		{
			method: "POST",
			credentials: "include",
			headers: getStudioAuthHeaders({
				"Content-Type": "application/json",
				"X-OpenDV-Role": "admin",
				...(headSha ? { "If-Match": `"${headSha}"` } : {}),
			}, deltaUrl),
			body: JSON.stringify({
				operations: formattedOps,
				grounding_rationale: "Automated pipeline builder delta sync",
				low_confidence_waiver: true,
			}),
		},
	);
	if (!res.ok) {
		const errBody = await res.text().catch(() => "");
		return {
			success: false,
			error: `Sync failed: HTTP ${res.status}${errBody ? ` - ${errBody}` : ""}`,
		};
	}
	return {
		success: true,
		message: `Ontology Deltas synced to Studio Session ${sessionId}!`,
	};
}
