import { describe, expect, it } from "bun:test";
import {
	FloatDomainError,
	IntegerDomainError,
	dumpsJcs,
	dumpsJcsBytes,
	loadsJcs,
} from "../src/jcs";

describe("RFC 8785 JSON Canonicalization Scheme (JCS)", () => {
	it("serializes primitives", () => {
		expect(dumpsJcs(null)).toBe("null");
		expect(dumpsJcs(true)).toBe("true");
		expect(dumpsJcs(false)).toBe("false");
		expect(dumpsJcs(0)).toBe("0");
		expect(dumpsJcs(42)).toBe("42");
		expect(dumpsJcs(-17)).toBe("-17");
	});

	it("serializes numbers, floats and integers with ECMAScript 7.1.12.1 rules", () => {
		expect(dumpsJcs(1.0)).toBe("1");
		expect(dumpsJcs(12.8)).toBe("12.8");
		expect(dumpsJcs(0.0)).toBe("0");
		// Negative zero must not serialize with minus sign
		expect(dumpsJcs(-0.0)).toBe("0");
		expect(dumpsJcs(100.0)).toBe("100");
		expect(dumpsJcs(-5.0)).toBe("-5");

		// Exponential thresholds
		expect(dumpsJcs(1e20)).toBe("100000000000000000000");
		expect(dumpsJcs(1e21)).toBe("1e+21");
		expect(dumpsJcs(-1e21)).toBe("-1e+21");
		expect(dumpsJcs(1e-6)).toBe("0.000001");
		expect(dumpsJcs(1e-7)).toBe("1e-7");
	});

	it("throws on NaN, Infinity and out-of-range integers", () => {
		expect(() => dumpsJcs(Number.NaN)).toThrow(FloatDomainError);
		expect(() => dumpsJcs(Number.POSITIVE_INFINITY)).toThrow(FloatDomainError);
		expect(() => dumpsJcs(Number.NEGATIVE_INFINITY)).toThrow(FloatDomainError);

		expect(() => dumpsJcs(BigInt(2 ** 53))).toThrow(IntegerDomainError);
		expect(() => dumpsJcs(BigInt(-(2 ** 53)))).toThrow(IntegerDomainError);
	});

	it("serializes strings and preserves non-control Unicode", () => {
		expect(dumpsJcs("hello world")).toBe('"hello world"');
		expect(dumpsJcs("café")).toBe('"café"');
		expect(dumpsJcs("こんにちは")).toBe('"こんにちは"');
		expect(dumpsJcs("🚀")).toBe('"🚀"');
		expect(dumpsJcs("alpha/beta\\gamma")).toBe('"alpha/beta\\\\gamma"');

		// Control character escaping
		expect(dumpsJcs("\b\f\n\r\t")).toBe('"\\b\\f\\n\\r\\t"');
		expect(dumpsJcs("\x00\x1f")).toBe('"\\u0000\\u001f"');
		expect(dumpsJcs('"quoted"')).toBe('"\\"quoted\\""');
	});

	it("sorts dictionary keys by UTF-16 code units", () => {
		const data = { b: 2, a: 1, c: 3 };
		expect(dumpsJcs(data)).toBe('{"a":1,"b":2,"c":3}');

		// Surrogate pairs sort before BMP U+E000..U+FFFF
		const surrogateData = { "\uffff": 1, "\u{10000}": 2 };
		expect(dumpsJcs(surrogateData)).toBe('{"\u{10000}":2,"\uffff":1}');
	});

	it("serializes collections and nesting", () => {
		expect(dumpsJcs([])).toBe("[]");
		expect(dumpsJcs({})).toBe("{}");
		expect(dumpsJcs([1, 2, 3])).toBe("[1,2,3]");
		expect(dumpsJcs({ nested: [null, true, false, 3.14] })).toBe(
			'{"nested":[null,true,false,3.14]}',
		);
	});

	it("round-trips through bytes and parser", () => {
		const obj = {
			code: 200,
			message: "OpenDV",
			status: "OK",
			tags: ["a", "b"],
		};
		const bytes = dumpsJcsBytes(obj);
		expect(bytes instanceof Uint8Array).toBe(true);
		expect(new TextDecoder().decode(bytes)).toBe(
			'{"code":200,"message":"OpenDV","status":"OK","tags":["a","b"]}',
		);
		const roundtripped = loadsJcs(bytes);
		expect(roundtripped).toEqual(obj);
	});
});
