const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('browser-extension/content.js','utf8');
const item={customerName:'친구플라워',matchedProductName:'Marguerite CHINA / 마가렛 400g',quantity:8,sourceUnit:'단',orderYear:2026,orderWeek:39,managerName:'정재훈',note:'kakao-defect:불량-CB737B8B6531:1'};
async function check(change={}) {
 let listener;
 const cells=['46',item.managerName,item.customerName,'기타','중국 · '+item.matchedProductName,'8단','','',item.note,'저장 완료','-','편집'];
 if(change.quantity)cells[5]=change.quantity;
 if(change.note)cells[8]=change.note;
 if(change.status)cells[9]=change.status;
 const row={innerText:cells.join(' '),querySelectorAll:()=>cells.map(innerText=>({innerText})),getBoundingClientRect:()=>({x:0,y:100,width:1000,height:80,left:0,right:1000,top:100,bottom:change.offscreen?1000:180})};
 const document={querySelectorAll:selector=>selector==='strong'?[{innerText:`영업 입력 완료 목록 — ${change.year||2026}년 39차`}]:selector==='tbody tr'?[row]:[]};
 vm.runInNewContext(source,{chrome:{runtime:{id:'test',onMessage:{addListener:f=>listener=f}}},location:{origin:'https://nenovaweb.com',pathname:'/sales/defect-deductions'},document,innerWidth:1200,innerHeight:800,Date,setTimeout});
 return new Promise(resolve=>listener({type:'nenova-defect-screenshot-check',job:{expires_at:Date.now()/1000+30,request_id:'불량-CB737B8B6531',params:[item]}},{id:'test'},resolve));
}
(async()=>{
 assert.equal((await check()).ready,true);
 for(const bad of [{quantity:'18단'},{note:'other'},{status:'미저장'},{year:2025},{offscreen:true}])assert.equal((await check(bad)).ready,false);
 console.log('6 screenshot DOM checks passed');
})();
