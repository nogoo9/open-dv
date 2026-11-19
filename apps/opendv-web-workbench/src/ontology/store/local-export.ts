/**
 * Local multi-format ontology serialization (YAML, JSON, JCS, Turtle, JSON-LD, N-Triples, RDF/XML).
 * (apps/opendv-web-workbench/src/ontology/store/local-export.ts)
 */

import {
	canonicalizeGraphADict,
	dumpsJcs,
	serializeGraphAYaml,
} from "./jcs-export";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "./types";

export function escapeXml(str?: string | null): string {
	const sanitized = Array.from(str ?? "")
		.filter((ch) => {
			const cp = ch.codePointAt(0) ?? 0;
			return (
				cp === 0x09 ||
				cp === 0x0a ||
				cp === 0x0d ||
				(cp >= 0x20 && cp <= 0xd7ff) ||
				(cp >= 0xe000 && cp <= 0xfffd) ||
				(cp >= 0x10000 && cp <= 0x10ffff)
			);
		})
		.join("");
	return sanitized
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&apos;");
}

export function escapeTurtleString(str?: string | null): string {
	return Array.from(str ?? "", (ch) => {
		if (ch === "\\") return "\\\\";
		if (ch === '"') return '\\"';
		if (ch === "\n") return "\\n";
		if (ch === "\r") return "\\r";
		if (ch === "\t") return "\\t";
		if (ch === "\b") return "\\b";
		if (ch === "\f") return "\\f";
		const cp = ch.codePointAt(0) ?? 0;
		if (cp < 0x20 || cp === 0x7f) {
			return `\\u${cp.toString(16).toUpperCase().padStart(4, "0")}`;
		}
		return ch;
	}).join("");
}

