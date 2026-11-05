import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,writeFile,appendFile,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {zstdCompressSync} from 'node:zlib'
import {readFrames} from '../scripts/read-dsh-observations.mjs'

const frame=value=>zstdCompressSync(Buffer.from(value.map(v=>JSON.stringify(v)).join('\n')+'\n'))
test('DSH v4 frame reader advances a bounded compressed cursor and waits for torn tails',async t=>{
 const root=await mkdtemp(join(tmpdir(),'observer-frames-'));t.after(()=>rm(root,{recursive:true,force:true}))
 const file=join(root,'session.v4.jsonl.zstd')
 const header=frame([{type:'session',version:4,id:'fixture',createdAt:1}])
 const a=frame([{seq:0,time:1,type:'assistant/message',data:{message:{content:[{type:'text',text:'new idea'}]}}}])
 const b=frame([{seq:1,time:2,type:'tool/call',data:{callId:'c',name:'solver_result',arguments:'{}'}}])
 await writeFile(file,Buffer.concat([header,a,b.subarray(0,8)]))
 const first=await readFrames(file)
 assert.equal(first.header.id,'fixture');assert.equal(first.records.length,1)
 assert.equal(first.next,header.length+a.length)
 assert.equal((await readFrames(file,first.next)).next,first.next)
 await appendFile(file,b.subarray(8))
 const next=await readFrames(file,first.next)
 assert.equal(next.records[0].seq,1);assert.equal(next.next,header.length+a.length+b.length)
 assert.equal((await readFrames(file,next.next)).records.length,0)
})

test('bounded output stops before the next frame without discarding the completed prefix',async t=>{
 const root=await mkdtemp(join(tmpdir(),'observer-limit-'));t.after(()=>rm(root,{recursive:true,force:true}))
 const file=join(root,'session.v4.jsonl.zstd')
 const a=frame([{seq:0,time:1,type:'assistant/message',data:{text:'a'.repeat(8000)}}])
 const b=frame([{seq:1,time:2,type:'assistant/message',data:{text:'b'.repeat(8000)}}])
 await writeFile(file,Buffer.concat([a,b]))
 const first=await readFrames(file,0,524288,10000)
 assert.equal(first.next,a.length);assert.equal(first.records.length,1)
 assert.equal((await readFrames(file,first.next,524288,10000)).records[0].seq,1)
})

test('unsupported format and corrupt frames fail without modifying the source',async t=>{
 const root=await mkdtemp(join(tmpdir(),'observer-reject-'));t.after(()=>rm(root,{recursive:true,force:true}))
 const file=join(root,'session.v4.jsonl.zstd')
 await writeFile(file,frame([{type:'session',version:5,id:'future'}]))
 await assert.rejects(readFrames(file),/Unsupported/)
 await writeFile(file,Buffer.from('bad frame'))
 await assert.rejects(readFrames(file),/corrupt/)
})
