#!/usr/bin/env node
'use strict';

// PSCode workbench modpack — server/web bundles (code-server-oss).
//
// WHY THIS EXISTS. A full PSCode is a rebranded fork of microsoft/vscode with
// the shim baked in as a built-in. The browser lane (pscode-collab) instead
// runs a STOCK Code-OSS server bundle with the shim installed as a user VSIX,
// and `.vscodeignore` excludes scripts/** from that VSIX -- so every
// workbench-level patch in this repo is physically absent from that container.
// Installing the extension can never produce PSCode; the workbench itself has
// to be patched. This script is that patch, run at IMAGE BUILD TIME as root.
//
// WHY NOT src/piranesiModeHeal.ts. That module cannot do this job here, for
// three independently fatal reasons, each measured against the live container
// on 2026-08-29:
//   1. it returns early unless process.platform === 'darwin' (this is Linux);
//   2. out/nls.messages.* are root:root 0644 and the workbench runs as
//      uid 10001(collab), so its writeFileSync would throw EACCES; and
//   3. its healJs does `JSON.parse` on the array slice, which THROWS on this
//      build -- Code-OSS serialises 198 entries as single-quoted JS literals,
//      which is valid JS and invalid JSON.
// Patching at build time as root fixes all three at once, and also retires the
// reason the heal was written (app rebuilds reverting the patch): the image IS
// the rebuild, so the patch is reapplied by construction.
//
// WHY REGENERATING nls.messages.js FROM nls.messages.json IS SOUND. Both files
// were verified to hold the SAME table -- 22960 entries each, zero differing
// elements (executed 2026-08-29). The .json parses as JSON; the .js does not.
// So we parse the .json, relabel, and re-emit the .js from the patched array,
// preserving the original prefix and trailer byte-for-byte. JSON is a subset of
// JS, so the emitted file is valid JS. This avoids eval/vm entirely (P10 R8).
//
// nls.messages.js is the file the BROWSER actually receives: workbench.html
// loads {{WORKBENCH_NLS_FALLBACK_URL}}, server-main.js points that at
// out/nls.messages.js, and WORKBENCH_NLS_URL is empty for the default locale.
// Patching only the .json would leave the picker reading "Autopilot".

const fs = require('node:fs');
const path = require('node:path');

const NLS_PREFIX = 'globalThis._VSCODE_NLS_MESSAGES=';
const MAX_MESSAGES = 200000; // P10 R2: every loop carries a ceiling.
const MAX_FILE_BYTES = 32 * 1024 * 1024;

// Exact relabels. These carry PSCode's own wording, not a mechanical word
// swap, and are applied before the generic fallback below.
const EXACT = new Map([
	['Autopilot', 'Piranesi'],
	['Autopilot (Preview)', 'Piranesi (Preview)'],
	[
		'Auto-approve all tool calls and continue until the task is done. Autopilot may increase costs.',
		'Auto-approve every tool call (incl. terminal) and continue until the task or /goal is complete. Piranesi self-determines routine actions — above Autopilot.',
	],
	[
		'Autopilot — autonomously iterate from start to finish.',
		'Piranesi — self-determine and iterate autonomously from start to finish, above Autopilot.',
	],
	['Start new chat sessions in Autopilot mode.', 'Start new chat sessions in Piranesi mode.'],
	['Enable Autopilot?', 'Enable Piranesi?'],
	['Implement with Autopilot', 'Implement with Piranesi'],
	['Implement with Autopilot Fleet', 'Implement with Piranesi Fleet'],
	['Start with Autopilot', 'Start with Piranesi'],
	['Started implementation with Autopilot', 'Started implementation with Piranesi'],
]);

const LONG_ENABLE_PREFIX = 'Autopilot will auto-approve all tool calls and continue working autonomously';

// CHAT PROGRESS FILLER (2026-10-09, operator). While a response is pending the stock
// chat view rotates jokes through its waiting line ("Bribing the hamster",
// "Summoning Clippy", ...). In PSCode the shim's chat participant reports the
// waist's own progress there — door, route, spool sibling, first token, token
// usage — so the filler becomes a plain "Working" and is replaced by real data
// as soon as the waist answers. Exact strings from nls.messages.json 1.128.
const PROGRESS_FILLER = new Map([
	['Bribing the hamster', 'Working'],
	['Reticulating splines', 'Working'],
	['Untangling the spaghetti', 'Working'],
	['Communing with the codebase', 'Working'],
	['Mining diamonds', 'Working'],
	['Summoning Clippy', 'Working'],
]);

