/**
 * Playback & Transport Controls (`PlaybackControls.tsx`) for Live SSE and Offline `.jsonl` Replay (`docs/10` §3.2).
 */

import {
	PLAYBACK_SPEED_PRESETS,
	useWorkbenchStore,
} from "../../store/workbench-store";

export interface PlaybackControlsProps {
	isPlaying?: boolean | undefined;
	replayCursor?: number | undefined;
	totalReplayEvents?: number | undefined;
	playbackSpeed?: number | undefined;
	onTogglePlayPause?: (() => void) | undefined;
	onStepForward?: (() => void) | undefined;
	onSeek?: ((index: number) => void) | undefined;
	onSpeedChange?: ((speed: number) => void) | undefined;
	onReplayInstant?: (() => void) | undefined;
	onReset?: (() => void) | undefined;
}

export function PlaybackControls(props: PlaybackControlsProps) {
	const storeIsPlaying = useWorkbenchStore((s) => s.isPlaying);
	const storeCursor = useWorkbenchStore((s) => s.replayCursor);
	const storeTotal = useWorkbenchStore((s) => s.totalReplayEvents);
	const storeSpeed = useWorkbenchStore((s) => s.playbackSpeed);
	const storeTogglePlayPause = useWorkbenchStore((s) => s.togglePlayPause);
	const storeStepForward = useWorkbenchStore((s) => s.stepForward);
	const storeSeek = useWorkbenchStore((s) => s.seekReplay);
	const storeSetSpeed = useWorkbenchStore((s) => s.setPlaybackSpeed);
	const storeReplayInstant = useWorkbenchStore((s) => s.replayAllInstant);
	const storeReset = useWorkbenchStore((s) => s.resetReplay);

	const isPlaying = props.isPlaying ?? storeIsPlaying;
	const replayCursor = props.replayCursor ?? storeCursor;
	const totalReplayEvents = props.totalReplayEvents ?? storeTotal;
	const playbackSpeed = props.playbackSpeed ?? storeSpeed;
	const handleTogglePlay = props.onTogglePlayPause ?? storeTogglePlayPause;
	const handleStep = props.onStepForward ?? (() => void storeStepForward());
	const handleSeek = props.onSeek ?? ((idx: number) => void storeSeek(idx));
	const handleSpeedChange = props.onSpeedChange ?? storeSetSpeed;
	const handleInstant =
		props.onReplayInstant ?? (() => void storeReplayInstant());
	const handleReset = props.onReset ?? storeReset;

	return (
		<fieldset
			aria-label="Replay Playback Transport Controls"
			data-testid="playback-controls"
			style={{
				display: "inline-flex",
				alignItems: "center",
				gap: "0.375rem",
				backgroundColor: "var(--dv-surface-2)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.375rem",
				padding: "0.2rem 0.5rem",
				margin: 0,
			}}
		>
			<button
				type="button"
				aria-label={
					isPlaying ? "Pause replay (Space or K)" : "Play replay (Space or K)"
				}
				aria-pressed={isPlaying}
				data-testid="playback-play-pause-btn"
				onClick={handleTogglePlay}
				style={{
					backgroundColor: "var(--dv-surface-3)",
					color: "var(--dv-text-primary)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.25rem",
					padding: "0.15rem 0.45rem",
					fontSize: "0.75rem",
					cursor: "pointer",
					fontWeight: 600,
				}}
			>
				{isPlaying ? "Pause" : "Play"}
			</button>

			<button
				type="button"
				aria-label="Step forward one event (L or Right Arrow)"
				data-testid="playback-step-btn"
				onClick={handleStep}
				disabled={replayCursor >= totalReplayEvents}
				style={{
					backgroundColor: "var(--dv-surface-1)",
					color: "var(--dv-text-primary)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.25rem",
					padding: "0.15rem 0.45rem",
					fontSize: "0.75rem",
					cursor: replayCursor >= totalReplayEvents ? "not-allowed" : "pointer",
				}}
			>
				Step
			</button>

			<button
				type="button"
				aria-label="Instant complete replay"
				data-testid="playback-instant-btn"
				onClick={handleInstant}
				style={{
					backgroundColor: "var(--dv-surface-1)",
					color: "var(--dv-text-primary)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.25rem",
					padding: "0.15rem 0.45rem",
					fontSize: "0.75rem",
					cursor: "pointer",
				}}
			>
				Instant
			</button>

			<button
				type="button"
				aria-label="Reset replay to start"
				data-testid="playback-reset-btn"
				onClick={handleReset}
				style={{
					backgroundColor: "var(--dv-surface-1)",
					color: "var(--dv-text-secondary)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.25rem",
					padding: "0.15rem 0.4rem",
					fontSize: "0.75rem",
					cursor: "pointer",
				}}
			>
				Reset
			</button>

			<label
				htmlFor="dv-playback-scrubber"
				style={{
					display: "inline-flex",
					alignItems: "center",
					gap: "0.25rem",
					fontSize: "0.75rem",
					color: "var(--dv-text-secondary)",
				}}
			>
				<input
					id="dv-playback-scrubber"
					type="range"
					aria-label="Replay event cursor scrubber"
					min={0}
					max={Math.max(1, totalReplayEvents)}
					value={replayCursor}
					onChange={(ev) => handleSeek(Number(ev.target.value))}
					style={{ width: "4.5rem" }}
				/>
				<span data-testid="playback-cursor-label">
					{replayCursor}/{totalReplayEvents}
				</span>
			</label>

			<label
				htmlFor="dv-playback-speed"
				style={{
					display: "inline-flex",
					alignItems: "center",
					gap: "0.25rem",
					fontSize: "0.75rem",
					color: "var(--dv-text-secondary)",
				}}
			>
				<span>Speed</span>
				<select
					id="dv-playback-speed"
					aria-label="Select playback speed multiplier"
					data-testid="playback-speed-select"
					value={String(playbackSpeed)}
					onChange={(ev) => handleSpeedChange(Number(ev.target.value))}
					style={{
						backgroundColor: "var(--dv-surface-1)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.25rem",
						padding: "0.1rem 0.25rem",
						fontSize: "0.75rem",
					}}
				>
					{PLAYBACK_SPEED_PRESETS.map((spd) => (
						<option key={spd} value={String(spd)}>
							{spd}x
						</option>
					))}
				</select>
			</label>
		</fieldset>
	);
}
