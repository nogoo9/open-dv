import {
	CEGAR_POLICY_DEFAULTS,
	type CegarPolicy,
	type CertifiedVaultIR,
	type EvolutionLockSpec,
	type GuidedDecisionCard as GuidedDecisionCardDTO,
	type GuidedRunView,
	type GuidedTableExplainerSpec,
	type HorizonActionReq,
	type RunStateResp,
	type WaiverSubmitReq,
} from "../api/openapi";
import { GuidedDecisionCard } from "./GuidedDecisionCard";

type GuidedEntitySummary = NonNullable<GuidedRunView["entities"]>[number];

const POLICIES: readonly CegarPolicy[] = [
	"symbolic_only",
	"fast",
	"balanced",
	"strict",
];

export function Step6PolicyToolbar({
	run,
	policy,
	isLive,
	isDraining,
	busy,
	showHorizonReview,
	acceptBestDisabled,
	onSelectPolicy,
	onStart,
	onPause,
	onOpenCtrl,
	onResumeEmpty,
	onHorizonReview,
	onAcceptBest,
}: {
	run: RunStateResp | null;
	policy: CegarPolicy;
	isLive: boolean;
	isDraining: boolean;
	busy: boolean;
	showHorizonReview: boolean;
	acceptBestDisabled: boolean;
	onSelectPolicy: (p: CegarPolicy) => void;
	onStart: () => void;
	onPause: () => void;
	onOpenCtrl: (mode: "hint" | "raise-budget") => void;
	onResumeEmpty: () => void;
	onHorizonReview: () => void;
	onAcceptBest: () => void;
}) {
	const canRaise =
		run &&
		(run.status === "QUEUED" ||
			run.status === "RUNNING" ||
			run.status === "HOLD_CHECKPOINTED" ||
			run.status === "UNCERTIFIED_DRAFT");

	return (
		<>
			<div className="card-subtle row-between">
				<fieldset
					className="row"
					style={{ border: 0, padding: 0, margin: 0 }}
					disabled={busy || isLive}
				>
					<legend className="visually-hidden">Synthesis Policy</legend>
					{POLICIES.map((pol) => (
						<label key={pol} className="radio-row">
							<input
								type="radio"
								name="step6_policy"
								value={pol}
								checked={policy === pol}
								onChange={() => onSelectPolicy(pol)}
							/>
							<span>{CEGAR_POLICY_DEFAULTS[pol].label}</span>
						</label>
					))}
				</fieldset>

				<div className="row">
					<button
						type="button"
						className="btn btn-primary"
						disabled={busy || isLive || isDraining}
						onClick={onStart}
					>
						{run ? "▶ Start / Re-run" : "▶ Start Synthesis"}
					</button>
					{run && (run.status === "QUEUED" || run.status === "RUNNING") && (
						<>
							<button
								type="button"
								className="btn btn-secondary"
								disabled={busy || isDraining}
								onClick={onPause}
							>
								⏸ Pause
							</button>
							<button
								type="button"
								className="btn btn-secondary"
								disabled={busy || isDraining}
								onClick={() => onOpenCtrl("hint")}
							>
								💬 Hint
							</button>
						</>
					)}
					{canRaise && (
						<button
							type="button"
							className="btn btn-secondary"
							disabled={busy || isDraining}
							onClick={() => onOpenCtrl("raise-budget")}
						>
							📈 Raise Budget
						</button>
					)}
					{run?.status === "HOLD_CHECKPOINTED" &&
						(run.pending_questions?.length ?? 0) === 0 && (
							<button
								type="button"
								className="btn btn-primary"
								disabled={busy || isDraining}
								onClick={onResumeEmpty}
							>
								▶ Resume Synthesis →
							</button>
						)}
					{showHorizonReview && (
						<button
							type="button"
							className="btn btn-coral-fill"
							disabled={busy || isDraining}
							onClick={onHorizonReview}
						>
							Run Horizon Review
						</button>
					)}
				</div>
			</div>

			{run?.status === "UNCERTIFIED_DRAFT" && (
				<output className="banner banner-warn row-between">
					<span>
						Synthesis paused at budget or repair limit with an uncertified best
						draft.
					</span>
					<div className="row">
						<button
							type="button"
							className="btn btn-sm"
							disabled={busy || isDraining}
							onClick={() => onOpenCtrl("raise-budget")}
						>
							Raise Budget
						</button>
						<button
							type="button"
							className="btn btn-primary btn-sm"
							disabled={acceptBestDisabled}
							onClick={onAcceptBest}
						>
							✓ Accept Best
						</button>
					</div>
				</output>
			)}
		</>
	);
}

