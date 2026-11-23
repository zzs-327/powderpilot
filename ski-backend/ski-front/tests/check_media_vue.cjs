const fs = require('node:fs')
const compilerPath = process.argv[2]
if (!compilerPath) throw new Error('传入本机已安装的 @vue/compiler-sfc 路径（不自动安装）')
const { parse, compileScript, compileTemplate } = require(compilerPath)
for (const relative of ['components/MediaPanel.vue', 'pages/resorts/resorts.vue', 'pages/trip/trip.vue', 'pages/index/index.vue']) {
  const filename = require('node:path').join(__dirname, '..', relative)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  if (errors.length) throw new Error(relative + ': ' + errors.join(';'))
  const script = compileScript(descriptor, { id: relative })
  const template = compileTemplate({ source: descriptor.template.content, filename, id: relative, compilerOptions: { bindingMetadata: script.bindings } })
  if (template.errors.length) throw new Error(relative + ': ' + template.errors.join(';'))
  console.log('SFC script/template OK: ' + relative)
}
