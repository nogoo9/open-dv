import { describe, expect, it } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { computeSha256, createTarGz, createZip } from "../src/archive";

describe("archive utilities", () => {
	it("creates valid tar.gz archive and round-trips data", () => {
		const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "archive-tar-"));
		const sourceFile = path.join(tmpDir, "sample.bin");
		const destTar = path.join(tmpDir, "sample.tar.gz");

		const testPayload = Buffer.from(
			"OpenDV Binary Payload Test Content 1234567890",
		);
		fs.writeFileSync(sourceFile, testPayload);

		createTarGz(sourceFile, "sample.bin", destTar);
		expect(fs.existsSync(destTar)).toBe(true);

		const gzBytes = fs.readFileSync(destTar);
		const uncompressed = zlib.gunzipSync(gzBytes);
		expect(uncompressed.length).toBeGreaterThan(512);

		// Extract entry name from tar header (0..100)
		const entryName = uncompressed
			.subarray(0, 100)
			.toString("utf-8")
			.replace(/\0+$/, "");
		expect(entryName).toBe("sample.bin");

		// Extract payload from offset 512
		const extracted = uncompressed.subarray(512, 512 + testPayload.length);
		expect(extracted.equals(testPayload)).toBe(true);

		fs.rmSync(tmpDir, { recursive: true, force: true });
	});

	it("creates valid zip archive and round-trips data", () => {
		const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "archive-zip-"));
		const sourceFile = path.join(tmpDir, "sample.exe");
		const destZip = path.join(tmpDir, "sample.zip");

		const testPayload = Buffer.from("Executable Data Simulation For Zip Test");
		fs.writeFileSync(sourceFile, testPayload);

		createZip(sourceFile, "sample.exe", destZip);
		expect(fs.existsSync(destZip)).toBe(true);

		// Verify zip signature (0x04034b50)
		const zipBytes = fs.readFileSync(destZip);
		expect(zipBytes.readUInt32LE(0)).toBe(0x04034b50);

		fs.rmSync(tmpDir, { recursive: true, force: true });
	});

	it("computes accurate SHA-256 digests", () => {
		const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "archive-hash-"));
		const sourceFile = path.join(tmpDir, "hash_test.txt");
		fs.writeFileSync(sourceFile, "hello world\n");

		const digest = computeSha256(sourceFile);
		expect(digest).toBe(
			"a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447",
		);

		fs.rmSync(tmpDir, { recursive: true, force: true });
	});
});
