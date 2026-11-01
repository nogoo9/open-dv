/**
 * Accessible Theme Selector Component (`ThemeToggle.tsx`) for the Open-DV Explainability Workbench (`docs/10` §3.1–§3.2).
 */

import { useEffect, useState } from "react";
import {
	type ThemeDefinition,
	type ThemeRegistry,
	defaultThemeRegistry,
} from "./theme-registry";

export interface ThemeToggleProps {
	registry?: ThemeRegistry | undefined;
	activeThemeId?: string | undefined;
	onSelectTheme?: ((themeId: string) => void) | undefined;
	compact?: boolean | undefined;
}

export function ThemeToggle({
	registry = defaultThemeRegistry,
	activeThemeId: controlledThemeId,
	onSelectTheme,
	compact = false,
}: ThemeToggleProps) {
	const [currentTheme, setCurrentTheme] = useState<ThemeDefinition>(() =>
		controlledThemeId
			? (registry.getTheme(controlledThemeId) ?? registry.getActiveTheme())
			: registry.getActiveTheme(),
	);
	const [themes, setThemes] = useState<ThemeDefinition[]>(() =>
		registry.listThemes(),
	);

	useEffect(() => {
		setThemes(registry.listThemes());
		setCurrentTheme(
			controlledThemeId
				? (registry.getTheme(controlledThemeId) ?? registry.getActiveTheme())
				: registry.getActiveTheme(),
		);
		return registry.subscribe((updated) => {
			setThemes(registry.listThemes());
			setCurrentTheme(updated);
		});
	}, [registry, controlledThemeId]);

	const resolvedThemeId = controlledThemeId ?? currentTheme.id;

	const handleSelect = (themeId: string) => {
		const applied = registry.setActiveTheme(themeId);
		setCurrentTheme(applied);
		setThemes(registry.listThemes());
		onSelectTheme?.(themeId);
	};

	return (
		<fieldset
			aria-label="Workbench Theme Selector"
			data-testid="dv-theme-toggle"
			style={{
				display: "inline-flex",
				alignItems: "center",
				gap: "0.375rem",
				border: "none",
				padding: 0,
				margin: 0,
			}}
		>
			<label
				htmlFor="dv-theme-select"
				style={{
					fontSize: "0.75rem",
					color: "var(--dv-text-secondary)",
					fontWeight: 600,
				}}
			>
				Theme
			</label>
			<select
				id="dv-theme-select"
				aria-label="Select workbench theme"
				value={resolvedThemeId}
				onChange={(ev) => handleSelect(ev.target.value)}
				style={{
					backgroundColor: "var(--dv-surface-2)",
					color: "var(--dv-text-primary)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.375rem",
					padding: "0.25rem 0.5rem",
					fontSize: "0.75rem",
				}}
			>
				{themes.map((t) => (
					<option key={t.id} value={t.id}>
						{t.label}
						{t.contrastStandard === "WCAG_AAA" ? " (WCAG AAA)" : ""}
					</option>
				))}
			</select>
			{!compact && (
				<div
					style={{
						display: "inline-flex",
						gap: "0.25rem",
					}}
				>
					{themes.map((t) => {
						const isActive = t.id === resolvedThemeId;
						return (
							<button
								key={t.id}
								type="button"
								aria-pressed={isActive}
								aria-label={`Activate ${t.label} theme`}
								data-theme-option={t.id}
								onClick={() => handleSelect(t.id)}
								style={{
									backgroundColor: isActive
										? "var(--dv-surface-3)"
										: "var(--dv-surface-1)",
									color: "var(--dv-text-primary)",
									border: "1px solid var(--dv-border-strong)",
									borderRadius: "0.25rem",
									padding: "0.125rem 0.375rem",
									fontSize: "0.7rem",
									cursor: "pointer",
									fontWeight: isActive ? 700 : 400,
								}}
							>
								{t.label}
							</button>
						);
					})}
				</div>
			)}
		</fieldset>
	);
}