export function ExplainerSections({ exp }: { exp: GuidedTableExplainerSpec }) {
	return (
		<div className="stack-sm">
			<div className="card-cream stack-sm">
				<strong>1. Business Purpose</strong>
				<p className="text-sm">{exp.business_purpose}</p>
			</div>
			<div className="card-cream stack-sm">
				<strong>2. How History Is Protected</strong>
				<p className="text-sm">{exp.history_protection}</p>
			</div>
			<div className="card-cream stack-sm">
				<strong>3. Future Change Impact</strong>
				<p className="text-sm">
					<strong>Flexible Here:</strong> {exp.flexible_here}
				</p>
				<p className="text-sm">
					<strong>Lock-In Contrast:</strong> {exp.lock_in_contrast}
				</p>
			</div>
		</div>
	);
}

export function GuidedBlueprintPanel({
	entities,
	activeEntity,
	activeExplainer,
	isMobile,
	onSelectEntity,
	onOpenExplainer,
}: {
	entities: GuidedEntitySummary[];
	activeEntity: GuidedEntitySummary | null;
	activeExplainer: GuidedTableExplainerSpec | null;
	isMobile: boolean;
	onSelectEntity: (entityId: string) => void;
	onOpenExplainer: () => void;
}) {
	return (
		<div className="split-grid" style={{ gridTemplateColumns: "1.15fr 1fr" }}>
			<div className="card-subtle stack-sm">
				<h2 className="h2-serif">
					Plain-English Vault Blueprint ({entities.length})
				</h2>
				{entities.length === 0 ? (
					<p className="text-muted text-sm">
						Run Vault Synthesis or inspect an imported certified wave.
					</p>
				) : (
					<div className="stack-sm">
						{entities.map((ent) => (
							<button
								key={ent.entity_id}
								type="button"
								className={
									activeEntity?.entity_id === ent.entity_id
										? "card-selected stack-sm"
										: "card-cream stack-sm"
								}
								style={{ textAlign: "left" }}
								onClick={() => onSelectEntity(ent.entity_id)}
							>
								<div className="row-between">
									<strong>{ent.title}</strong>
									<span className="pill">{ent.badge_label}</span>
								</div>
								<span className="text-sm">{ent.summary}</span>
							</button>
						))}
					</div>
				)}
			</div>

			<div className="card-subtle stack-md">
				<div className="row-between">
					<h2 className="h2-serif">Why Does This Table Exist?</h2>
					{isMobile && activeExplainer && (
						<button
							type="button"
							className="btn btn-sm btn-secondary"
							onClick={onOpenExplainer}
						>
							Why Does This Table Exist?
						</button>
					)}
				</div>
				{!activeExplainer ? (
					<p className="text-muted text-sm">
						Select a modeled entity to read its plain-English explanation.
					</p>
				) : isMobile ? (
					<p className="text-sm text-muted">
						Tap &ldquo;Why Does This Table Exist?&rdquo; to open the 4-part
						explainer for {activeEntity?.title}.
					</p>
				) : (
					<ExplainerSections exp={activeExplainer} />
				)}
			</div>
		</div>
	);
}

