#!/usr/bin/env node
'use strict';
// Black-box test of patch-workbench-modpack.js's client-identity rebranding:
// a synthetic workbench.js carrying the BUILD-inlined "Code - OSS" product
// literal (the shape measured in the live 1.128.0 bundle, 2026-10-09) plus the
// out-of-sources "Code - OSS Dev" fallback that must stay untouched.
//   verify before -> rc 1 (fail closed); apply -> rc 0; verify after -> rc 0;
//   second apply changes nothing; Dev fallback intact; applicationName etc. set.
const { execFileSync, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const PATCHER = path.resolve(__dirname, '..', '..', 'pscode-collab', 'patch-workbench-modpack.js');
const LITERAL = 'nw={nameShort:"Code - OSS",nameLong:"Code - OSS",applicationName:"code-oss",dataFolderName:".vscode-oss",urlProtocol:"code-oss",linuxIconName:"code-oss",licenseUrl:"https://github.com/microsoft/vscode/blob/main/LICENSE.txt",reportIssueUrl:"https://github.com/microsoft/vscode/issues/new",serverApplicationName:"code-server-oss",serverDataFolderName:".vscode-server-oss",commit:"42f7092d1f1917f2eecf21ef23cef21363014595",nested:{keep:"me"}}';
const DEV = 'Object.assign(nw,{version:"1.104.0-dev",nameShort:"Code - OSS Dev",nameLong:"Code - OSS Dev",applicationName:"code-oss"})';
const BUNDLE = `(function(){var nw;if(globalThis._VSCODE_PRODUCT_JSON){nw=globalThis._VSCODE_PRODUCT_JSON}else ${LITERAL};if(Object.keys(nw).length===0){${DEV}}})();`;

function run(args) {
	const r = spawnSync(process.execPath, [PATCHER, ...args], { encoding: 'utf8' });
	return { rc: r.status, out: `${r.stdout}${r.stderr}` };
}
function assert(cond, msg) { if (!cond) { throw new Error(`FAIL: ${msg}`); } }

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modpack-identity-'));
const dir = path.join(root, 'out', 'vs', 'code', 'browser', 'workbench');
fs.mkdirSync(dir, { recursive: true });
const bundle = path.join(dir, 'workbench.js');
fs.writeFileSync(bundle, BUNDLE);

const before = run(['--app-root', root, '--verify']);
assert(before.rc === 1 && /identity keys still unbranded/.test(before.out), `verify-before must fail closed: rc=${before.rc} ${before.out}`);
const apply = run(['--app-root', root]);
assert(apply.rc === 0 && /10 identity keys/.test(apply.out), `apply: rc=${apply.rc} ${apply.out}`);
const after = run(['--app-root', root, '--verify']);
assert(after.rc === 0 && /0 identity keys/.test(after.out), `verify-after: rc=${after.rc} ${after.out}`);
const patched = fs.readFileSync(bundle, 'utf8');
assert(patched.includes('nameShort:"PSCode",nameLong:"Piranesi Studio Code",applicationName:"pscode",dataFolderName:".pscode",urlProtocol:"pscode",linuxIconName:"pscode"'), 'identity keys rebranded in order');
assert(patched.includes('serverApplicationName:"pscode-server",serverDataFolderName:".pscode-server"'), 'server names rebranded');
assert(patched.includes('commit:"42f7092d1f1917f2eecf21ef23cef21363014595",nested:{keep:"me"}'), 'untouched keys and nested object preserved');
assert(patched.includes(DEV), 'Code - OSS Dev out-of-sources fallback untouched');
assert(!patched.includes('nameShort:"Code - OSS",'), 'no built literal left unbranded');
const again = run(['--app-root', root]);
assert(again.rc === 0 && /0 identity keys/.test(again.out), `idempotent second apply: ${again.out}`);

// A bundle with no identity object at all is a hard failure, never a silent pass.
const root2 = fs.mkdtempSync(path.join(os.tmpdir(), 'modpack-identity-'));
fs.mkdirSync(path.join(root2, 'out', 'vs', 'code', 'browser', 'workbench'), { recursive: true });
fs.writeFileSync(path.join(root2, 'out', 'vs', 'code', 'browser', 'workbench', 'workbench.js'), 'var x=1;');
const missing = run(['--app-root', root2]);
assert(missing.rc !== 0 && /identity object not found/.test(missing.out), `missing object must fail: rc=${missing.rc} ${missing.out}`);

fs.rmSync(root, { recursive: true, force: true });
fs.rmSync(root2, { recursive: true, force: true });
process.stdout.write('test_modpack_identity: 9 assertions passed\n');
