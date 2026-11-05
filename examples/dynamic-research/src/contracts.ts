import {readFile} from 'node:fs/promises'
import {join} from 'node:path'
/** This method uses only the vocabulary below; unsupported schema keywords are rejected. */
function validate(schema:any,value:unknown,path='$'):void {
 const supported=new Set(['$schema','type','const','required','properties','additionalProperties','items','minItems','maxItems'])
 if(Object.keys(schema).some(k=>!supported.has(k)))throw new Error('Unsupported fixture contract vocabulary')
 if('const'in schema&&value!==schema.const)throw new Error(`Fixture delivery ${path}: invalid constant`)
 const type=Array.isArray(value)?'array':value===null?'null':typeof value
 if(schema.type&&type!==schema.type)throw new Error(`Fixture delivery ${path}: expected ${schema.type}`)
 if(type==='number'&&!Number.isFinite(value))throw new Error(`Fixture delivery ${path}: nonfinite number`)
 if(type==='object'){
  const object=value as Record<string,unknown>
  for(const key of schema.required??[])if(!Object.hasOwn(object,key))throw new Error(`Fixture delivery ${path}: missing ${key}`)
  for(const [key,item]of Object.entries(object)){if(schema.properties?.[key])validate(schema.properties[key],item,path+'.'+key);else if(schema.additionalProperties===false)throw new Error(`Fixture delivery ${path}: unknown ${key}`)}
 }
 if(type==='array'){const values=value as unknown[];if(schema.minItems!==undefined&&values.length<schema.minItems||schema.maxItems!==undefined&&values.length>schema.maxItems)throw new Error(`Fixture delivery ${path}: invalid array length`);if(schema.items)values.forEach((item,i)=>validate(schema.items,item,path+'['+i+']'))}
}
export async function validateDelivery(packageRoot:string,value:unknown){validate(JSON.parse(await readFile(join(packageRoot,'schemas/delivery.json'),'utf8')),value)}
