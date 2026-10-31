import { describe, expect, it } from "bun:test";
import {
	NULL_TOKEN,
	PE_CANONICAL_HASH_PILLS,
	ZERO_KEY_ERROR_HEX,
	ZERO_KEY_HEX,
	computeDv2LengthPrefixedSha256,
	computeDv2PrehashString,
	formatSemanticHashPill,
} from "../src/index";

describe("hash-pill utilities (Algorithm A.5)", () => {
	it("defines canonical 64-hex Zero Keys and -1:^^ null token", () => {
		expect(ZERO_KEY_HEX).toBe("0".repeat(64));
		expect(ZERO_KEY_ERROR_HEX).toBe("F".repeat(64));
		expect(NULL_TOKEN).toBe("-1:^^");
	});

	it("matches Python Algorithm A.5 Private Equity test vectors bit-for-bit", async () => {
		// 1. CalPERS (GLOBAL:LP-101 -> 5D15..0AE4)
		expect(computeDv2PrehashString(["LP-101"], { bkccPrefix: "GLOBAL" })).toBe(
			"6:GLOBAL||6:LP-101",
		);
		const calpersSha = await computeDv2LengthPrefixedSha256(["LP-101"], {
			bkccPrefix: "GLOBAL",
		});
		expect(calpersSha).toBe(
			"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
		);
		const calpersPill = formatSemanticHashPill(
			"HK",
			calpersSha,
			"GLOBAL",
			"LP-101",
			"CalPERS",
		);
		expect(calpersPill.pill_label).toBe(
			"[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]",
		);
		expect(calpersPill).toEqual(PE_CANONICAL_HASH_PILLS.CALPERS_LP_101);

		// 2. Fund IV (GLOBAL:APEX_BO_IV -> 94A6..8759)
		const fundIvSha = await computeDv2LengthPrefixedSha256(["APEX_BO_IV"], {
			bkccPrefix: "GLOBAL",
		});
		expect(fundIvSha).toBe(
			"94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759",
		);
		const fundIvPill = formatSemanticHashPill(
			"HK",
			fundIvSha,
			"GLOBAL",
			"APEX_BO_IV",
			"Fund IV",
		);
		expect(fundIvPill.pill_label).toBe(
			"[HK: 94A6..8759 | GLOBAL:APEX_BO_IV Fund IV]",
		);
		expect(fundIvPill).toEqual(PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV);

		// 3. Bavarian Pension Reserve Fund (LEGACY_GP_ADMIN:LP-101 -> 6B7C..D8B9)
		const bavarianSha = await computeDv2LengthPrefixedSha256(["lp-101"], {
			bkccPrefix: "legacy_gp_admin",
		});
		expect(bavarianSha).toBe(
			"6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9",
		);
		const bavarianPill = formatSemanticHashPill(
			"HK",
			bavarianSha,
			"LEGACY_GP_ADMIN",
			"LP-101",
			"Bavarian Pension",
		);
		expect(bavarianPill.pill_label).toBe(
			"[HK: 6B7C..D8B9 | LEGACY_GP_ADMIN:LP-101 Bavarian Pension]",
		);
		expect(bavarianPill).toEqual(
			PE_CANONICAL_HASH_PILLS.BAVARIAN_PENSION_LP_101,
		);
		expect(bavarianSha).not.toBe(calpersSha);

		// 4. Nordic Cloud SaaS AB (GLOBAL:529900T8BM49AURSDO55 -> 0C18..454E)
		const nordicCloudSha = await computeDv2LengthPrefixedSha256([
			"GLOBAL",
			"529900T8BM49AURSDO55",
		]);
		expect(nordicCloudSha).toBe(
			"0C1850CAC2E686A1F9233FA223F3986DC0949963DE1A32DFFDB25E1139B0454E",
		);
	});

	it("prevents boundary-shift delimiter collisions and positional NULL collisions", async () => {
		const leftShift = await computeDv2LengthPrefixedSha256([
			"ANNE||O",
			"BRIEN",
		]);
		const rightShift = await computeDv2LengthPrefixedSha256([
			"ANNE",
			"O||BRIEN",
		]);
		expect(computeDv2PrehashString(["ANNE||O", "BRIEN"])).toBe(
			"7:ANNE||O||5:BRIEN",
		);
		expect(computeDv2PrehashString(["ANNE", "O||BRIEN"])).toBe(
			"4:ANNE||8:O||BRIEN",
		);
		expect(leftShift).not.toBe(rightShift);

		const nullMid = await computeDv2LengthPrefixedSha256([
			"VAL_X",
			null,
			"VAL_Y",
		]);
		const nullEnd = await computeDv2LengthPrefixedSha256([
			"VAL_X",
			"VAL_Y",
			null,
		]);
		expect(computeDv2PrehashString(["VAL_X", null, "VAL_Y"])).toBe(
			"5:VAL_X||-1:^^||5:VAL_Y",
		);
		expect(computeDv2PrehashString(["VAL_X", "VAL_Y", null])).toBe(
			"5:VAL_X||5:VAL_Y||-1:^^",
		);
		expect(nullMid).not.toBe(nullEnd);
	});

	it("resolves all-NULL business keys to ZERO_KEY_HEX and preserves case for satellite hash_diff", async () => {
		const allNullBk = await computeDv2LengthPrefixedSha256(
			[null, undefined, "   "],
			{ isBusinessKey: true, bkccPrefix: "GLOBAL" },
		);
		expect(allNullBk).toBe(ZERO_KEY_HEX);

		const lowerPayload = await computeDv2LengthPrefixedSha256([" Nordisk "], {
			isBusinessKey: false,
		});
		const upperPayload = await computeDv2LengthPrefixedSha256([" NORDISK "], {
			isBusinessKey: false,
		});
		expect(lowerPayload).not.toBe(upperPayload);

		// UTF-8 multi-byte length prefix check ("Nordisk Suverän Fond" has 21 UTF-8 bytes, 20 JS chars)
		expect(
			computeDv2PrehashString(["Nordisk Suverän Fond"], {
				isBusinessKey: false,
			}),
		).toBe("21:Nordisk Suverän Fond");
	});
});
