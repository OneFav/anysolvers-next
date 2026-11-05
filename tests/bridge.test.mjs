import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, rm, rename, realpath } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { PythonBridge } from '../lib/core/bridge.js'

const python = process.env.ANYSOLVERS_TEST_PYTHON ?? execFileSync('python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim()
async function fixture(t, code) {
  const root = await mkdtemp(join(tmpdir(), 'anysolvers-bridge-'))
  await mkdir(join(root, 'tools'))
  await writeFile(join(root, 'tools/dsh_bridge.py'), code)
  const bridge = new PythonBridge({ root, python, maxInputBytes: 1024, maxOutputBytes: 4096 })
  t.after(async () => { await bridge.close(); await rm(root, { recursive: true, force: true }) })
  await bridge.validate()
  return bridge
}

test('bridge preserves data and trusted scope without constructing shell commands', async t => {
  const bridge = await fixture(t, 'import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({"schema_version":1,"ok":True,"value":r}))\n')
  const payload = { content: '$(touch /never-created) `id`\n"quotes"', id: 'r1' }
  const result = await bridge.call('research', payload, { runId: 'demo/001', sessionId: 'r1-r001' })
  assert.deepEqual(result, { schema_version: 1, operation: 'research', payload, run_id: 'demo/001', session_id: 'r1-r001' })
  await assert.rejects(bridge.call('shell', {}), error => error.code === 'OPERATION')
  await assert.rejects(bridge.call('status', { huge: 'a'.repeat(1024) }), error => error.code === 'INPUT_LIMIT')
})

test('relative bridge paths work after relocation without depending on caller cwd', async t => {
  const root=await mkdtemp(join(tmpdir(),'relative bridge '))
  const moved=root+' moved';t.after(()=>rm(moved,{recursive:true,force:true}))
  await mkdir(join(root,'project/tools'),{recursive:true})
  await writeFile(join(root,'project/tools/dsh_bridge.py'),'import json,os\nprint(json.dumps({"schema_version":1,"ok":True,"value":{"cwd":os.getcwd(),"storage":os.environ.get("ANYSOLVERS_DSH_WORKSPACES")}}))\n')
  await rename(root,moved)
  const bridge=new PythonBridge({pathBase:moved,root:'project',python:'python3',workspaceRoot:'.workspaces',maxInputBytes:1024,maxOutputBytes:4096})
  t.after(()=>bridge.close());await bridge.validate()
  const result=await bridge.call('status')
  assert.equal(result.cwd,await realpath(join(moved,'project')));assert.equal(result.storage,'.workspaces')
})

test('generic discovery uses its fixed read-only entry without changing the frozen execution broker', async t => {
  const bridge = await fixture(t, 'raise RuntimeError("execution broker must not receive launch metadata")\n')
  await writeFile(join(bridge.config.root, 'tools/dsh_launch.py'), 'import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({"schema_version":1,"ok":True,"value":r}))\n')
  const value = await bridge.call('launch-profile', { config: 'configs/routes.yaml' }, { studyId: 'routing' })
  assert.equal(value.operation, 'launch-profile')
  assert.equal(value.study_id, 'routing')
  assert.equal(value.payload.config, 'configs/routes.yaml')
})

test('bridge rejects malformed and unsuccessful process responses', async t => {
  const bridge = await fixture(t, 'import sys\nprint("not JSON")\nsys.exit(2)\n')
  await assert.rejects(bridge.call('status'), error => error.code === 'PROTOCOL')
})

test('bridge returns sanitized structured domain failures', async t => {
  const bridge = await fixture(t, 'import json,sys\nprint("host private diagnostic",file=sys.stderr)\nprint(json.dumps({"schema_version":1,"ok":False,"error":{"code":"GATE","message":"Pending request"}}))\nsys.exit(2)\n')
  await assert.rejects(bridge.call('status'), error => error.code === 'GATE' && error.message === 'Pending request')
})

test('cancellation stops a process and marks its result as uncertain', async t => {
  const bridge = await fixture(t, 'import time\ntime.sleep(30)\n')
  const abort = new AbortController()
  const call = bridge.call('research', {}, {}, abort.signal)
  abort.abort()
  await assert.rejects(call, error => error.code === 'INTERRUPTED')
  await bridge.close()
  await assert.rejects(bridge.call('status'), error => error.code === 'CLOSED')
})

test('oversized responses fail closed', async t => {
  const bridge = await fixture(t, 'print("x" * 8192)\n')
  await assert.rejects(bridge.call('status'), error => error.code === 'OUTPUT_LIMIT')
})

test('desktop workspace configuration reaches Python without shell environment variables', async t => {
  const bridge = await fixture(t, 'import json,os\nprint(json.dumps({"schema_version":1,"ok":True,"value":{"workspace_root":os.environ.get("ANYSOLVERS_DSH_WORKSPACES")}}))\n')
  bridge.config.workspaceRoot = '/private/tmp/desktop-private-workspaces'
  const result = await bridge.call('status')
  assert.equal(result.workspace_root, '/private/tmp/desktop-private-workspaces')
})


test('only an explicit metadata admission lock rejection is retried, never an evaluation', async t => {
  const bridge=await fixture(t, `import json,sys,pathlib
r=json.load(sys.stdin)
p=pathlib.Path('count');n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))
if n<3 or r['operation']=='measure':
 print(json.dumps({'schema_version':1,'ok':False,'error':{'code':'PROJECT_REQUEST','message':'another evaluation or file operation is active'}}));sys.exit(2)
print(json.dumps({'schema_version':1,'ok':True,'value':{'calls':n}}))
`)
  assert.equal((await bridge.call('begin-session',{actor:'r1',idea:'seed'},{runId:'demo/001'})).calls,3)
  await assert.rejects(bridge.call('measure'),/another evaluation/)
  assert.equal(await readFile(join(bridge.config.root,'count'),'utf8'),'4')
})
