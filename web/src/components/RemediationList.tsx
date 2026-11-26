import { useState } from "react";
import type { ErrorRemediation, ProblemDetails } from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export interface RemediationListProps {
	remediation?: ErrorRemediation | null;
	problem?: ProblemDetails | null;
}

export function RemediationList({
	remediation,
	problem,
}: RemediationListProps) {
	const announce = useProjectStore((s) => s.announce);
	const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
	const rem = remediation ?? problem?.remediation ?? null;

	if (!rem || (!rem.summary && !(rem.actions?.length ?? 0))) return null;

	const copyCmd = async (cmd: string, idx: number) => {
		try {
			if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
				await navigator.clipboard.writeText(cmd);
			}
			setCopiedIdx(idx);
			announce("polite", `Copied command: ${cmd}`);
		} catch {
			announce("polite", `Copy failed for command: ${cmd}`);
		}
	};

	return (
		<div className="stack-sm">
			{rem.summary && <div className="text-sm">{rem.summary}</div>}
			{(rem.actions ?? []).map((act, idx) => (
				<div
					key={`${act.actor}-${act.label}-${idx}`}
					className="card-subtle stack-sm"
				>
					<div className="row-between">
						<div className="row">
							<span
								className={`pill ${act.actor === "agent" ? "pill-sal" : "pill-warn"}`}
							>
								{act.actor === "agent" ? "Agent" : "Human"}
							</span>
							<span className="text-sm font-semibold">{act.label}</span>
							{act.requires_tty && (
								<span className="pill">Interactive terminal</span>
							)}
						</div>
						{act.command && (
							<button
								type="button"
								className="btn btn-sm"
								onClick={() => void copyCmd(act.command ?? "", idx)}
							>
								{copiedIdx === idx ? "Copied" : "Copy"}
							</button>
						)}
					</div>
					{act.command && (
						<code
							className="mono text-sm"
							style={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
						>
							{act.command}
						</code>
					)}
				</div>
			))}
		</div>
	);
}