export interface GuidedReadinessBriefProps {
	guidedView: GuidedRunView | null;
	run: RunStateResp | null;
	policy: CegarPolicy;
	vaultIr: CertifiedVaultIR | null;
	evolutionLocks?: EvolutionLockSpec | null;
	runsCount: number;
	v1V6FailCount?: number;
	v6bUnwaivedCount?: number;
	v7Score?: number | null;
	step7Unlocked: boolean;
	step7LockReason: string | null;
	busy?: boolean;
	onAcceptBest: () => void;
	onVerifyVault: () => void;
}

export function GuidedReadinessBrief({
	guidedView,
	run,
	policy,
	vaultIr,
	evolutionLocks,
	runsCount,
	v1V6FailCount = 0,
	v6bUnwaivedCount = 0,
	v7Score = null,
	step7Unlocked,
	step7LockReason,
	busy = false,
	onAcceptBest,
	onVerifyVault,
}: GuidedReadinessBriefProps) {
	if (runsCount === 0 && vaultIr) {
		return (
			<section
				className="card-subtle stack-md"
				aria-label="Imported Bundle Status"
			>
				<div className="row-between">
					<h2 className="h2-serif">Imported Certified Wave Status</h2>
					<span className="pill pill-ok">
						Wave {vaultIr.wave_number} Certified
					</span>
				</div>
				<div className="card-cream stack-sm text-sm">
					<div>
						<strong>Entities Modeled:</strong> {vaultIr.hubs?.length ?? 0} core
						entities, {vaultIr.links?.length ?? 0} relationships,{" "}
						{vaultIr.satellites?.length ?? 0} history tables
					</div>
					<div>
						<strong>Ontology Version:</strong> v{vaultIr.ontology_version}
					</div>
					{evolutionLocks && (
						<div>
							<strong>Active Locks:</strong>{" "}
							{Object.keys(evolutionLocks.locked_hub_bks ?? {}).length} business
							keys locked in Wave {evolutionLocks.wave_number}
						</div>
					)}
				</div>
				<div className="row-end">
					<button
						type="button"
						className="btn btn-secondary"
						disabled={busy}
						onClick={onVerifyVault}
					>
						✓ Verify Vault
					</button>
				</div>
			</section>
		);
	}

	const brief = guidedView?.readiness_brief;
	const strictBlocked =
		policy === "strict" &&
		(v6bUnwaivedCount > 0 || v7Score === null || v7Score < 95);
	const acceptBestDisabled =
		busy ||
		run?.status !== "UNCERTIFIED_DRAFT" ||
		v1V6FailCount > 0 ||
		strictBlocked;

	const isCertified =
		brief?.certification_label === "CERTIFIED" ||
		brief?.certification_label === "CERTIFIED_FAST" ||
		Boolean(vaultIr);
	const isLive =
		run?.status === "QUEUED" ||
		run?.status === "RUNNING" ||
		run?.status === "HOLD_REQUESTED";

	return (
		<section className="card-subtle stack-md" aria-label="Readiness Brief">
			<div className="row-between">
				<h2 className="h2-serif">Wave Readiness Brief</h2>
				{brief && (
					<span
						className={`pill ${
							brief.certification_label === "CERTIFIED"
								? "pill-ok"
								: brief.certification_label === "CERTIFIED_FAST"
									? "pill-sal"
									: "pill-warn"
						}`}
					>
						{brief.certification_label}
					</span>
				)}
			</div>

			{guidedView && (
				<ol className="stack-sm" style={{ margin: 0, paddingLeft: 18 }}>
					{guidedView.steps_summary.map((stepTxt, idx) => (
						<li
							key={stepTxt}
							className={
								idx === guidedView.milestone_index
									? "font-semibold"
									: "text-muted text-sm"
							}
						>
							{stepTxt}
						</li>
					))}
				</ol>
			)}

			{brief && (
				<div className="stack-sm">
					<p className="font-semibold">{brief.confidence_summary}</p>
					{(brief.plain_highlights ?? []).length > 0 && (
						<div className="card-cream stack-sm">
							<div className="field-label">What Was Checked</div>
							<ul style={{ margin: 0, paddingLeft: 18 }}>
								{(brief.plain_highlights ?? []).map((h) => (
									<li key={h} className="text-sm">
										{h}
									</li>
								))}
							</ul>
						</div>
					)}
					{(brief.watchlist ?? []).length > 0 && (
						<div className="callout-amber stack-sm">
							<div className="field-label">Future Watchlist</div>
							<ul style={{ margin: 0, paddingLeft: 18 }}>
								{(brief.watchlist ?? []).map((w) => (
									<li key={w} className="text-sm">
										{w}
									</li>
								))}
							</ul>
						</div>
					)}
					{(brief.blocked_reasons ?? []).length > 0 && (
						<output className="banner banner-danger stack-sm">
							{(brief.blocked_reasons ?? []).map((r) => (
								<div key={r}>{r}</div>
							))}
						</output>
					)}
				</div>
			)}

			{!step7Unlocked && step7LockReason && (
				<output className="banner banner-warn">
					<strong>Step 7 Target Build Locked:</strong> {step7LockReason}
				</output>
			)}

			<div className="row-end">
				{run?.status === "UNCERTIFIED_DRAFT" && (
					<button
						type="button"
						className="btn btn-primary"
						disabled={acceptBestDisabled}
						title={
							strictBlocked
								? "Strict policy requires 0 unwaived warnings and score >= 95."
								: undefined
						}
						onClick={onAcceptBest}
					>
						✓ Accept Best
					</button>
				)}
				<button
					type="button"
					className="btn btn-secondary"
					disabled={busy || !isCertified || isLive}
					onClick={onVerifyVault}
				>
					✓ Verify Vault
				</button>
			</div>
		</section>
	);
}