// Strings that MUST keep the word "Autopilot". The scheduler dialog string
// enumerates the NATIVE Permission Mode options by name; renaming it there
// would describe a control that does not exist. PSCode desktop preserves this
// exact string too -- it is one of the 2 remaining "Autopilot" hits there.
const PRESERVE_PREFIXES = [
	'The dialog has a Name field',
];

const BRAND = {
	nameShort: 'PSCode',
	nameLong: 'Piranesi Studio Code',
	applicationName: 'pscode',
	dataFolderName: '.pscode',
	urlProtocol: 'pscode',
	serverApplicationName: 'pscode-server',
	serverDataFolderName: '.pscode-server',
	updateCommand: 'piranesi-shim.checkForUpdates',
};

function readTextFile(file) {
	const stat = fs.statSync(file);
	if (stat.size > MAX_FILE_BYTES) {
		throw new Error(`refusing to read ${file}: ${stat.size} bytes exceeds cap`);
	}
	return fs.readFileSync(file, 'utf8');
}

function isPreserved(value) {
	for (let i = 0; i < PRESERVE_PREFIXES.length; i += 1) {
		if (value.startsWith(PRESERVE_PREFIXES[i])) {
			return true;
		}
	}
	return false;
}

// Returns the relabelled string, or null when the entry is already correct.
function relabelOne(value) {
	if (typeof value !== 'string') {
		return null;
	}
	const filler = PROGRESS_FILLER.get(value);
	if (filler !== undefined) {
		return filler;
	}
	if (!value.includes('Autopilot')) {
		return null;
	}
	// IDEMPOTENCE. Three of our own replacement values deliberately keep the
	// word "Autopilot" as a comparative ("... above Autopilot."). Without this
	// guard the generic fallback at the bottom re-fires on them the next time
	// the patcher runs and rewrites them to "above Piranesi", quietly
	// destroying the comparison. Measured on 2026-08-29: a second apply took
	// the bundle from 4 Autopilot occurrences to 1.
	// Stock Code-OSS contains zero occurrences of "Piranesi", so the presence
	// of that word is a sound marker that this entry is already ours.
	if (value.includes('Piranesi')) {
		return null;
	}
	if (isPreserved(value)) {
		return null;
	}
	const exact = EXACT.get(value);
	if (exact !== undefined) {
		return exact === value ? null : exact;
	}
	if (value.startsWith(LONG_ENABLE_PREFIX)) {
		return value.replace('Autopilot will', 'Piranesi will');
	}
	return value.split('Autopilot').join('Piranesi');
}

function relabelMessages(messages) {
	if (!Array.isArray(messages)) {
		throw new Error('nls message table is not an array');
	}
	if (messages.length > MAX_MESSAGES) {
		throw new Error(`nls table too large: ${messages.length}`);
	}
	let changed = 0;
	let autopilot = 0;
	for (let i = 0; i < messages.length; i += 1) {
		const next = relabelOne(messages[i]);
		if (next !== null) {
			if (messages[i].includes('Autopilot')) {
				autopilot += 1;
			}
			messages[i] = next;
			changed += 1;
		}
	}
	// `autopilot` counts the relabels that remove the word; the filler
	// relabels change nothing the Autopilot gate in main() can measure.
	return { changed, autopilot };
}

// Splits the .js bundle into [prefix, arraySource, trailer] without evaluating
// it. The trailer (";\n\n//# sourceMappingURL=...") is preserved verbatim.
function splitNlsJs(raw) {
	const start = raw.indexOf(NLS_PREFIX);
	if (start !== 0) {
		throw new Error(`nls.messages.js does not begin with the expected prefix (found at ${start})`);
	}
	const arrayStart = NLS_PREFIX.length;
	if (raw[arrayStart] !== '[') {
		throw new Error('nls.messages.js prefix is not followed by an array literal');
	}
	const arrayEnd = raw.lastIndexOf(';');
	if (arrayEnd <= arrayStart) {
		throw new Error('nls.messages.js has no terminating semicolon after the array');
	}
	if (raw[arrayEnd - 1] !== ']') {
		throw new Error('nls.messages.js array does not end immediately before its semicolon');
	}
	return { prefix: raw.slice(0, arrayStart), trailer: raw.slice(arrayEnd) };
}

