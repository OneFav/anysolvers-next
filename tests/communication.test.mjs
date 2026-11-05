// Deterministic local validation; no DSH session, provider, evaluator, or IM calls.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {checkedOutwardText,outwardWordCount} from '../lib/next/communication.js'

test('mixed-language outgoing messages reject excess instead of truncating or splitting',()=>{
 const hundred='word '.repeat(100)
 assert.equal(checkedOutwardText(hundred),hundred)
 assert.throws(()=>checkedOutwardText('word '.repeat(101)),/100 words/)
 assert.throws(()=>checkedOutwardText('中文研究 '.repeat(101)),/100 words/)
 assert.throws(()=>checkedOutwardText('[报告](https://example.com/'+'path/'.repeat(105)+')'),/100 words/)
 assert.throws(()=>checkedOutwardText(' '),/nonempty/)
 assert.equal(checkedOutwardText('目前没有正在运行的遗忘蚂蚁实验。'),'目前没有正在运行的遗忘蚂蚁实验。')
})

test('synthetic reviewed rewrites pass the actual tool-side validator',async()=>{
 const {cases}=JSON.parse(await readFile(new URL('./fixtures/communication/history.json',import.meta.url),'utf8'))
 for(const item of cases){
  assert.equal(checkedOutwardText(item.reviewed_rewrite),item.reviewed_rewrite)
  assert.ok(outwardWordCount(item.reviewed_rewrite)<outwardWordCount(item.original))
  if(outwardWordCount(item.original)>100)assert.throws(()=>checkedOutwardText(item.original),/100 words/)
 }
})

test('synthetic communication fixtures use the same Python and TypeScript word counts',async()=>{
 const {cases}=JSON.parse(await readFile(new URL('./fixtures/communication/model-review.json',import.meta.url),'utf8'))
 for(const item of cases){
  assert.equal(checkedOutwardText(item.outward_text),item.outward_text)
  assert.equal(outwardWordCount(item.outward_text),item.outward_words)
 }
})

test('research reporting examples retain method changes and evidence within the outward limit',async()=>{
 const fixture=JSON.parse(await readFile(new URL('./fixtures/communication/research.json',import.meta.url),'utf8'))
 assert.equal(fixture.kind,'synthetic-research-reporting')
 for(const item of fixture.cases){
  if(item.expected_action!=='send')continue
  const answer=checkedOutwardText(item.reviewed_rewrite)
  for(const alternatives of item.required_groups)assert.ok(alternatives.some(word=>answer.includes(word)),item.id+': '+alternatives)
  for(const word of item.forbidden)assert.ok(!answer.includes(word),item.id+': '+word)
 }
})

test('saved actual-model research replies satisfy the scenario rubric, including justified uncertainty',async()=>{
 const fixture=JSON.parse(await readFile(new URL('./fixtures/communication/research.json',import.meta.url),'utf8'))
 const review=JSON.parse(await readFile(new URL('./fixtures/communication/research-model-review.json',import.meta.url),'utf8'))
 assert.equal(review.final.fixture_kind,fixture.kind)
 assert.equal(review.final.cases.length,fixture.cases.length)
 assert.equal(review.final.research_executed,false)
 assert.equal(review.final.external_messages,0)
 assert.equal(review.final.tools_available,0)
 for(const item of fixture.cases){
  const observation=review.final.cases.find(c=>c.id===item.id)
  assert.ok(observation?.validation_ok,item.id)
  const response=observation.attempts.at(-1)
  assert.equal(response.action,item.expected_action,item.id)
  if(response.action==='send')assert.equal(checkedOutwardText(response.text),response.text)
  else assert.equal(response.text,'')
  for(const alternatives of item.required_groups)assert.ok(alternatives.some(word=>response.text.includes(word)),item.id+': '+alternatives)
  for(const word of item.forbidden)assert.ok(!response.text.includes(word),item.id+': '+word)
 }
})
