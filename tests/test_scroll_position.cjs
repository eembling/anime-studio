const{test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const{readFileSync}=require('node:fs');
const script=readFileSync(require('node:path').join(__dirname,'../app/ui.html'),'utf8').split('<script>')[1].split('</script>')[0];
function setup(){
 const main={scrollTop:1800,scrollLeft:12},frames=[],events={};
 const ctx={tab:'shots',$:s=>main,requestAnimationFrame:fn=>frames.push(fn),render:()=>{main.scrollTop=0;main.scrollLeft=0},
  document:{addEventListener:(type,fn)=>events[type]=fn,querySelectorAll:()=>[]}};
 vm.createContext(ctx);vm.runInContext(script.slice(script.indexOf('const TAB_SCROLL='),script.indexOf('const STATE_ETAGS=')),ctx);
 return{ctx,main,events,flush:()=>{while(frames.length)frames.shift()()}};
}
test('DOM refresh retains vertical and horizontal position, including next-frame layout changes',()=>{
 const{ctx,main,flush}=setup();ctx.preserveMainScroll(()=>{main.scrollTop=0;main.scrollLeft=0});
 assert.equal(main.scrollTop,1800);assert.equal(main.scrollLeft,12);
 main.scrollTop=0;flush();assert.equal(main.scrollTop,1800);
});
test('a user scroll after refresh is never overridden by a pending restoration',()=>{
 const{ctx,main,flush,events}=setup();ctx.preserveMainScroll(()=>{});events.wheel();main.scrollTop=2100;
 flush();assert.equal(main.scrollTop,2100);
});
test('switching tabs remembers positions and reselecting current tab does not reset it',()=>{
 const{ctx,main,flush}=setup();ctx.go('shots');assert.equal(main.scrollTop,1800);
 ctx.go('voices');assert.equal(main.scrollTop,0);main.scrollTop=600;
 ctx.go('shots');assert.equal(main.scrollTop,1800);flush();assert.equal(main.scrollTop,1800);
 ctx.go('voices');assert.equal(main.scrollTop,600);
});
test('nested refreshes restore the outer viewport and do not fight later navigation',()=>{
 const{ctx,main,flush}=setup();ctx.preserveMainScroll(()=>{main.scrollTop=100;ctx.preserveMainScroll(()=>main.scrollTop=0)});
 assert.equal(main.scrollTop,1800);ctx.go('voices');flush();assert.equal(main.scrollTop,0);
});
test('explicit jump opens the shot tab and is not undone by repaint restoration',()=>{
 const{ctx,main,flush}=setup();ctx.tab='production';
 const target={scrollIntoView:()=>main.scrollTop=900,classList:{remove(){},add(){}},offsetWidth:10};
 ctx.$=s=>s==='main'?main:target;ctx.renderShots=()=>ctx.preserveMainScroll(()=>main.scrollTop=0);
 const jump=script.slice(script.indexOf('function jump('),script.indexOf('\nfunction setFilter',script.indexOf('function jump(')));
 vm.runInContext(jump,ctx);ctx.jump(1);flush();assert.equal(ctx.tab,'shots');assert.equal(main.scrollTop,900);
});
function card(id,html=id){return{id,outerHTML:html,replaceWith(fresh){const b=this.board;b.children.splice(b.children.indexOf(this),1,fresh);fresh.board=b},remove(){this.board.children.splice(this.board.children.indexOf(this),1)}}}
test('keyed shot updates retain unchanged card/video nodes and only replace changed cards',()=>{
 const first=card('cut-1'),second=card('cut-2');first.playing=true;
 const board={children:[first,second],insertBefore(node,reference){const old=this.children.indexOf(node);if(old>=0)this.children.splice(old,1);const at=reference?this.children.indexOf(reference):this.children.length;this.children.splice(at,0,node);node.board=this}};
 first.board=second.board=board;
 const ctx={$:()=>board,document:{createElement:()=>({content:{children:[]},set innerHTML(value){this.content.children=JSON.parse(value).map(([id,html])=>card(id,html))}})}};
 vm.createContext(ctx);vm.runInContext(script.slice(script.indexOf('function updateShotCards('),script.indexOf('function renderShots(){')),ctx);
 ctx.updateShotCards(JSON.stringify([['cut-1','cut-1'],['cut-2','changed']]));
 assert.equal(board.children[0],first);assert.equal(first.playing,true);assert.notEqual(board.children[1],second);
 ctx.updateShotCards(JSON.stringify([['cut-2','changed'],['cut-3','new']]));
 assert.deepEqual(board.children.map(n=>n.id),['cut-2','cut-3']);
});