function patchNls(outDir, apply) {
	const jsonPath = path.join(outDir, 'nls.messages.json');
	const jsPath = path.join(outDir, 'nls.messages.js');
	if (!fs.existsSync(jsonPath) || !fs.existsSync(jsPath)) {
		return { skipped: true, changed: 0, detail: 'nls.messages.{json,js} not both present' };
	}
	const messages = JSON.parse(readTextFile(jsonPath));
	const jsRaw = readTextFile(jsPath);
	const { prefix, trailer } = splitNlsJs(jsRaw);
	const { changed, autopilot } = relabelMessages(messages);
	if (changed > 0 && apply) {
		const serialised = JSON.stringify(messages);
		fs.writeFileSync(jsonPath, `${serialised}\n`);
		fs.writeFileSync(jsPath, `${prefix}${serialised}${trailer}`);
	}
	return { skipped: false, changed, autopilot, detail: `${messages.length} entries` };
}

// The native permissions picker carries a "learn more" pseudo-option that
// links out to vendor docs. PSCode desktop strips it (see
// scripts/patch-vscode-native-permissions.js, which does the same thing to the
// DESKTOP bundles). The browser lane keeps its workbench somewhere else
// entirely -- out/vs/code/browser/workbench/workbench.js, not
// workbench.desktop.main.js -- which is why the desktop patcher cannot reach
// it and the option survived here. Verified 2026-09-01: exactly 1 occurrence
// in the container bundle, same marker shape as desktop.
const LEARN_MORE_MARKER = 'actionBarActions:[{id:"chat.permissions.learnMore"';
const EMPTY_ACTION_BAR = 'actionBarActions:[]';
const MAX_ACTIONBAR_PATCHES = 32; // P10 R2: the scan is bounded, not while(true).
const WORKBENCH_REL = path.join('out', 'vs', 'code', 'browser', 'workbench', 'workbench.js');

// Scans forward for the matching close bracket, honouring string literals and
// escapes so a bracket inside a quoted label cannot end the array early.
function findMatchingBracket(text, start, open, close) {
	if (text[start] !== open) {
		throw new Error(`expected ${open} at offset ${start}`);
	}
	let depth = 0;
	let quote = '';
	let escaped = false;
	for (let i = start; i < text.length; i += 1) {
		const char = text[i];
		if (quote) {
			if (escaped) {
				escaped = false;
			} else if (char === '\\') {
				escaped = true;
			} else if (char === quote) {
				quote = '';
			}
			continue;
		}
		if (char === '"' || char === "'" || char === '`') {
			quote = char;
			continue;
		}
		if (char === open) {
			depth += 1;
		} else if (char === close) {
			depth -= 1;
			if (depth === 0) {
				return i;
			}
		}
	}
	throw new Error(`no matching ${close} for offset ${start}`);
}

function removeLearnMoreActionBars(content) {
	let output = content;
	let offset = 0;
	let removed = 0;
	for (let guard = 0; guard < MAX_ACTIONBAR_PATCHES; guard += 1) {
		const start = output.indexOf(LEARN_MORE_MARKER, offset);
		if (start < 0) {
			break;
		}
		const arrayStart = output.indexOf('[', start);
		const arrayEnd = findMatchingBracket(output, arrayStart, '[', ']');
		output = `${output.slice(0, start)}${EMPTY_ACTION_BAR}${output.slice(arrayEnd + 1)}`;
		offset = start + EMPTY_ACTION_BAR.length;
		removed += 1;
	}
	return { content: output, removed };
}

