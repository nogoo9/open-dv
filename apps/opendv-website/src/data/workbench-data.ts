import { EXPLORER_SCREENS } from "./explorer-screens-data";
import type { WorkbenchScreen } from "./types";
import { WIZARD_SCREENS } from "./wizard-screens-data";

export const WORKBENCH_SCREENS: readonly WorkbenchScreen[] = [
	...WIZARD_SCREENS,
	...EXPLORER_SCREENS,
];
