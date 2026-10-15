const assert = require('node:assert/strict');
const { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const root = join(__dirname, '../..');
const script = join(root, 'pscode-collab', 'patch-web-branding.js');

function writePng(file, width, height, marker) {
	const header = Buffer.alloc(24);
	Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(header);
	header.writeUInt32BE(13, 8);
	header.write('IHDR', 12);
	header.writeUInt32BE(width, 16);
	header.writeUInt32BE(height, 20);
	writeFileSync(file, Buffer.concat([header, Buffer.from(marker)]));
}

function run(args) {
	return spawnSync(process.execPath, [script, ...args], { encoding: 'utf8' });
}

test('patches the server bundle with PSCode web identity and House icon assets', () => {
	const fixture = mkdtempSync(join(tmpdir(), 'pscode-web-branding-'));
	try {
		const appRoot = join(fixture, 'opt', 'pscode');
		const resources = join(appRoot, 'resources', 'server');
		const assets = join(fixture, 'assets');
		mkdirSync(resources, { recursive: true });
		mkdirSync(assets, { recursive: true });

		writeFileSync(join(resources, 'manifest.json'), JSON.stringify({
			name: 'Code - OSS',
			short_name: 'Code- OSS',
			start_url: '/',
			icons: [],
		}));
		writePng(join(assets, 'house-192.png'), 192, 192, 'house-192');
		writePng(join(assets, 'house-512.png'), 512, 512, 'house-512');
		writeFileSync(join(assets, 'house.ico'), Buffer.from([0, 0, 1, 0, 1, 0, 32, 0]));

		const args = [
			'--app-root', appRoot,
			'--icon-192', join(assets, 'house-192.png'),
			'--icon-512', join(assets, 'house-512.png'),
			'--favicon', join(assets, 'house.ico'),
		];
		assert.equal(run(args).status, 0);
		assert.equal(run([...args, '--verify']).status, 0);

		const manifest = JSON.parse(readFileSync(join(resources, 'manifest.json'), 'utf8'));
		assert.equal(manifest.name, 'Piranesi Studio Code');
		assert.equal(manifest.short_name, 'PSCode');
		assert.deepEqual(manifest.icons, [
			{ src: 'code-192.png', type: 'image/png', sizes: '192x192' },
			{ src: 'code-512.png', type: 'image/png', sizes: '512x512' },
		]);
		assert.deepEqual(readFileSync(join(resources, 'code-192.png')), readFileSync(join(assets, 'house-192.png')));
		assert.deepEqual(readFileSync(join(resources, 'code-512.png')), readFileSync(join(assets, 'house-512.png')));
		assert.deepEqual(readFileSync(join(resources, 'favicon.ico')), readFileSync(join(assets, 'house.ico')));

		writePng(join(resources, 'code-192.png'), 128, 128, 'wrong-size');
		assert.notEqual(run([...args, '--verify']).status, 0);
	} finally {
		rmSync(fixture, { recursive: true, force: true });
	}
});
