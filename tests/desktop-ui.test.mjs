import test from 'node:test'
import assert from 'node:assert/strict'
import {handle} from '../packages/anysolvers-im/host.mjs'
import {readFile} from 'node:fs/promises'
test('IM bridge only accepts owner and its fixed endpoints',async()=>{
 let calls=0;const execute=async v=>{calls++;return v}
 assert.equal((await handle('anysolvers-im/projects',{},undefined,{principal:{kind:'guest'}},execute)).ok,false)
 assert.equal((await handle('anysolvers-im/evaluate',{},undefined,{principal:{kind:'owner'}},execute)).ok,false)
 assert.equal(calls,0)
 const result=await handle('anysolvers-im/projects',{},undefined,{principal:{kind:'owner'}},execute)
 assert.equal(result.ok,true);assert.equal(result.value.method,'projects');assert.equal(calls,1)
})
test('IM uses separate package identity and shared renderer with native Next start',async()=>{
 const bundle=await readFile(new URL('../packages/anysolvers-im/lib/client.js',import.meta.url),'utf8')
 assert.match(bundle,/id:"@onefav\/anysolvers-im"/)
 assert.match(bundle,/label:'AnySolvers-IM'/)
 assert.match(bundle,/applyPanel/)
 assert.match(bundle,/api\('start'/)
})
