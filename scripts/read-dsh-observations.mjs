// DSH 0.2.1-rc.5 / session format 4: one zstd frame per append batch.
// No persistence service, write ownership, log repair, or flush. Partial tails wait.
import {open} from 'node:fs/promises'
import {zstdDecompressSync} from 'node:zlib'

// zstdDecompressSync can accept an incomplete empty prefix without throwing.
// Parse the physical frame boundary first; only fully committed frames advance.
export function frameLength(buffer) {
  if(buffer.length<5)return null
  if(buffer.readUInt32LE(0)!==0xfd2fb528)throw Error('Unsupported/corrupt zstd magic')
  const descriptor=buffer[4], single=!!(descriptor&32), checksum=!!(descriptor&4)
  if(descriptor&8)throw Error('Corrupt zstd frame descriptor')
  const sizeFlag=descriptor>>>6, dictBytes=[0,1,2,4][descriptor&3]
  const contentBytes=sizeFlag===0?(single?1:0):[0,2,4,8][sizeFlag]
  let cursor=5+(single?0:1)+dictBytes+contentBytes
  if(buffer.length<cursor)return null
  for(;;){
    if(buffer.length<cursor+3)return null
    const header=buffer.readUIntLE(cursor,3), last=header&1, type=(header>>>1)&3, size=header>>>3
    if(type===3)throw Error('Corrupt zstd reserved block type')
    cursor+=3+(type===1?1:size)
    if(last){cursor+=checksum?4:0;return buffer.length<cursor?null:cursor}
    if(buffer.length<cursor)return null
  }
}

export async function readFrames(path, offset=0, maxInput=524288, maxOutput=524288) {
  const f=await open(path,'r')
  try {
    const stat=await f.stat()
    if(offset<0 || offset>stat.size)throw Error('Source shrank; reconcile stream identity before advancing')
    const buffer=Buffer.alloc(Math.min(maxInput,stat.size-offset))
    const {bytesRead}=await f.read(buffer,0,buffer.length,offset)
    let consumed=0, output=0, header, records=[]
    while(consumed<bytesRead) {
      const length=frameLength(buffer.subarray(consumed,bytesRead))
      if(length===null)break
      let decoded
      try { decoded=zstdDecompressSync(buffer.subarray(consumed,consumed+length),{info:true,maxOutputLength:maxOutput-output}) }
      catch(e) {
        if(e.code==='ERR_BUFFER_TOO_LARGE' && consumed>0)break
        if(e.code==='Z_BUF_ERROR' || /unexpected end|incomplete|Src size is incorrect/i.test(e.message))break
        throw Error('Unsupported/corrupt/oversize DSH frame; cursor retained: '+e.code)
      }
      const lines=decoded.buffer.toString('utf8').split('\n').filter(Boolean)
      const values=lines.map(line=>JSON.parse(line))
      if(decoded.engine.bytesWritten!==length)throw Error('DSH frame decoding did not consume its exact boundary')
      if(output+decoded.buffer.length>maxOutput)break
      for(const value of values) {
        if(value.type==='session') {
          if(value.version!==4)throw Error('Unsupported DSH session format')
          header=value
        } else {
          if(!Number.isSafeInteger(value.seq)||value.seq<0||typeof value.time!=='number')throw Error('Invalid DSH record envelope')
          records.push(value)
        }
      }
      consumed+=decoded.engine.bytesWritten
      output+=decoded.buffer.length
      if(output>=maxOutput)break
    }
    if(!consumed && bytesRead===maxInput)throw Error('DSH frame exceeds configured read limit; cursor retained')
    return {header,records,next:offset+consumed,cutoff:offset+bytesRead,size:stat.size,
      source_identity:String(stat.dev)+':'+String(stat.ino),read_bytes:bytesRead,parsed_bytes:output}
  } finally {await f.close()}
}

if(process.argv[1] && import.meta.url===new URL(process.argv[1],'file:').href) {
  let input=''
  for await(const chunk of process.stdin)input+=chunk
  try {const q=JSON.parse(input);process.stdout.write(JSON.stringify(await readFrames(q.path,q.offset??0,q.max_input,q.max_output)))}
  catch(e){process.stderr.write(String(e.message));process.exitCode=1}
}
