
class Component extends DCLogic {
  state = {
    page:'resellers', loaded: false,
    filter:null, openR:null, openS:null,
    showDoneR:false, showDoneS:false,
    items:{}, accounts:{}, actedR:0, actedS:0,
    removedIds:[], extraAccounts:[], showAddForm:false, newHandle:'', removeWarn:null,
  };

  adminBase = [
    {id:'ig_001', handle:'fleek_vintage_uk', cap:40, used:14, live:31},
    {id:'ig_002', handle:'fleek_archive_eu', cap:40, used:20, live:12},
    {id:'ig_003', handle:'fleek_sourcing',   cap:40, used:40, live:23},
  ];

  componentDidMount(){
    const init=()=>{
      const D=window.FLEEK_DATA; if(!D||this.state.loaded)return;
      this.D=D;
      const order=[...D.resellers.replies, ...D.resellers.followups, ...D.resellers.newout].map(x=>x.id);
      this.qOrder=order; this.numMap={}; order.forEach((id,i)=>this.numMap[id]=i+1);
      this.rmap={}; [...D.resellers.replies,...D.resellers.followups,...D.resellers.newout].forEach(x=>this.rmap[x.id]=x);
      this.sOrder=[]; this.smap={}; D.shops.cities.forEach(c=>c.shops.forEach(s=>{this.sOrder.push(s.id); this.smap[s.id]=s;}));
      const accounts={}; D.accounts.forEach(a=>accounts[a.id]=a.used);
      this.setState({loaded:true, accounts, openR:order[0]||null, openS:this.sOrder[0]||null});
    };
    if(window.FLEEK_DATA) init();
    window.addEventListener('fleekdata', init, {once:true});
    if(!window.FLEEK_DATA){let n=0;const t=setInterval(()=>{if(window.FLEEK_DATA){clearInterval(t);init();}else if(++n>60)clearInterval(t);},80);}
  }

  acct(id){ return this.D.accounts.find(a=>a.id===id); }
  S(id){ return this.state.items[id]||{}; }