export function escapeYamlString(str?: string | null): string {
	return (str ?? "")
		.replace(/\\/g, "\\\\")
		.replace(/"/g, '\\"')
		.replace(/\r/g, "\\r")
		.replace(/\n/g, "\\n")
		.replace(/\t/g, "\\t");
}

export function escapeIri(iri?: string | null): string {
	const trimmed = (iri ?? "").trim();
	return Array.from(trimmed, (c) => {
		const cp = c.codePointAt(0) ?? 0;
		return cp <= 0x20 || '<>"{}|^`\\'.includes(c) || /\s/.test(c)
			? encodeURIComponent(c)
			: c;
	}).join("");
}

export function generateLocalExport(
	format: string,
	version: string,
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	session: StudioSessionSummary,
): string {
	const normalizedFormat = format.toLowerCase().trim();
	const validFormats = [
		"json",
		"turtle",
		"jsonld",
		"ntriples",
		"nt",
		"rdf",
		"yaml",
		"rfc8785",
		"jcs",
	];
	if (!validFormats.includes(normalizedFormat)) {
		throw new Error(
			`Unsupported export format '${format}'. Supported formats: ${validFormats.join(", ")}`,
		);
	}

	const rawDomain = session.domain_namespace || "general";
	const rawOwner = session.enterprise_owner || "Enterprise Architecture";
	const domain = escapeIri(rawDomain);

	switch (normalizedFormat) {
		case "rfc8785":
		case "jcs": {
			const canonical = canonicalizeGraphADict(nodes, edges, {
				...session,
				active_version: version,
			});
			return dumpsJcs(canonical, { excludeNone: true });
		}
		case "ntriples":
		case "nt": {
			const ontIri = `<urn:opendv:ontology:${domain}>`;
			const rdfType = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>";
			const owlOnt = "<http://www.w3.org/2002/07/owl#Ontology>";
			const owlClass = "<http://www.w3.org/2002/07/owl#Class>";
			const owlObjProp = "<http://www.w3.org/2002/07/owl#ObjectProperty>";
			const owlVer = "<http://www.w3.org/2002/07/owl#versionInfo>";
			const rdfsLabel = "<http://www.w3.org/2000/01/rdf-schema#label>";
			const rdfsComment = "<http://www.w3.org/2000/01/rdf-schema#comment>";
			const rdfsDomain = "<http://www.w3.org/2000/01/rdf-schema#domain>";
			const rdfsRange = "<http://www.w3.org/2000/01/rdf-schema#range>";
			const lines: string[] = [
				`${ontIri} ${rdfType} ${owlOnt} .`,
				`${ontIri} ${owlVer} "${escapeTurtleString(version)}" .`,
			];
			for (const n of nodes) {
				const safeUri = `<${escapeIri(n.concept_uri)}>`;
				lines.push(`${safeUri} ${rdfType} ${owlClass} .`);
				lines.push(
					`${safeUri} ${rdfsLabel} "${escapeTurtleString(n.concept_name)}" .`,
				);
				lines.push(
					`${safeUri} ${rdfsComment} "${escapeTurtleString(n.definition)}" .`,
				);
			}
			for (const e of edges) {
				const safeUri = `<${escapeIri(e.relationship_uri)}>`;
				lines.push(`${safeUri} ${rdfType} ${owlObjProp} .`);
				lines.push(
					`${safeUri} ${rdfsLabel} "${escapeTurtleString(e.relationship_name)}" .`,
				);
				lines.push(
					`${safeUri} ${rdfsDomain} <${escapeIri(e.source_concept_uri)}> .`,
				);
				lines.push(
					`${safeUri} ${rdfsRange} <${escapeIri(e.target_concept_uri)}> .`,
				);
			}
			return lines.join("\n");
		}
		case "json": {
			const canonical = canonicalizeGraphADict(nodes, edges, {
				...session,
				domain_namespace: rawDomain,
				enterprise_owner: rawOwner,
				active_version: version,
			});
			return dumpsJcs(canonical, { excludeNone: true });
		}
		case "turtle": {
			const lines = [
				"@prefix owl: <http://www.w3.org/2002/07/owl#> .",
				"@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
				"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
				"@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
				`@prefix opendv: <urn:opendv:ontology:${domain}#> .`,
				"",
				`<urn:opendv:ontology:${domain}> a owl:Ontology ;`,
				`    owl:versionInfo "${escapeTurtleString(version)}" .`,
				"",
			];
			for (const n of nodes) {
				const safeUri = escapeIri(n.concept_uri);
				lines.push(`<${safeUri}> a owl:Class ;`);
				lines.push(`    rdfs:label "${escapeTurtleString(n.concept_name)}" ;`);
				lines.push(`    rdfs:comment "${escapeTurtleString(n.definition)}" .`);
				lines.push("");
			}
			for (const e of edges) {
				const safeUri = escapeIri(e.relationship_uri);
				lines.push(`<${safeUri}> a owl:ObjectProperty ;`);
				lines.push(
					`    rdfs:label "${escapeTurtleString(e.relationship_name)}" ;`,
				);
				lines.push(`    rdfs:domain <${escapeIri(e.source_concept_uri)}> ;`);
				lines.push(`    rdfs:range <${escapeIri(e.target_concept_uri)}> .`);
				lines.push("");
			}
			return lines.join("\n");
		}
		case "jsonld": {
			const graph = [
				...nodes.map((n) => ({
					"@id": escapeIri(n.concept_uri),
					"@type": "owl:Class",
					"rdfs:label": n.concept_name,
					"rdfs:comment": n.definition,
				})),
				...edges.map((e) => ({
					"@id": escapeIri(e.relationship_uri),
					"@type": "owl:ObjectProperty",
					"rdfs:label": e.relationship_name,
					"rdfs:domain": { "@id": escapeIri(e.source_concept_uri) },
					"rdfs:range": { "@id": escapeIri(e.target_concept_uri) },
				})),
			];
			return JSON.stringify(
				{
					"@context": {
						owl: "http://www.w3.org/2002/07/owl#",
						rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
						rdfs: "http://www.w3.org/2000/01/rdf-schema#",
					},
					"@graph": graph,
				},
				null,
				2,
			);
		}
		case "rdf": {
			const lines = [
				'<?xml version="1.0" encoding="utf-8"?>',
				'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:owl="http://www.w3.org/2002/07/owl#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">',
				`  <owl:Ontology rdf:about="urn:opendv:ontology:${escapeXml(domain)}">`,
				`    <owl:versionInfo>${escapeXml(version)}</owl:versionInfo>`,
				"  </owl:Ontology>",
			];
			for (const n of nodes) {
				lines.push(
					`  <owl:Class rdf:about="${escapeXml(escapeIri(n.concept_uri))}">`,
				);
				lines.push(`    <rdfs:label>${escapeXml(n.concept_name)}</rdfs:label>`);
				lines.push(
					`    <rdfs:comment>${escapeXml(n.definition)}</rdfs:comment>`,
				);
				lines.push("  </owl:Class>");
			}
			for (const e of edges) {
				lines.push(
					`  <owl:ObjectProperty rdf:about="${escapeXml(escapeIri(e.relationship_uri))}">`,
				);
				lines.push(
					`    <rdfs:label>${escapeXml(e.relationship_name)}</rdfs:label>`,
				);
				lines.push(
					`    <rdfs:domain rdf:resource="${escapeXml(escapeIri(e.source_concept_uri))}"/>`,
				);
				lines.push(
					`    <rdfs:range rdf:resource="${escapeXml(escapeIri(e.target_concept_uri))}"/>`,
				);
				lines.push("  </owl:ObjectProperty>");
			}
			lines.push("</rdf:RDF>");
			return lines.join("\n");
		}
		default: {
			const canonical = canonicalizeGraphADict(nodes, edges, {
				...session,
				domain_namespace: rawDomain,
				enterprise_owner: rawOwner,
				active_version: version,
			});
			return serializeGraphAYaml(canonical);
		}
	}
}