// CLIENT IDENTITY (added 2026-10-09). The browser never learns nameShort /
// nameLong from the server: webClientServer.ts sends only embedderIdentifier
// and the extensions gallery, and the client falls back to the product object
// the BUILD inlined into workbench.js (product.ts: BUILD->INSERT_PRODUCT_
// CONFIGURATION). The shipped bundle was built before product.json was
// branded, so that literal still reads "Code - OSS" — which is exactly what
// the welcome page and the tab title showed (measured 2026-10-09). Patching
// product.json cannot reach it; the literal itself is rebranded here. Keys are
// replaced inside that ONE object only, located by its unmistakable opening,
// and the "Code - OSS Dev" out-of-sources fallback is left alone.
const CLIENT_IDENTITY_OPEN = 'nameShort:"Code - OSS",nameLong:"Code - OSS",';
const CLIENT_IDENTITY = {
	nameShort: 'PSCode',
	nameLong: 'Piranesi Studio Code',
	applicationName: 'pscode',
	dataFolderName: '.pscode',
	urlProtocol: 'pscode',
	linuxIconName: 'pscode',
	serverApplicationName: 'pscode-server',
	serverDataFolderName: '.pscode-server',
	licenseUrl: 'http://100.64.0.12:18792/piranesi-org/pscode/src/branch/main/LICENSE.txt',
	reportIssueUrl: 'http://100.64.0.12:18792/piranesi-org/pscode/issues/new',
	documentationUrl: 'http://100.64.0.10:18790/wiki/concepts/piranesi/cpcs',
};
const MAX_IDENTITY_OBJECT_BYTES = 65536; // P10 R2: the scan has a ceiling.

// Returns {content, changed}. Idempotent: once the opening reads PSCode the
// object is found by its branded opening instead, and every key compares equal.
function rebrandClientIdentity(content) {
	const brandedOpen = `nameShort:"${CLIENT_IDENTITY.nameShort}",nameLong:"${CLIENT_IDENTITY.nameLong}",`;
	let start = content.indexOf(CLIENT_IDENTITY_OPEN);
	if (start < 0) {
		start = content.indexOf(brandedOpen);
	}
	if (start < 0) {
		return { content, changed: 0, found: false };
	}
	const objectStart = content.lastIndexOf('{', start);
	if (objectStart < 0 || start - objectStart > 8) {
		throw new Error('client identity opening is not at the start of an object literal');
	}
	const objectEnd = findMatchingBracket(content, objectStart, '{', '}');
	if (objectEnd - objectStart > MAX_IDENTITY_OBJECT_BYTES) {
		throw new Error('client identity object exceeds the scan ceiling');
	}
	let object = content.slice(objectStart, objectEnd + 1);
	let changed = 0;
	for (const key of Object.keys(CLIENT_IDENTITY)) {
		const pattern = new RegExp(`([{,])${key}:"([^"]*)"`);
		const match = pattern.exec(object);
		if (!match) {
			continue; // a key the build did not inline is not invented
		}
		if (match[2] === CLIENT_IDENTITY[key]) {
			continue;
		}
		object = object.replace(pattern, `$1${key}:"${CLIENT_IDENTITY[key]}"`);
		changed += 1;
	}
	return { content: `${content.slice(0, objectStart)}${object}${content.slice(objectEnd + 1)}`, changed, found: true };
}

function patchWorkbenchBundle(appRoot, apply) {
	const bundlePath = path.join(appRoot, WORKBENCH_REL);
	if (!fs.existsSync(bundlePath)) {
		return { skipped: true, changed: 0, detail: 'browser workbench bundle absent' };
	}
	const before = readTextFile(bundlePath);
	const stripped = removeLearnMoreActionBars(before);
	const identity = rebrandClientIdentity(stripped.content);
	if (!identity.found) {
		throw new Error('client identity object not found in the browser workbench bundle');
	}
	const content = identity.content;
	const changed = stripped.removed + identity.changed;
	if (changed > 0 && apply && content !== before) {
		fs.writeFileSync(bundlePath, content);
	}
	return { skipped: false, changed, detail: `${stripped.removed} learn-more action bars, ${identity.changed} identity keys` };
}

// CHAT WITHOUT GITHUB (added 2026-10-09, Piranesi-Main DECIDE: A, cs_mv0aff9e_12).
//
// Stock product.json names GitHub.copilot-chat as `defaultChatAgent`. The
// workbench then registers Copilot "setup" agents as the default chat
// participant, so the first message in the chat view runs the Copilot
// install + GitHub sign-in flow -- in a container that has no Copilot
// extension and no route to github.com. Measured 2026-10-09: that is why
// the web chat never reached a model.
//
// Two product keys fix it at the source. Removing `defaultChatAgent` makes
// ChatEntitlementService return before it builds the setup machinery (the
// whole chatSetup contribution then disables itself), and
// `extensionEnabledApiProposals` grants the shim the `defaultChatParticipant`
// proposal its manifest declares, so @piranesi -- whose participant is
// `isDefault` -- becomes the default agent and the model picker is populated
// by the shim's language-model providers (the Piranesi waist). No GitHub
// account is consulted anywhere on this path.
const CHAT_PRODUCT = {
	extensionEnabledApiProposals: {
		'piranesi.piranesi-shim': ['defaultChatParticipant'],
	},
};
const CHAT_PRODUCT_REMOVE = ['defaultChatAgent'];

