import { useEffect, useState } from "react";
import { hasActiveReactDispatcher } from "../explorer-store";

export function safeUseEffect(
	effect: Parameters<typeof useEffect>[0],
	deps?: readonly unknown[] | undefined,
): void {
	if (!hasActiveReactDispatcher()) {
		return;
	}
	try {
		useEffect(effect, deps);
	} catch {
		// Ignore hook errors when called outside of a React render tree
	}
}

export function safeUseState<T>(
	initialValue: T | (() => T),
): [T, (val: T | ((prev: T) => T)) => void] {
	if (!hasActiveReactDispatcher()) {
		const val =
			typeof initialValue === "function"
				? (initialValue as () => T)()
				: initialValue;
		return [val, () => {}];
	}
	try {
		return useState(initialValue);
	} catch {
		const val =
			typeof initialValue === "function"
				? (initialValue as () => T)()
				: initialValue;
		return [val, () => {}];
	}
}
