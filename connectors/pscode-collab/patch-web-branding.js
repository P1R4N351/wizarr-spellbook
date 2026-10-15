#!/usr/bin/env node
'use strict';

// Applies PSCode Web branding to an already-built Code-OSS server bundle.
// It deliberately leaves product.json and bin/code-server-oss intact: those
// are managed by the existing workbench modpack and are the container's
// runtime contract. This layer owns only browser-visible PWA resources.

const fs = require('node:fs');
const path = require('node:path');

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const MAX_ASSET_BYTES = 4 * 1024 * 1024;
const PWA_ICONS = [
	{ src: 'code-192.png', type: 'image/png', sizes: '192x192' },
	{ src: 'code-512.png', type: 'image/png', sizes: '512x512' },
];

function fail(message) {
	throw new Error(`pscode web branding: ${message}`);
}

function parseArgs(argv) {
	const options = { verify: false };
	for (let index = 0; index < argv.length; index += 1) {
		const argument = argv[index];
		if (argument === '--verify') {
			options.verify = true;
			continue;
		}
		if (!['--app-root', '--icon-192', '--icon-512', '--favicon'].includes(argument)) {
			fail(`unknown argument ${argument}`);
		}
		const value = argv[index + 1];
		if (!value || value.startsWith('--')) {
			fail(`${argument} needs a path`);
		}
		options[argument.slice(2).replace(/-(.)/g, (_, character) => character.toUpperCase())] = path.resolve(value);
		index += 1;
	}
	if (!options.appRoot) {
		fail('--app-root needs a path');
	}
	if (!options.verify && (!options.icon192 || !options.icon512 || !options.favicon)) {
		fail('--icon-192, --icon-512, and --favicon are required when applying branding');
	}
	return options;
}

function assertRegularFile(file) {
	const stat = fs.statSync(file);
	if (!stat.isFile() || stat.size === 0 || stat.size > MAX_ASSET_BYTES) {
		fail(`invalid asset ${file}`);
	}
}

function readAsset(file) {
	assertRegularFile(file);
	return fs.readFileSync(file);
}

function assertPng(file, dimension) {
	const contents = readAsset(file);
	if (!contents.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
		fail(`${file} is not a PNG`);
	}
	if (contents.length < 24 || contents.readUInt32BE(16) !== dimension || contents.readUInt32BE(20) !== dimension) {
		fail(`${file} must be ${dimension}x${dimension}`);
	}
}

function assertIco(file) {
	const contents = readAsset(file);
	if (contents.length < 6 || contents.readUInt16LE(0) !== 0 || ![1, 2].includes(contents.readUInt16LE(2)) || contents.readUInt16LE(4) < 1) {
		fail(`${file} is not an ICO`);
	}
}

function serverResources(appRoot) {
	const stat = fs.statSync(appRoot);
	if (!stat.isDirectory()) {
		fail(`${appRoot} is not a directory`);
	}
	const resources = path.join(appRoot, 'resources', 'server');
	if (!fs.statSync(resources).isDirectory()) {
		fail(`${resources} is not a directory`);
	}
	return resources;
}

function readManifest(resources) {
	const manifestPath = path.join(resources, 'manifest.json');
	try {
		const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
		if (manifest === null || typeof manifest !== 'object' || Array.isArray(manifest)) {
			fail(`${manifestPath} is not an object`);
		}
		return manifest;
	} catch (error) {
		if (error instanceof Error && error.message.startsWith('pscode web branding:')) {
			throw error;
		}
		fail(`cannot read ${manifestPath}: ${error instanceof Error ? error.message : String(error)}`);
	}
}

function brandedManifest(manifest) {
	return {
		...manifest,
		name: 'Piranesi Studio Code',
		short_name: 'PSCode',
		icons: PWA_ICONS,
	};
}

function verify(resources) {
	const manifest = readManifest(resources);
	if (manifest.name !== 'Piranesi Studio Code' || manifest.short_name !== 'PSCode' || JSON.stringify(manifest.icons) !== JSON.stringify(PWA_ICONS)) {
		fail('manifest does not declare PSCode and both House PWA icons');
	}
	assertPng(path.join(resources, 'code-192.png'), 192);
	assertPng(path.join(resources, 'code-512.png'), 512);
	assertIco(path.join(resources, 'favicon.ico'));
}

function apply(options) {
	const resources = serverResources(options.appRoot);
	assertPng(options.icon192, 192);
	assertPng(options.icon512, 512);
	assertIco(options.favicon);

	fs.copyFileSync(options.icon192, path.join(resources, 'code-192.png'));
	fs.copyFileSync(options.icon512, path.join(resources, 'code-512.png'));
	fs.copyFileSync(options.favicon, path.join(resources, 'favicon.ico'));
	fs.writeFileSync(path.join(resources, 'manifest.json'), `${JSON.stringify(brandedManifest(readManifest(resources)), null, '\t')}\n`);
	verify(resources);
}

function main(argv) {
	const options = parseArgs(argv);
	const resources = serverResources(options.appRoot);
	if (options.verify) {
		verify(resources);
		process.stdout.write('pscode web branding: verified\n');
		return;
	}
	apply(options);
	process.stdout.write('pscode web branding: applied\n');
}

try {
	main(process.argv.slice(2));
} catch (error) {
	process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
	process.exitCode = 1;
}
