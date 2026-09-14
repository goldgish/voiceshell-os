// esbuild 双入口构建脚本。
// host entry -> lib/index.mjs（Node ESM）
// client entry -> lib/client.js（浏览器 CJS，被 DSH 的 window.__ModuleLoader__.load 加载）
//
// client.js 格式借鉴 dsh-codex-ui 的 tsdown.config.ts：
//   window.__ModuleLoader__.load({ id, factory: (require) => {
//     var module = { exports: {} }; var exports = module.exports;
//     ...bundled cjs code...
//     return module.exports;
//   } });
import { build } from 'esbuild'

const packageId = '@local/dsh-voice-input'

const external = [
  'react',
  'react-dom',
  'react/jsx-runtime',
  '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-ui-conversation',
  '@deepseek-ai/dsh-client-ui-locale',
  '@deepseek-ai/dsh-client-connection',
  '@deepseek-ai/dsh-client-runtime',
]

// host entry（Node ESM）
await build({
  bundle: true,
  format: 'esm',
  platform: 'node',
  external,
  loader: { '.ts': 'ts', '.tsx': 'tsx' },
  target: 'es2022',
  logLevel: 'info',
  entryPoints: ['src/index.ts'],
  outfile: 'lib/index.mjs',
})

// client entry（浏览器 CJS + __ModuleLoader__ 包裹）
await build({
  bundle: true,
  format: 'cjs',
  external,
  loader: { '.ts': 'ts', '.tsx': 'tsx' },
  target: 'es2022',
  logLevel: 'info',
  entryPoints: ['src/client/index.ts'],
  outfile: 'lib/client.js',
  banner: {
    js: `window.__ModuleLoader__.load({ id: ${JSON.stringify(packageId)}, factory: (require) => { var module = { exports: {} }; var exports = module.exports;`,
  },
  footer: {
    js: `return module.exports; } });`,
  },
})
