const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');

const html = readFileSync(join(__dirname, '../app/ui.html'), 'utf8');
const start = html.indexOf('function renderScript()');
const source = html.slice(start, html.indexOf('/* ---------- cast ---------- */', start));

function setup(overrides = {}) {
 const editor = {value:'', defaultValue:'', dataset:{}};
 const button = {};
 const ctx = vm.createContext({
  S:{project_id:'project', settings:{active_episode:'1', script:'Saved scene'}},
  $:selector=>selector==='#script'?editor:button,
  ...overrides,
 });
 vm.runInContext(source, ctx);
 return {ctx, editor, button};
}

test('a state refresh preserves unsaved script text and loads the next episode', () => {
 const {ctx, editor} = setup();
 ctx.renderScript();
 assert.equal(editor.value, 'Saved scene');
 editor.value = 'Unsaved direction';
 ctx.renderScript();
 assert.equal(editor.value, 'Unsaved direction');
 ctx.S.settings = {active_episode:'2', script:'Another episode'};
 ctx.renderScript();
 assert.equal(editor.value, 'Another episode');
});

test('script planning skips state refresh and adds only the reviewed selection', async () => {
 const requests = [];
 let dialog, currentTab;
 const {ctx, editor, button} = setup({
  post:async (_url, body, options) => {
   requests.push({body, options});
   return body.preview?{rows:[{title:'First', duration:4}, {title:'Second', duration:6}], token:'reviewed'}:{made:1};
  },
  document:{querySelectorAll:()=>[{dataset:{scriptIndex:'1'}}]},
  esc:value=>value,
  scriptShotDetails:()=>'',
  modal:markup=>{dialog = markup},
  closeModal:()=>{}, toast:()=>{}, go:tab=>{currentTab = tab},
 });
 editor.value = 'Unsaved direction';
 await ctx.draftShots();
 assert.match(dialog, /Add selected shots/);
 assert.equal(requests[0].options.refresh, false);
 assert.equal(requests[0].body.script, 'Unsaved direction');
 await button.onclick({currentTarget:button});
 assert.deepEqual(JSON.parse(JSON.stringify(requests[1].body)), {script:'Unsaved direction', token:'reviewed', indices:[1]});
 assert.equal(currentTab, 'shots');
});