function patchChatProduct(product) {
	let changed = 0;
	for (let i = 0; i < CHAT_PRODUCT_REMOVE.length; i += 1) {
		const key = CHAT_PRODUCT_REMOVE[i];
		if (Object.prototype.hasOwnProperty.call(product, key)) {
			delete product[key];
			changed += 1;
		}
	}
	const keys = Object.keys(CHAT_PRODUCT);
	for (let i = 0; i < keys.length; i += 1) {
		const key = keys[i];
		const want = JSON.stringify(CHAT_PRODUCT[key]);
		if (JSON.stringify(product[key]) !== want) {
			product[key] = JSON.parse(want);
			changed += 1;
		}
	}
	return changed;
}

function patchProduct(appRoot, apply) {
	const productPath = path.join(appRoot, 'product.json');
	if (!fs.existsSync(productPath)) {
		return { skipped: true, changed: 0, detail: 'product.json absent' };
	}
	const product = JSON.parse(readTextFile(productPath));
	const keys = Object.keys(BRAND);
	let changed = 0;
	for (let i = 0; i < keys.length; i += 1) {
		const key = keys[i];
		if (product[key] !== BRAND[key]) {
			product[key] = BRAND[key];
			changed += 1;
		}
	}
	changed += patchChatProduct(product);
	if (changed > 0 && apply) {
		fs.writeFileSync(productPath, `${JSON.stringify(product, null, '\t')}\n`);
	}
	return { skipped: false, changed, detail: `${keys.length} branded keys + chat-without-github` };
}

function countAutopilot(outDir) {
	const jsPath = path.join(outDir, 'nls.messages.js');
	if (!fs.existsSync(jsPath)) {
		return -1;
	}
	return readTextFile(jsPath).split('Autopilot').length - 1;
}

function main(argv) {
	const apply = !argv.includes('--verify');
	const rootFlag = argv.indexOf('--app-root');
	const appRoot = rootFlag >= 0 && argv[rootFlag + 1]
		? argv[rootFlag + 1]
		: (process.env.PSCODE_APP_ROOT || '/opt/pscode');
	const outDir = path.join(appRoot, 'out');
	if (!fs.existsSync(outDir)) {
		process.stderr.write(`[modpack] no out/ under ${appRoot}\n`);
		return 3;
	}
	const before = countAutopilot(outDir);
	const nls = patchNls(outDir, apply);
	const product = patchProduct(appRoot, apply);
	const workbench = patchWorkbenchBundle(appRoot, apply);
	const after = countAutopilot(outDir);
	const mode = apply ? 'apply' : 'verify';
	process.stdout.write(
		`[modpack] ${mode}: nls ${nls.changed} relabelled (${nls.detail}); ` +
		`product ${product.changed} rebranded (${product.detail}); ` +
		`workbench ${workbench.changed} stripped (${workbench.detail}); ` +
		`Autopilot occurrences ${before} -> ${after}\n`
	);
	if (!apply && workbench.detail.endsWith(' identity keys') && !workbench.detail.endsWith(' 0 identity keys')) {
		process.stderr.write('[modpack] VERIFY FAILED: client identity keys still unbranded in the workbench bundle\n');
		return 1;
	}
	// Only Autopilot relabels can move the Autopilot count; the chat-filler
	// relabels (PROGRESS_FILLER) legitimately change nls without touching it
	// (measured 2026-10-09: "nls 6 relabelled ... Autopilot occurrences 4 -> 4"
	// failed this gate and the image build with it).
	if (apply && (nls.autopilot || 0) > 0 && after >= before) {
		process.stderr.write('[modpack] FAILED: Autopilot relabels reported but the bundle did not shrink its Autopilot count\n');
		return 1;
	}
	return 0;
}

if (require.main === module) {
	process.exitCode = main(process.argv.slice(2));
}

module.exports = {
	relabelOne, relabelMessages, splitNlsJs, patchNls, patchProduct,
	findMatchingBracket, removeLearnMoreActionBars, patchWorkbenchBundle,
	EXACT, BRAND,
};
