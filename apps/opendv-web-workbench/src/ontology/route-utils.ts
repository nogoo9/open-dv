/**
 * Route resolution utilities for Open-DV Web Workbench / Ontology Visual Studio SPA.
 * Conforms to Task 5D.1 (#222).
 */

export function isOntologyRoute(pathname?: string): boolean {
	if (pathname !== undefined) {
		return pathname === "/ontology" || pathname.startsWith("/ontology/");
	}
	if (typeof globalThis !== "undefined" && "location" in globalThis) {
		const currentPath = globalThis.location?.pathname ?? "";
		return currentPath === "/ontology" || currentPath.startsWith("/ontology/");
	}
	return false;
}