  nextActive(order, items, fromId){
    const idx=order.indexOf(fromId);
    for(let k=idx+1;k<order.length;k++){const it=items[order[k]]; if(!it||!it.terminal)return order[k];}
    for(let k=0;k<order.length;k++){const it=items[order[k]]; if((!it||!it.terminal)&&order[k]!==fromId)return order[k];}
    return null;
  }
  // reseller
  toggleR(id){ this.setState(s=>({openR:s.openR===id?null:id})); }
  copyR(id,draft){ try{navigator.clipboard&&navigator.clipboard.writeText(draft);}catch(e){} this.setState(s=>({items:{...s.items,[id]:{...s.items[id],copied:true}}})); }
  markSentR(id,account){
    this.setState(s=>{
      const items={...s.items,[id]:{...s.items[id],terminal:true,result:'Sent'}};
      const accounts={...s.accounts}; let acc=account;
      if(!acc){ const free=this.D.accounts.filter(a=>accounts[a.id]<a.cap).sort((x,y)=>accounts[x.id]-accounts[y.id])[0]; acc=free?free.id:null; }
      if(acc){ accounts[acc]=Math.min(accounts[acc]+1,this.acct(acc).cap); items[id].assigned=acc; items[id].result='Sent · @'+this.acct(acc).handle; }
      return {items,accounts,openR:this.nextActive(this.qOrder,items,id),actedR:s.actedR+1};
    });
  }
  skipR(id){ this.setState(s=>{const items={...s.items,[id]:{...s.items[id],terminal:true,result:'Skipped'}};return {items,openR:this.nextActive(this.qOrder,items,id),actedR:s.actedR+1};}); }
  chipR(id,label){ this.setState(s=>{const items={...s.items,[id]:{...s.items[id],terminal:true,result:label}};return {items,openR:this.nextActive(this.qOrder,items,id),actedR:s.actedR+1};}); }
  replyToggleR(id){ this.setState(s=>({items:{...s.items,[id]:{...s.items[id],replyOpen:!(s.items[id]&&s.items[id].replyOpen)}}})); }
  replyInputR(id,v){ this.setState(s=>({items:{...s.items,[id]:{...s.items[id],replyText:v}}})); }
  replySaveR(id){ this.setState(s=>({items:{...s.items,[id]:{...s.items[id],replyOpen:false,inboundLogged:(s.items[id]&&s.items[id].replyText)||''}}})); }
  // shops
  toggleS(id){ this.setState(s=>({openS:s.openS===id?null:id})); }
  copyS(id,draft){ try{navigator.clipboard&&navigator.clipboard.writeText(draft);}catch(e){} this.setState(s=>({items:{...s.items,[id]:{...s.items[id],copied:true}}})); }
  markSentS(id){ this.setState(s=>{const items={...s.items,[id]:{...s.items[id],terminal:true,result:'Email sent'}};return {items,openS:this.nextActive(this.sOrder,items,id),actedS:s.actedS+1};}); }
  skipS(id){ this.setState(s=>{const items={...s.items,[id]:{...s.items[id],terminal:true,result:'Skipped'}};return {items,openS:this.nextActive(this.sOrder,items,id),actedS:s.actedS+1};}); }
  answeredS(id){ this.setState(s=>({items:{...s.items,[id]:{...s.items[id],answered:true}}})); }
  noAnswerS(id){ this.setState(s=>{const items={...s.items,[id]:{...s.items[id],terminal:true,result:'No answer — next attempt scheduled'}};return {items,openS:this.nextActive(this.sOrder,items,id),actedS:s.actedS+1};}); }
  chipS(id,label,track){
    let result=label;
    if(label==='Visit booked') result = track==='call'?'Closed — visit booked via call':'Closed — visit booked';
    else if(label==='Call booked') result='Closed — call booked';
    this.setState(s=>{const items={...s.items,[id]:{...s.items[id],terminal:true,result}};return {items,openS:this.nextActive(this.sOrder,items,id),actedS:s.actedS+1};});
  }
  replyToggleS(id){ this.setState(s=>({items:{...s.items,[id]:{...s.items[id],replyOpen:!(s.items[id]&&s.items[id].replyOpen)}}})); }
  replyInputS(id,v){ this.setState(s=>({items:{...s.items,[id]:{...s.items[id],replyText:v}}})); }
  // downloads
  downloadCSV(name,rows){
    const csv=rows.map(r=>r.map(c=>'"'+String(c==null?'':c).replace(/"/g,'""')+'"').join(',')).join('\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
    const a=document.createElement('a'); a.href=url; a.download=name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1500);
  }
  exportVisit(city){
    const c=this.D.shops.cities.find(x=>x.city===city);
    const rows=[['Shop','City','Phone','Last stage']].concat(c.shops.map(s=>[s.store,s.city,s.phone||'—',s.dueLine]));
    this.downloadCSV('fleek_visit_'+city.toLowerCase()+'.csv',rows);
  }
  downloadActivityR(){
    const rows=[['Handle','Action','Why']];
    this.qOrder.forEach(id=>{const it=this.state.items[id]; if(it&&it.terminal){const m=this.rmap[id]; rows.push(['@'+m.handle,it.result,m.why]);}});
    this.downloadCSV('fleek_resellers_activity.csv',rows);
  }
  downloadActivityS(){
    const rows=[['Shop','City','Action']];
    this.sOrder.forEach(id=>{const it=this.state.items[id]; if(it&&it.terminal){const m=this.smap[id]; rows.push([m.store,m.city,it.result]);}});
    this.downloadCSV('fleek_shops_activity.csv',rows);
  }

  buildAdminAccounts(){
    const st=this.state;
    const all=[...this.adminBase,...st.extraAccounts].filter(a=>!st.removedIds.includes(a.id));
    return all.map(a=>{
      const isLimit=a.used>=a.cap;
      const status=isLimit?'At limit':'Active';
      const pillColor=isLimit?'#E8563A':'#6B7280';
      return {
        id:a.id, handle:'@'+a.handle, usedStr:a.used+'/'+a.cap,
        pct:Math.round(a.used/a.cap*100)+'%', status, pillColor, pillBg:'#F4F4F3', live:a.live||0,
        warn:st.removeWarn===a.id,
        warnText:(a.live||0)+' leads are mid-conversation on this account. They will be flagged for review — nothing is reassigned.',
        onRemove:()=>this.setState({removeWarn:a.id}),
        confirmRemove:()=>this.setState(s=>({removeWarn:null,removedIds:[...s.removedIds,a.id]})),
        cancelRemove:()=>this.setState({removeWarn:null}),
      };
    });
  }

  mkRow(item){
    const s=this.S(item.id);
    const r={
      id:item.id, num:String(this.numMap[item.id]).padStart(2,'0'), handle:'@'+item.handle, reason:item.why,
      open:this.state.openR===item.id, toggle:()=>this.toggleR(item.id),
      isStall:!!item.isStall, draft:item.draft,
      lastInbound:s.inboundLogged||item.lastInbound, hasInbound:!!(s.inboundLogged||item.lastInbound),
      copied:!!s.copied, notCopied:!s.copied,
      copy:()=>this.copyR(item.id,item.draft), markSent:()=>this.markSentR(item.id,item.account), skip:()=>this.skipR(item.id),
      replyOpen:!!s.replyOpen, replyClosed:!s.replyOpen, replyText:s.replyText||'',
      replyToggle:()=>this.replyToggleR(item.id), replyInput:e=>this.replyInputR(item.id,e.target.value), replySave:()=>this.replySaveR(item.id),
    };
    if(item.band==='reply') r.chips=['Keep talking','Call booked','Not now','Wrong person','Lost'].map(l=>({label:l,onClick:()=>this.chipR(item.id,l)}));
    return r;
  }
  groupByAccount(items){
    const active=items.filter(it=>!this.S(it.id).terminal);
    const filt=this.state.filter?active.filter(it=>it.account===this.state.filter):active;
    const groups=[];
    this.D.accounts.forEach(a=>{const rows=filt.filter(it=>it.account===a.id).map(it=>this.mkRow(it)); if(rows.length)groups.push({key:a.id,label:'Sending from @'+a.handle,rows});});
    return {groups,count:filt.length};
  }
  mkShop(item){
    const s=this.S(item.id);
    const o={
      id:item.id, store:item.store, city:item.city, dueLine:item.dueLine, phone:item.phone||'—', draft:item.draft, otherTrack:item.otherTrack,
      open:this.state.openS===item.id, toggle:()=>this.toggleS(item.id),
      isEmail:item.track==='email', isCall:item.track==='call',
      copied:!!s.copied, notCopied:!s.copied,
      copy:()=>this.copyS(item.id,item.draft), markSent:()=>this.markSentS(item.id), skip:()=>this.skipS(item.id),
      answered:!!s.answered, notAnswered:!s.answered, answeredAct:()=>this.answeredS(item.id), noAnswer:()=>this.noAnswerS(item.id),
      replyOpen:!!s.replyOpen, replyClosed:!s.replyOpen, replyText:s.replyText||'',
      replyToggle:()=>this.replyToggleS(item.id), replyInput:e=>this.replyInputS(item.id,e.target.value),
      callChips:['Visit booked','Interested — follow up','Not now','Wrong number','Lost'].map(l=>({label:l,onClick:()=>this.chipS(item.id,l,'call')})),
      emailChips:['Keep talking','Visit booked','Call booked','Not now','Lost'].map(l=>({label:l,onClick:()=>this.chipS(item.id,l,'email')})),
    };
    return o;
  }

  acctView(a){
    const used=this.state.accounts[a.id], isLimit=used>=a.cap;
    return { id:a.id, handle:'@'+a.handle, used, cap:a.cap, usedStr:used+'/'+a.cap, pct:Math.round(used/a.cap*100)+'%',
      status:isLimit?'At limit':'Active', pillColor:isLimit?'#E8563A':'#6B7280', pillBg:isLimit?'#FBF1EE':'#F4F4F3',
      ring:this.state.filter===a.id?'#E8563A':'#EDEDEB', active:this.state.filter===a.id,
      onClick:()=>this.setState(s=>({filter:s.filter===a.id?null:a.id})) };
  }

  renderVals(){
    if(!this.state.loaded||!this.D) return {notLoaded:true, loaded:false};
    const D=this.D, st=this.state;
    const v={ loaded:true, notLoaded:false,
      isResellers:st.page==='resellers', isShops:st.page==='shops', isAdmin:st.page==='admin',
      goR:()=>this.setState({page:'resellers'}), goS:()=>this.setState({page:'shops'}), goAdmin:()=>this.setState({page:'admin'}),
      tabRColor:st.page==='resellers'?'#1A1A1A':'#6B7280', tabSColor:st.page==='shops'?'#1A1A1A':'#6B7280',
      tabAdminColor:st.page==='admin'?'#1A1A1A':'#6B7280',
      tabRUnderline:st.page==='resellers'?'inset 0 -2px 0 #E8563A':'none',
      tabSUnderline:st.page==='shops'?'inset 0 -2px 0 #E8563A':'none',
    };
    // accounts (reseller bar)
    v.accounts=D.accounts.map(a=>this.acctView(a));
    v.hasFilter=!!st.filter;
    v.filterLabel=st.filter?('@'+this.acct(st.filter).handle):'';
    v.clearFilter=()=>this.setState({filter:null});
    // bands
    const reply=this.groupByAccount(D.resellers.replies);
    const fu=this.groupByAccount(D.resellers.followups);
    const newActive=D.resellers.newout.filter(it=>!this.S(it.id).terminal);
    const newFilt=st.filter?[]:newActive;
    v.replyGroups=reply.groups; v.replyCount=reply.count; v.replyHas=reply.count>0;
    v.fuGroups=fu.groups; v.fuCount=fu.count; v.fuHas=fu.count>0;
    v.newGroups=newFilt.length?[{key:'u',label:'Unassigned · load-balances to free budget',rows:newFilt.map(it=>this.mkRow(it))}]:[];
    v.newCount=newFilt.length; v.newHas=newFilt.length>0;
    v.allCaughtR=(reply.count+fu.count+newFilt.length)===0;
    // progress + done
    const totalR=D.resellers.doneStart + (D.resellers.replies.length+D.resellers.followups.length+D.resellers.newout.length);
    v.progressTextR=(D.resellers.doneStart+st.actedR)+' of '+totalR+' done today';
    v.doneCountR=D.resellers.doneStart+st.actedR;
    v.showActivityR=st.actedR>0;
    v.toggleDoneR=()=>this.setState(s=>({showDoneR:!s.showDoneR}));
    v.showDoneR=st.showDoneR; v.doneArrowR=st.showDoneR?'▾':'▸';
    v.doneRowsR=this.qOrder.filter(id=>this.S(id).terminal).map(id=>({handle:'@'+this.rmap[id].handle,result:this.S(id).result}));
    v.doneEarlierR=D.resellers.doneStart+' completed earlier today';
    v.downloadR=()=>this.downloadActivityR();
    // shops
    v.cities=D.shops.cities.map(c=>{
      const shops=c.shops.filter(it=>!this.S(it.id).terminal).map(it=>this.mkShop(it));
      return {city:c.city, shops, showPrompt:!!c.showPrompt&&shops.length>0, warmCount:c.warmCount, promptText:c.warmCount+' shops warm in '+c.city, exportVisit:()=>this.exportVisit(c.city)};
    }).filter(c=>c.shops.length>0);
    v.allCaughtS=v.cities.length===0;
    const totalS=D.shops.doneStart+D.shops.total;
    v.progressTextS=(D.shops.doneStart+st.actedS)+' of '+totalS+' done today';
    v.doneCountS=D.shops.doneStart+st.actedS;
    v.showActivityS=st.actedS>0;
    v.toggleDoneS=()=>this.setState(s=>({showDoneS:!s.showDoneS}));
    v.showDoneS=st.showDoneS; v.doneArrowS=st.showDoneS?'▾':'▸';
    v.doneRowsS=this.sOrder.filter(id=>this.S(id).terminal).map(id=>({store:this.smap[id].store,result:this.S(id).result}));
    v.doneEarlierS=D.shops.doneStart+' completed earlier today';
    v.downloadS=()=>this.downloadActivityS();
    // admin
    v.admin={
      showAddForm:st.showAddForm, showAddBtn:!st.showAddForm,
      newHandle:st.newHandle||'',
      handleInput:e=>this.setState({newHandle:e.target.value}),
      addAccount:()=>this.setState({showAddForm:true,newHandle:''}),
      cancelAdd:()=>this.setState({showAddForm:false,newHandle:''}),
      saveAccount:()=>{
        const h=(st.newHandle||'').replace(/^@/,'').trim(); if(!h)return;
        const id='ig_'+String(Date.now()).slice(-4);
        this.setState(s=>({extraAccounts:[...s.extraAccounts,{id,handle:h,cap:40,used:0,live:0}],showAddForm:false,newHandle:''}));
      },
    };
    v.adminAccounts=this.buildAdminAccounts();
    return v;
  }
}
