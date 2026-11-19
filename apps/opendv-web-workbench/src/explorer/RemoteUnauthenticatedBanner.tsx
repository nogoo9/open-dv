/**
 * Remote Unauthenticated Mode Coral Banner (`RemoteUnauthenticatedBanner.tsx`, FR-AUTH-04, D18).
 */

import { CLAUDE_TOKENS, EXPLORER_FONTS } from "./design-tokens";

export interface RemoteUnauthenticatedBannerProps {
	visible: boolean;
}

export function RemoteUnauthenticatedBanner({
	visible,
}: RemoteUnauthenticatedBannerProps) {
	if (!visible) return null;

	return (
		<div
			role="alert"
			data-testid="remote-unauthenticated-banner"
			style={{
				padding: "8px 24px",
				backgroundColor: CLAUDE_TOKENS.colors.error,
				borderBottom: `2px solid ${CLAUDE_TOKENS.colors.primary}`,
				color: CLAUDE_TOKENS.colors.onPrimary,
				fontFamily: EXPLORER_FONTS.sans,
				fontSize: "12.5px",
				fontWeight: 500,
				display: "flex",
				alignItems: "center",
				justifyContent: "space-between",
				gap: "12px",
			}}
		>
			<span>
				<strong>Unauthenticated Remote Mode:</strong> Server launched with{" "}
				<code>--allow-remote-unauthenticated</code> on a non-loopback host.
				Restrict network exposure to trusted peers only.
			</span>
		</div>
	);
}
