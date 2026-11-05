#!/usr/bin/env node
/** Install exact local release tarballs into a selected DSH profile. Never starts or stops DSH. */
import {readFile,writeFile,mkdir,copyFile,stat} from 'node:fs/promises'
import {resolve,join} from 'node:path'
import {spawnSync} from 'node:child_process'
import {createHash} from 'node:crypto'
import {createRequire} from 'node:module'
import {pathToFileURL} from 'node:url'
import {dirname} from 'node:path'
const args=process.argv.slice(2),option=name=>{const i=args.indexOf(name);return i<0?undefined:args[i+1]},profileArg=option('--profile'),artifactsArg=option('--artifacts')
if(!profileArg||!artifactsArg)throw new Error('Usage: node install-profile.mjs --profile PROFILE --artifacts RELEASE [--fixture]; CLI paths are relative to the invocation directory')
const profile=resolve(profileArg),artifacts=resolve(artifactsArg),release=JSON.parse(await readFile(join(artifacts,'release.json'),'utf8'))
const packages=release.packages.filter(p=>args.includes('--fixture')||p.name!=='@onefav/dynamic-research-fixture')
for(const entry of packages){const file=join(artifacts,entry.file);if(createHash('sha256').update(await readFile(file)).digest('hex')!==entry.sha256)throw new Error('Release artifact changed: '+entry.file)}
await mkdir(profile,{recursive:true});const packageFile=join(profile,'package.json');let pkg
try{pkg=JSON.parse(await readFile(packageFile,'utf8'))}catch(error){if(error.code!=='ENOENT')throw error;pkg={name:'dsh-research-profile',private:true}}
const backup=join(profile,'.anysolvers-install-backups',new Date().toISOString().replaceAll(':','-'));await mkdir(backup,{recursive:true})
for(const filename of ['package.json','package-lock.json','pnpm-lock.yaml','cordis.patch.yml']){try{await copyFile(join(profile,filename),join(backup,filename))}catch(error){if(error.code!=='ENOENT')throw error}}
const files=packages.map(p=>join(artifacts,p.file));let pnpm=false;try{await stat(join(profile,'pnpm-lock.yaml'));pnpm=true}catch{}
await writeFile(packageFile,JSON.stringify(pkg,null,2)+'\n')
const result=spawnSync(pnpm?'pnpm':'npm',pnpm?['add','--ignore-scripts',...files]:['install','--ignore-scripts','--no-audit','--no-fund',...files],{cwd:profile,stdio:'inherit',shell:false})
if(result.status!==0)throw new Error(`Dependency install failed; profile backup retained at ${backup}. No DSH process was touched.`)
const installedRequire=createRequire(packageFile),runtime=await import(pathToFileURL(installedRequire.resolve('@onefav/research-runtime')).href)
for(const entry of packages){const identity=await runtime.artifactIdentity(dirname(installedRequire.resolve(entry.name+'/package.json')));if(identity.digest!==entry.implementationDigest)throw new Error('Installed package content differs from release: '+entry.name)}
pkg=JSON.parse(await readFile(packageFile,'utf8'));pkg.dsh??={};pkg.dsh.profile??={};const bundles=new Set(pkg.dsh.profile.bundles??[]);bundles.add('@onefav/anysolvers');if(args.includes('--fixture'))bundles.add('@onefav/dynamic-research-fixture');pkg.dsh.profile.bundles=[...bundles];await writeFile(packageFile,JSON.stringify(pkg,null,2)+'\n')
console.log(JSON.stringify({installed:packages.map(p=>({name:p.name,version:p.version,sha256:p.sha256})),profile,backup,restartRequired:true,startedResearch:false},null,2))
