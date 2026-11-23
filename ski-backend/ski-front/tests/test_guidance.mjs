import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
const source = await readFile(new URL('../utils/guidance.js', import.meta.url), 'utf8')
const { normalizeGuidance, validateSceneIntent, safeLearningUrl } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
let assertions = 0
const equal = (actual, expected) => { assert.deepEqual(actual, expected); assertions++ }
const match = (actual, expected) => { assert.match(actual, expected); assertions++ }
const result = normalizeGuidance({suggestions: Array.from({length: 8}, (_,i)=>({id:''+i,label:'学习',question:'新手怎么练？'})),scenes:[{resort_id:'wanlong',name:'万龙'},{resort_id:'../bad',name:'错误'}],learning:{notice:'没有核验视频',resources:[{title:'安全课程',url:'https://example.com/lesson',source:'来源',updated_at:'2026-01-01'},{title:'恶意',url:'javascript:alert(1)',source:'来源',updated_at:'2026'}]}})
equal(result.suggestions.length,3)
equal(result.scenes.length,1)
equal(result.learning.resources.length,1)
const unsafeUrls = ['https://user:password@example.com', 'http://example.com', 'javascript:alert(1)', 'data:text/html,test', '//example.com', 'https://', 'https:example.com', ' https://example.com', 'https://example.com/has space', 'https://example.com/\npath', 'https://example.com/\\path', 'https://example.com/' + 'x'.repeat(2048), null, {}]
for (const url of unsafeUrls) equal(safeLearningUrl(url), '')
equal(safeLearningUrl('https://example.com/original?ref=ski'), 'https://example.com/original?ref=ski')
equal(validateSceneIntent({resort_id:'not-covered'},['wanlong']),null)
equal(validateSceneIntent({resort_id:'wanlong',filter:'beginner'},['wanlong']),{resort_id:'wanlong',filter:'beginner'})
equal(validateSceneIntent({resort_id:'beidahu',filter:'all',focus:'H6岗梁道'},['beidahu']),{resort_id:'beidahu',filter:'all',focus:'H6岗梁道'})
equal(validateSceneIntent({resort_id:'wanlong',filter:'bad'},['wanlong']),{resort_id:'wanlong',filter:'all'})
const empty = {suggestions:[],scenes:[],learning:{notice:'',resources:[]},media:[]}
equal(normalizeGuidance(null),empty)
equal(normalizeGuidance({media:{}}),empty)
const media = {title:'万龙雪场视频',resort_id:'wanlong',kind:'video',url:'https://example.com/original',source:'官方页面',published_at:'2026-01-01',checked_at:'2026-03-01',verification:'page_checked',rights:'link_only',notice:'仅核验页面，未观看视频；请在原始页面确认时效。'}
equal(normalizeGuidance({media:[media]}).media,[media])
const { published_at, ...undated } = media
equal(normalizeGuidance({media:[undated]}).media[0].published_at,'未注明')
equal(normalizeGuidance({media:Array(8).fill(media)}).media.length,3)
for (const kind of ['photo','video','map']) equal(normalizeGuidance({media:[{...media,kind,verification:'content_checked',rights:'authorized'}]}).media.length,1)
for (const field of ['title','resort_id','kind','url','source','checked_at','verification','rights','notice']) {
 const incomplete = {...media}; delete incomplete[field]
 equal(normalizeGuidance({media:[incomplete]}).media,[])
 equal(normalizeGuidance({media:[{...media,[field]:'   '}]}).media,[])
}
for (const [field,value] of [['kind','iframe'],['verification','viewed'],['rights','public'],['resort_id','../wanlong'],['verification',{}],['rights',[]]]) equal(normalizeGuidance({media:[{...media,[field]:value}]}).media,[])
for (const url of unsafeUrls) equal(normalizeGuidance({media:[{...media,url}]}).media,[])
equal(normalizeGuidance({media:[null,'bad',[]]}).media,[])
const page = await readFile(new URL('../pages/index/index.vue',import.meta.url),'utf8')
match(page,/!m.streaming && !m.failed && !m.stopped && m.guidance/)
match(page,/case 'reset':[\s\S]*?reply.guidance = null/)
match(page,/case 'error':[\s\S]*?reply.guidance = null/)
match(page,/currentReply.stopped = true; currentReply.guidance = null/)
match(page,/item.guidance = item.guidance && !item.failed && !item.stopped \? normalizeGuidance/)
match(page,/guidance: m.guidance \|\| null/)
match(page, /<MediaPanel v-if="m.guidance.media.length" :resources="m.guidance.media" compact/)
match(page, /档案参数 · 不是模型印象/)
match(page, /查看来源档案/)
match(page, /case 'replace':[\s\S]*?reply.raw = event.text/)
const brief = normalizeGuidance({run_brief:{title:'岗梁道',source:'官网雪道表',date:'2026-10-11',caveat:'两个口径',runs:[{name:'H6岗梁道',difficulty:'中级道',length_m:2269,vertical_m:584,slope_deg:13.8,note:'雪后常有软粉雪'},{name:'',difficulty:'初级道'},{length_m:-1}]}})
equal(brief.run_brief.runs.length,1)
equal(brief.run_brief.runs[0].name,'H6岗梁道')
equal(brief.run_brief.caveat,'两个口径')
equal('run_brief' in normalizeGuidance(null), false)
match(page, /const openLearning = openMediaPage/)
const panel = await readFile(new URL('../components/MediaPanel.vue', import.meta.url), 'utf8')
const actions = await readFile(new URL('../utils/media-actions.js', import.meta.url), 'utf8')
const helpers = await readFile(new URL('../utils/media.js', import.meta.url), 'utf8')
match(panel, /resource.published_at/)
match(panel, /resource.checked_at/)
match(helpers, /不代表已观看视频或核验画面\/教学内容/)
match(panel, /仅链接 · 无转载授权/)
match(actions, /noopener noreferrer/)
equal(/<(?:iframe|video)\b|download|v-html/.test(panel), false)
console.log('guidance: ' + assertions + ' assertions passed')
