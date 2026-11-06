/**
 * RFC 8785 JSON Canonicalization Scheme (JCS) Serializer.
 *
 * Guarantees bit-for-bit canonical serialization identical to Python `opendv_contracts.jcs.dumps_jcs`:
 * - Lexicographically sorted dictionary keys (UTF-16 code units).
 * - Zero extraneous whitespace outside string literals.
 * - ECMAScript 7.1.12.1 number serialization:
 *   - Negative zero (`-0.0`) normalizes to `0`.
 *   - `NaN` and `Infinity` throw domain errors.
 *   - Integer range guarded to [-(2^53 - 1), 2^53 - 1].
 * - Standard JSON control character escaping with unescaped UTF-8 code points above 0x1F.
 */

const INT_MAX = 2 ** 53 - 1;
const INT_MIN = -(2 ** 53) + 1;

export class CanonicalizationError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "CanonicalizationError";
	}
}

export class IntegerDomainError extends CanonicalizationError {
	constructor(n: number | bigint) {
		super(
			`Integer ${n} exceeds safe IEEE 754 float domain [-(2^53-1), 2^53-1]`,
		);
		this.name = "IntegerDomainError";
	}
}

export class FloatDomainError extends CanonicalizationError {
	constructor(f: number) {
		super(
			`Float ${f} is not representable in RFC 8785 JCS (NaN/Infinity prohibited)`,
		);
		this.name = "FloatDomainError";
	}
}

function serializeString(str: string): string {
	return JSON.stringify(str);
}

function serializeNumber(num: number): string {
	if (Number.isNaN(num) || !Number.isFinite(num)) {
		throw new FloatDomainError(num);
	}
	if (Object.is(num, -0) || num === 0) {
		return "0";
	}
	return num.toString();
}

function serializeValue(val: unknown): string {
	if (val === null || val === undefined) {
		return "null";
	}
	if (typeof val === "boolean") {
		return val ? "true" : "false";
	}
	if (typeof val === "number") {
		return serializeNumber(val);
	}
	if (typeof val === "bigint") {
		if (val < BigInt(INT_MIN) || val > BigInt(INT_MAX)) {
			throw new IntegerDomainError(Number(val));
		}
		return val.toString();
	}
	if (typeof val === "string") {
		return serializeString(val);
	}
	if (Array.isArray(val)) {
		const items = val.map((elem) => serializeValue(elem));
		return `[${items.join(",")}]`;
	}
	if (typeof val === "object") {
		const obj = val as Record<string, unknown>;
		// RFC 8785 §3.2.3: keys ordered by UTF-16 code units
		const keys = Object.keys(obj).sort();
		const entries: string[] = [];
		for (const key of keys) {
			const itemVal = obj[key];
			if (itemVal === undefined) {
				continue;
			}
			entries.push(`${serializeString(key)}:${serializeValue(itemVal)}`);
		}
		return `{${entries.join(",")}}`;
	}
	throw new CanonicalizationError(
		`Unsupported type for canonicalization: ${typeof val}`,
	);
}

export function dumpsJcs(obj: unknown): string {
	return serializeValue(obj);
}

export function dumpsJcsBytes(obj: unknown): Uint8Array {
	const text = dumpsJcs(obj);
	return new TextEncoder().encode(text);
}

export function loadsJcs<T = unknown>(input: string | Uint8Array): T {
	const raw =
		typeof input === "string" ? input : new TextDecoder().decode(input);
	return JSON.parse(raw) as T;
}
