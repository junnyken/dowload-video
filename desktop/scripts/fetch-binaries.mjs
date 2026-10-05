#!/usr/bin/env node
// Download the pinned sidecar binaries listed in ../binaries.lock.json,
// verify their SHA256 and place them in src-tauri/binaries/ with the
// target-triple names that Tauri's bundle.externalBin expects.
//
// Plain ESM JavaScript (no build step, no npm dependencies): run with Node 22
//   node scripts/fetch-binaries.mjs [--target <triple>] [--only <name>] [--record-extracted]
//
// Rules
// - Any url/sha256 that is still "REPLACE_ME" makes the script refuse to run.
// - extractedSha256 is the hash of the binary itself (what the app re-checks at
//   runtime, compiled in by src-tauri/build.rs). For archives whose publisher
//   does not publish that value, it can only be filled with --record-extracted,
//   and only after the archive hash matched the official checksum.
// - Nothing here goes through a shell.

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateRawSync } from 'node:zlib';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LOCK_PATH = join(ROOT, 'binaries.lock.json');
const OUT_DIR = join(ROOT, 'src-tauri', 'binaries');
const CACHE_DIR = join(ROOT, '.cache', 'binaries');
const PLACEHOLDER = 'REPLACE_ME';

function parseArgs(argv) {
  const args = { target: null, only: null, recordExtracted: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--target') args.target = argv[++i];
    else if (a === '--only') args.only = argv[++i];
    else if (a === '--record-extracted') args.recordExtracted = true;
    else if (a === '-h' || a === '--help') {
      console.log('usage: node scripts/fetch-binaries.mjs [--target <triple>] [--only <name>] [--record-extracted]');
      process.exit(0);
    } else throw new Error(`unknown argument: ${a}`);
  }
  return args;
}

function hostTriple() {
  if (process.platform === 'win32' && process.arch === 'x64') return 'x86_64-pc-windows-msvc';
  if (process.platform === 'linux' && process.arch === 'x64') return 'x86_64-unknown-linux-gnu';
  throw new Error(`no default target for ${process.platform}/${process.arch}; pass --target`);
}

const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');
const isPlaceholder = (v) => typeof v !== 'string' || v.includes(PLACEHOLDER);

async function download(url, expectedSha) {
  // Content-addressed cache: a file is reused only if its hash still matches.
  mkdirSync(CACHE_DIR, { recursive: true });
  const cached = join(CACHE_DIR, expectedSha);
  if (existsSync(cached)) {
    const buf = readFileSync(cached);
    if (sha256(buf) === expectedSha) return buf;
    rmSync(cached);
  }
  console.log(`  downloading ${url}`);
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const got = sha256(buf);
  if (got !== expectedSha) {
    throw new Error(`SHA256 mismatch for ${url}\n  expected ${expectedSha}\n  got      ${got}`);
  }
  writeFileSync(cached, buf);
  return buf;
}

// Minimal ZIP reader (stored + deflate entries, no ZIP64). Enough for the
// gyan.dev and Deno archives; avoids depending on unzip/tar being installed.
function extractZipMember(zip, member) {
  const EOCD_SIG = 0x06054b50;
  let eocd = -1;
  for (let i = zip.length - 22; i >= Math.max(0, zip.length - 65557); i--) {
    if (zip.readUInt32LE(i) === EOCD_SIG) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('zip: end of central directory not found');
  const count = zip.readUInt16LE(eocd + 10);
  let p = zip.readUInt32LE(eocd + 16);
  if (p === 0xffffffff) throw new Error('zip: ZIP64 archives are not supported');
  for (let n = 0; n < count; n++) {
    if (zip.readUInt32LE(p) !== 0x02014b50) throw new Error('zip: bad central directory entry');
    const method = zip.readUInt16LE(p + 10);
    const compSize = zip.readUInt32LE(p + 20);
    const nameLen = zip.readUInt16LE(p + 28);
    const extraLen = zip.readUInt16LE(p + 30);
    const commentLen = zip.readUInt16LE(p + 32);
    const localOff = zip.readUInt32LE(p + 42);
    const name = zip.toString('utf8', p + 46, p + 46 + nameLen);
    if (name === member) {
      const lNameLen = zip.readUInt16LE(localOff + 26);
      const lExtraLen = zip.readUInt16LE(localOff + 28);
      const start = localOff + 30 + lNameLen + lExtraLen;
      const data = zip.subarray(start, start + compSize);
      if (method === 0) return Buffer.from(data);
      if (method === 8) return inflateRawSync(data);
      throw new Error(`zip: unsupported compression method ${method} for ${member}`);
    }
    p += 46 + nameLen + extraLen + commentLen;
  }
  throw new Error(`zip: member not found: ${member}`);
}

function writeAtomic(path, buf, executable) {
  const tmp = `${path}.tmp-${process.pid}`;
  writeFileSync(tmp, buf);
  if (executable && process.platform !== 'win32') chmodSync(tmp, 0o755);
  renameSync(tmp, path);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const target = args.target ?? hostTriple();
  const exe = target.includes('windows') ? '.exe' : '';
  const lock = JSON.parse(readFileSync(LOCK_PATH, 'utf8'));
  const names = Object.keys(lock.binaries).filter((n) => !args.only || n === args.only);
  if (names.length === 0) throw new Error(`nothing to do (--only ${args.only} not in lock file)`);

  // Refuse up front, before downloading anything, if any pin is a placeholder.
  const problems = [];
  for (const name of names) {
    const t = lock.binaries[name].targets[target];
    if (!t) { problems.push(`${name}: no entry for target ${target}`); continue; }
    if (isPlaceholder(t.url)) problems.push(`${name}: url is ${PLACEHOLDER}`);
    if (isPlaceholder(t.sha256)) problems.push(`${name}: sha256 is ${PLACEHOLDER}`);
    if (isPlaceholder(t.extractedSha256) && !args.recordExtracted) {
      problems.push(`${name}: extractedSha256 is ${PLACEHOLDER} (re-run with --record-extracted to fill it from the verified archive)`);
    }
  }
  if (problems.length) {
    console.error(`Refusing to run for ${target}:\n  - ${problems.join('\n  - ')}`);
    process.exit(2);
  }

  mkdirSync(OUT_DIR, { recursive: true });
  let lockChanged = false;
  for (const name of names) {
    const t = lock.binaries[name].targets[target];
    console.log(`${name} ${lock.binaries[name].version} (${target})`);
    const payload = await download(t.url, t.sha256);
    console.log(`  archive/file sha256 OK ${t.sha256}`);
    const bin = t.archive ? extractZipMember(payload, t.archive.member) : payload;
    const binSha = sha256(bin);
    if (isPlaceholder(t.extractedSha256)) {
      // Only reachable with --record-extracted, after the archive hash matched.
      t.extractedSha256 = binSha;
      lockChanged = true;
      console.log(`  recorded extractedSha256 ${binSha}`);
    } else if (binSha !== t.extractedSha256) {
      throw new Error(`${name}: extracted binary hash mismatch\n  expected ${t.extractedSha256}\n  got      ${binSha}`);
    }
    const dest = join(OUT_DIR, `${name}-${target}${exe}`);
    writeAtomic(dest, bin, true);
    console.log(`  -> ${dest}`);
  }
  if (lockChanged) {
    writeAtomic(LOCK_PATH, Buffer.from(JSON.stringify(lock, null, 2) + '\n'), false);
    console.log(`updated ${LOCK_PATH} — review the diff and commit it; rebuild so build.rs picks up the new hashes`);
  }
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