export function GuidedDecisionsPanel(
	props: GuidedReadinessBriefProps & {
		isMobile: boolean;
		onSubmitDecision: (answers: Record<string, string>) => Promise<void>;
		onSubmitHorizon: (req: HorizonActionReq) => Promise<void>;
		onSubmitWaiver: (req: WaiverSubmitReq) => Promise<void>;
		onAskAiRefactor: (card: GuidedDecisionCardDTO) => Promise<void>;
		onNavigateStep4: (step4RunId?: string | null) => void;
	},
) {
	const cards = props.guidedView?.decision_cards ?? [];
	const seenCardKeys = new Map<string, number>();
	return (
		<div className="split-grid">
			<div className="stack-md">
				<h2 className="h2-serif">Steward Decision Cards ({cards.length})</h2>
				{cards.length === 0 ? (
					<div className="card-subtle text-sm text-muted">
						No pending Steward clarification, waiver, or horizon decisions.
					</div>
				) : (
					cards.map((card) => {
						const occ = seenCardKeys.get(card.card_id) ?? 0;
						seenCardKeys.set(card.card_id, occ + 1);
						const renderKey =
							occ === 0 ? card.card_id : `${card.card_id}#${occ}`;
						return (
							<GuidedDecisionCard
								key={renderKey}
								card={card}
								run={props.run}
								v6bTarget={
									props.guidedView?.v6b_waiver_targets?.[card.card_id] ?? null
								}
								waveNumber={
									props.guidedView?.readiness_brief.wave_number ??
									props.vaultIr?.wave_number ??
									1
								}
								step4RunId={props.guidedView?.step4_run_id ?? null}
								isMobile={props.isMobile}
								busy={props.busy}
								onSubmitDecision={props.onSubmitDecision}
								onSubmitHorizon={props.onSubmitHorizon}
								onSubmitWaiver={props.onSubmitWaiver}
								onAskAiRefactor={props.onAskAiRefactor}
								onNavigateStep4={props.onNavigateStep4}
							/>
						);
					})
				)}
			</div>
			<GuidedReadinessBrief {...props} />
		</div>
	);
}
