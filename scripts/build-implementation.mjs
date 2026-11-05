import {manifest} from './manifest.mjs'
await manifest(new URL('../',import.meta.url),['lib','runtime','cordis.patch.yml','package.json'])
