/**
 * Standalone Archive & Checksum Utilities for opendvctl release packaging.
 *
 * Implements pure TypeScript, zero-dependency:
 * - POSIX ustar tar.gz archive generation via node:zlib
 * - PKZip Deflate archive generation via node:zlib
 * - SHA-256 digest computation via node:crypto
 */

import crypto from "node:crypto";
import fs from "node:fs";
import zlib from "node:zlib";

/**
 * Creates a gzipped POSIX ustar tar archive containing a single file.
 */
export function createTarGz(
	sourceFilePath: string,
	entryName: string,
	destGzPath: string,
): void {
	const fileBytes = fs.readFileSync(sourceFilePath);
	const fileSize = fileBytes.length;

	// 512-byte POSIX ustar header
	const header = Buffer.alloc(512);

	// 0..100: File name (null terminated or padded)
	header.write(entryName, 0, 100, "utf-8");
	// 100..108: File mode (executable: 0000755)
	header.write("0000755\0", 100, 8, "ascii");
	// 108..116: Owner numeric ID
	header.write("0000000\0", 108, 8, "ascii");
	// 116..124: Group numeric ID
	header.write("0000000\0", 116, 8, "ascii");
	// 124..136: Size in bytes (11 octal digits + null)
	header.write(`${fileSize.toString(8).padStart(11, "0")}\0`, 124, 12, "ascii");
	// 136..148: Last modification time (11 octal digits + null)
	const mtime = Math.floor(Date.now() / 1000);
	header.write(`${mtime.toString(8).padStart(11, "0")}\0`, 136, 12, "ascii");
	// 148..156: Checksum (initially filled with spaces)
	header.fill(0x20, 148, 156);
	// 156: Type flag ('0' for normal file)
	header[156] = 0x30;
	// 257..263: Magic ("ustar\0")
	header.write("ustar\0", 257, 6, "ascii");
	// 263..265: Version ("00")
	header.write("00", 263, 2, "ascii");

	// Compute checksum: unsigned sum of all bytes in 512-byte header
	let chksum = 0;
	for (let i = 0; i < 512; i++) {
		chksum += header[i] ?? 0;
	}
	header.write(`${chksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");

	// Padding to 512-byte record boundary
	const padding = (512 - (fileSize % 512)) % 512;
	const padBuffer = Buffer.alloc(padding);
	// Two 512-byte zero blocks marking end of tar archive
	const endBlocks = Buffer.alloc(1024);

	const tarBuffer = Buffer.concat([header, fileBytes, padBuffer, endBlocks]);
	const gzBuffer = zlib.gzipSync(tarBuffer, { level: 9 });
	fs.writeFileSync(destGzPath, gzBuffer);
}

/**
 * Creates a standard PKZip archive containing a single file compressed with Deflate.
 */
export function createZip(
	sourceFilePath: string,
	entryName: string,
	destZipPath: string,
): void {
	const fileBytes = fs.readFileSync(sourceFilePath);
	const uncompressedSize = fileBytes.length;

	// Compress file data with raw Deflate
	const compressedData = zlib.deflateRawSync(fileBytes, { level: 9 });
	const compressedSize = compressedData.length;
	const nameBuffer = Buffer.from(entryName, "utf-8");
	const nameLen = nameBuffer.length;
	const crc = zlib.crc32(fileBytes);

	// Local file header (30 bytes + nameLen)
	const localHeader = Buffer.alloc(30);
	localHeader.writeUInt32LE(0x04034b50, 0); // Local file header signature
	localHeader.writeUInt16LE(20, 4); // Version needed to extract (2.0)
	localHeader.writeUInt16LE(0, 6); // General purpose bit flag
	localHeader.writeUInt16LE(8, 8); // Compression method: 8 = Deflate
	localHeader.writeUInt16LE(0, 10); // File last modification time
	localHeader.writeUInt16LE(0, 12); // File last modification date
	localHeader.writeUInt32LE(crc, 14); // CRC-32
	localHeader.writeUInt32LE(compressedSize, 18); // Compressed size
	localHeader.writeUInt32LE(uncompressedSize, 22); // Uncompressed size
	localHeader.writeUInt16LE(nameLen, 26); // File name length
	localHeader.writeUInt16LE(0, 28); // Extra field length

	const localRecord = Buffer.concat([localHeader, nameBuffer, compressedData]);
	const localOffset = 0;

	// Central directory header (46 bytes + nameLen)
	const cdHeader = Buffer.alloc(46);
	cdHeader.writeUInt32LE(0x02014b50, 0); // Central directory header signature
	cdHeader.writeUInt16LE(20, 4); // Version made by
	cdHeader.writeUInt16LE(20, 6); // Version needed to extract
	cdHeader.writeUInt16LE(0, 8); // General purpose bit flag
	cdHeader.writeUInt16LE(8, 10); // Compression method: 8 = Deflate
	cdHeader.writeUInt16LE(0, 12); // File last modification time
	cdHeader.writeUInt16LE(0, 14); // File last modification date
	cdHeader.writeUInt32LE(crc, 16); // CRC-32
	cdHeader.writeUInt32LE(compressedSize, 20); // Compressed size
	cdHeader.writeUInt32LE(uncompressedSize, 24); // Uncompressed size
	cdHeader.writeUInt16LE(nameLen, 28); // File name length
	cdHeader.writeUInt16LE(0, 30); // Extra field length
	cdHeader.writeUInt16LE(0, 32); // File comment length
	cdHeader.writeUInt16LE(0, 34); // Disk number start
	cdHeader.writeUInt16LE(0, 36); // Internal file attributes
	cdHeader.writeUInt32LE(0, 38); // External file attributes
	cdHeader.writeUInt32LE(localOffset, 42); // Relative offset of local header

	const cdRecord = Buffer.concat([cdHeader, nameBuffer]);

	// End of central directory record (22 bytes)
	const eocd = Buffer.alloc(22);
	eocd.writeUInt32LE(0x06054b50, 0); // End of central dir signature
	eocd.writeUInt16LE(0, 4); // Number of this disk
	eocd.writeUInt16LE(0, 6); // Disk where central directory starts
	eocd.writeUInt16LE(1, 8); // Number of central directory records on this disk
	eocd.writeUInt16LE(1, 10); // Total number of central directory records
	eocd.writeUInt32LE(cdRecord.length, 12); // Size of central directory
	eocd.writeUInt32LE(localRecord.length, 16); // Offset of start of central directory
	eocd.writeUInt16LE(0, 20); // Comment length

	const zipBuffer = Buffer.concat([localRecord, cdRecord, eocd]);
	fs.writeFileSync(destZipPath, zipBuffer);
}

/**
 * Computes hexadecimal SHA-256 digest of a file.
 */
export function computeSha256(filePath: string): string {
	const hash = crypto.createHash("sha256");
	const data = fs.readFileSync(filePath);
	hash.update(data);
	return hash.digest("hex");
}
