/** Build a standalone Git-installable DSH UI bundle; no install-time shell hooks. */
import {mkdir,copyFile,readFile,writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {resolve,join} from 'node:path'
const root=resolve(fileURLToPath(new URL('../',import.meta.url)))
const target=resolve(process.argv[2]??join(root,'.state/release/dsh-plugin'))
await mkdir(join(target,'lib'),{recursive:true})
for(const path of ['host.mjs','cordis.patch.yml','lib/client.js'])await copyFile(join(root,'packages/anysolvers-im',path),join(target,path))
const pkg=JSON.parse(await readFile(join(root,'packages/anysolvers-im/package.json'),'utf8'))
pkg.version=JSON.parse(await readFile(join(root,'package.json'),'utf8')).version
pkg.engines={node:'>=22.19.0',dsh:'0.2.1-rc.5'}
pkg.files=['host.mjs','cordis.patch.yml','lib','README.md']
await writeFile(join(target,'package.json'),JSON.stringify(pkg,null,2)+'\n')
await writeFile(join(target,'README.md'),`# AnySolvers IM\n\nInstall from the pinned plugin release branch/tag in https://github.com/OneFav/anysolvers-next .\n\nAsk your installation agent to follow [INSTALL.md](https://github.com/OneFav/anysolvers-next/blob/main/INSTALL.md) for the independent backend. Set this bundle's \`instance\` configuration to the instance directory, or set \`ANYSOLVERS_INSTANCE\` before starting DSH. Credentials and research data never live inside this plugin. Closing DSH does not stop services.\n`)
console.log(JSON.stringify({plugin:target,version:pkg.version}))
