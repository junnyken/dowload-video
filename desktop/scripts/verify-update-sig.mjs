#!/usr/bin/env node
// Verifies a Tauri updater signature (.sig) against the public key in
// src-tauri/tauri.conf.json (plugins.updater.pubkey), the same check the
// installed app does before installing an update (task #6205,
// docs/desktop/UPDATER.md). Exit 0 = verified, 1 = not.
//
//   node scripts/verify-update-sig.mjs <file> <file.sig> [expected-version]
//
// Format (minisign): the pubkey / .sig values are base64 of minisign text
// files. Public key line: "Ed" + key id (8) + Ed25519 key (32). Signature
// line: "ED" (BLAKE2b-512 pre-hashed) or "Ed" + key id (8) + signature (64).
// The global signature covers signature (64) + trusted comment.
import { createHash, createPublicKey, verify } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const [file, sigFile, expectVersion] = process.argv.slice(2);
if (!file || !sigFile) {
  console.error('usage: verify-update-sig.mjs <file> <file.sig> [expected-version]');
  process.exit(2);
}

const here = dirname(fileURLToPath(import.meta.url));
const conf = JSON.parse(readFileSync(join(here, '..', 'src-tauri', 'tauri.conf.json'), 'utf8'));
const pubText = Buffer.from(conf.plugins.updater.pubkey, 'base64').toString('utf8');
const pub = Buffer.from(pubText.trim().split('\n')[1], 'base64');
const sigLines = Buffer.from(readFileSync(sigFile, 'utf8').trim(), 'base64').toString('utf8').trim().split('\n');

function fail(msg) {
  console.error(`NOT verified: ${msg}`);
  process.exit(1);
}
if (pub.length !== 42 || pub.subarray(0, 2).toString() !== 'Ed') fail('bad public key');
if (sigLines.length !== 4 || !sigLines[2].startsWith('trusted comment: ')) fail('bad .sig format');

const sig = Buffer.from(sigLines[1], 'base64');
const alg = sig.subarray(0, 2).toString();
if (sig.length !== 74 || (alg !== 'ED' && alg !== 'Ed')) fail('bad signature line');
if (!sig.subarray(2, 10).equals(pub.subarray(2, 10))) fail('signed by another key (key id differs)');

// Ed25519 public key as SPKI DER: fixed 12-byte prefix + 32 bytes.
const key = createPublicKey({
  key: Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), pub.subarray(10)]),
  format: 'der',
  type: 'spki',
});
const data = readFileSync(file);
const msg = alg === 'ED' ? createHash('blake2b512').update(data).digest() : data;
if (!verify(null, msg, key, sig.subarray(10))) fail('file signature does not match');

const trusted = sigLines[2].slice('trusted comment: '.length);
const global = Buffer.from(sigLines[3], 'base64');
if (!verify(null, Buffer.concat([sig.subarray(10), Buffer.from(trusted, 'utf8')]), key, global)) {
  fail('trusted comment signature does not match');
}
const signedVersion = trusted.split('\t').find((f) => f.startsWith('version:'))?.slice('version:'.length);
if (expectVersion && signedVersion !== expectVersion) fail(`signed for version ${signedVersion ?? '(none)'}, expected ${expectVersion}`);

console.log(`verified: ${file}`);
console.log(`key id ${Buffer.from(pub.subarray(2, 10)).reverse().toString('hex').toUpperCase()}; trusted comment: ${trusted}`);
