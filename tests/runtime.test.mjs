import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath,stat} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {Context} from '@deepseek-ai/cordis'
import StudyRuntime from '../lib/plugins/study-runtime.js'
import {SessionJournal} from '../lib/core/session-journal.js'
import {saveAuthorization,readAuthorization,scopeDigest} from '../lib/core/policy-rules.js'
import {prepareWorkspace} from '../lib/core/workspace-docker.js'

async function temp(t){const dir=await realpath(await mkdtemp(join(tmpdir(),'as-runtime-')));t.after(()=>rm(dir,{recursive:true,force:true}));return dir}
test('installed service needs no legacy root and diagnoses a data-only project', {skip:!process.env.ANYSOLVERS_PYTHON},async t=>{
 const dir=await temp(t),project=join(dir,'project');await mkdir(project)
 const ctx=new Context(),fiber=ctx.plugin(StudyRuntime,{stateRoot:join(dir,'cache'),python:process.env.ANYSOLVERS_PYTHON});await fiber;t.after(()=>fiber.dispose())
 const result=await ctx.studies.forRoot(project).call('as-preflight')
 assert.equal(result.ready,false);assert.match(JSON.stringify(result),/初始化/)
 await assert.rejects(stat(join(project,'tools')),{code:'ENOENT'})
 const manifest=JSON.parse(await readFile(new URL('../lib/runtime/current.json',import.meta.url)))
 assert.ok((await stat(join(dir,'cache/engines',manifest.engine_id,'manifest.json'))).isFile())
})
test('controller, consent and private workspace choose one batch runtime directory',async t=>{
 const root=await temp(t),run='example/001',directory=join(root,'runs',run)
 await mkdir(join(directory,'.runtime'),{recursive:true});await writeFile(join(directory,'.runtime/engine.json'),'{}')
 const journal=new SessionJournal(root)
 await journal.transaction(run,async(_record,save)=>{await save()})
 const scope={action:'fixture-only'}
 await saveAuthorization(root,run,scope,'fixture-main')
 assert.equal(await readAuthorization(root,run,scopeDigest(scope)),true)
 assert.match(await prepareWorkspace(root,run,'r1-r001'),/\/\.runtime\/workspaces\//)
 assert.ok((await stat(join(directory,'.runtime/controller.json'))).isFile())
 await assert.rejects(stat(join(directory,'dsh')),{code:'ENOENT'})
 const legacy='example/002';await mkdir(join(root,'runs',legacy),{recursive:true})
 await journal.transaction(legacy,async(_record,save)=>{await save()})
 assert.ok((await stat(join(root,'runs',legacy,'dsh/controller.json'))).isFile())
})
