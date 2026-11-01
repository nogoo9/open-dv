import { PROTOCOL_VERSION } from "@opendv/protocol-ts";

export function getWorkbenchVersion(): string {
	return `Open-DV Web Workbench (Protocol ${PROTOCOL_VERSION})`;
}
