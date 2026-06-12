
class Component extends DCLogic {
  state = {
    page:'resellers', loaded: false,
    filter:null, openR:null, openS:null,
    showDoneR:false, showDoneS:false,
    items:{}, actedR:0, actedS:0,
    igAccounts:[], showAddForm:false, newHandle:'', removeWarn:null,
    // JS-side account overrides for leads (id → accountId), used for redistribution
    leadAssignments:{},
    // notification shown after upload simulation or account add
    uploadMsg:null, uploadLoading:false,
  };

  componentDidMount(){
    const init=()=>{
      const D=window.FLEEK_DATA; if(!D||this.state.loaded)return;
      this.D=D;
      const order=[...D.resellers.replies,...D.resellers.followups,...D.resellers.newout].map(x=>x.id);
      this.qOrder=order; this.numMap={}; order.forEach((id,i)=>this.numMap[id]=i+1);
      this.rmap={}; [...D.resellers.replies,...D.resellers.followups,...D.resellers.newout].forEach(x=>this.rmap[x.id]=x);
      this.sOrder=[]; this.smap={}; D.shops.cities.forEach(c=>c.shops.forEach(s=>{this.sOrder.push(s.id); this.smap[s.id]=s;}));
      const igAccounts=D.accounts.map(a=>({
        id:a.id, handle:a.handle, cap:a.cap, sentToday:a.used,
        midConvoCount:a.midConvoCount||0, status:(a.status||'active').toLowerCase(),
        inProgress:a.inProgress||0, queued:a.queued||0,
      }));
      this.setState({loaded:true, igAccounts, openR:order[0]||null, openS:this.sOrder[0]||null});
    };
    if(window.FLEEK_DATA) init();
    window.addEventListener('fleekdata', init, {once:true});
    if(!window.FLEEK_DATA){let n=0;const t=setInterval(()=>{if(window.FLEEK_DATA){clearInterval(t);init();}else if(++n>60)clearInterval(t);},80);}
  }

  acct(id){ return this.state.igAccounts.find(a=>a.id===id); }
  S(id){ return this.state.items[id]||{}; }

  // Returns the effective account ID for a lead — JS override wins over Python assignment
  getAssignedAccount(id, fallback){
    return this.state.leadAssignments[id] || fallback || null;
  }

  // Count active (non-terminal) leads managed by an account across all bands
  getManagedCount(accId){
    const all=[...this.D.resellers.replies,...this.D.resellers.followups,...this.D.resellers.newout];
    return all.filter(it=>
      !this.S(it.id).terminal &&
      (this.state.leadAssignments[it.id]||it.account)===accId
    ).length;
  }

  nextActive(order, items, fromId){
    const idx=order.indexOf(fromId);
    for(let k=idx+1;k<order.length;k++){const it=items[order[k]]; if(!it||!it.terminal)return order[k];}
    for(let k=0;k<order.length;k++){const it=items[order[k]]; if((!it||!it.terminal)&&order[k]!==fromId)return order[k];}
    return null;
  }

  // reseller
  toggleR(id){ this.setState(s=>({openR:s.openR===id?null:id})); }
  copyR(id,draft){ try{navigator.clipboard&&navigator.clipboard.writeText(draft);}catch(e){} this.setState(s=>({items:{...s.items,[id]:{...s.items[id],copied:true}}})); }

  markSentR(id,itemAccount){
    this.setState(s=>{
      const igAccounts=[...s.igAccounts];
      const items={...s.items,[id]:{...s.items[id],terminal:true,result:'Sent'}};
      // JS-side override takes precedence over Python-assigned account
      let accId=s.leadAssignments[id]||itemAccount;
      if(!accId){
        // fallback: pick active account with most remaining capacity
        const free=igAccounts.filter(a=>a.status==='active'&&a.sentToday<a.cap)
                             .sort((x,y)=>x.sentToday-y.sentToday)[0];
        accId=free?free.id:null;
      }
      if(accId){
        const idx=igAccounts.findIndex(a=>a.id===accId);
        if(idx>=0){
          igAccounts[idx]={...igAccounts[idx],sentToday:Math.min(igAccounts[idx].sentToday+1,igAccounts[idx].cap)};
        }
        items[id].assigned=accId;
        const acc=idx>=0?igAccounts[idx]:null;
        items[id].result='Sent · @'+(acc?acc.handle:accId);
      }
      return {items,igAccounts,openR:this.nextActive(this.qOrder,items,id),actedR:s.actedR+1};
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
    if(label==='Visit booked') result=track==='call'?'Closed — visit booked via call':'Closed — visit booked';
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
    const rows=[['Handle','Action','Account','Why']];
    this.qOrder.forEach(id=>{
      const it=this.state.items[id];
      if(it&&it.terminal){
        const m=this.rmap[id];
        const acc=it.assigned?this.acct(it.assigned):null;
        rows.push(['@'+m.handle,it.result,acc?('@'+acc.handle):'',m.why]);
      }
    });
    this.downloadCSV('fleek_resellers_activity.csv',rows);
  }
  downloadActivityS(){
    const rows=[['Shop','City','Action']];
    this.sOrder.forEach(id=>{const it=this.state.items[id]; if(it&&it.terminal){const m=this.smap[id]; rows.push([m.store,m.city,it.result]);}});
    this.downloadCSV('fleek_shops_activity.csv',rows);
  }

  buildAdminAccounts(){
    const st=this.state;
    return st.igAccounts.map(a=>{
      const isLimit=a.sentToday>=a.cap;
      const isPaused=a.status==='paused';
      const status=isPaused?'Paused':isLimit?'At limit':'Active';
      const pillColor=isPaused?'#9A9A95':isLimit?'#E8563A':'#6B7280';
      const managed=this.getManagedCount(a.id);
      return {
        id:a.id, handle:'@'+a.handle, usedStr:a.sentToday+'/'+a.cap,
        pct:Math.round(a.sentToday/a.cap*100)+'%', status, pillColor, pillBg:'#F4F4F3',
        live:a.midConvoCount||0, managed,
        inProgress:a.inProgress||0, queued:a.queued||0,
        warn:st.removeWarn===a.id,
        warnText:(a.midConvoCount||0)+' leads are mid-conversation on this account. They will be flagged for review — nothing is reassigned.',
        onRemove:()=>this.setState({removeWarn:a.id}),
        confirmRemove:()=>this.setState(s=>({removeWarn:null,igAccounts:s.igAccounts.filter(x=>x.id!==a.id)})),
        cancelRemove:()=>this.setState({removeWarn:null}),
      };
    });
  }

  mkRow(item){
    const s=this.S(item.id);

    // Channel tags with step counts — show next step to send (step+1), not completed count.
    // Reply band is the exception: show the step they actually replied at, guard against 0.
    const primaryIsDm=(item.primaryChannel||'dm')!=='email';
    const isReply = item.band === 'reply_needed';
    const dmDisplay = isReply ? (item.dmStep||0) : Math.min((item.dmStep||0)+1, 4);
    const emailDisplay = isReply ? (item.emailStep||0) : Math.min((item.emailStep||0)+1, 4);
    const dmTag = item.dmActive && (!isReply || dmDisplay > 0) ? `DM ${dmDisplay}/4` : null;
    const emailTag = item.emailActive && (!isReply || emailDisplay > 0) ? `EMAIL ${emailDisplay}/4` : null;
    const hasDmTag=!!dmTag;
    const hasEmailTag=!!emailTag;
    const dmTagClass=`ch-tag ${primaryIsDm?'ch-tag-primary':'ch-tag-secondary'}`;
    const emailTagClass=`ch-tag ${primaryIsDm?'ch-tag-secondary':'ch-tag-primary'}`;

    // Split why into italic status context + stats line
    const WHY=item.why||'';
    const STATUS_PREFIXES=['warm reply:','objection:','replied:','day-one'];
    let statusContext='';
    let statsLine=WHY;
    if(STATUS_PREFIXES.some(p=>WHY.startsWith(p))){
      const sep=WHY.indexOf(' · ');
      if(sep!==-1){statusContext=WHY.substring(0,sep);statsLine=WHY.substring(sep+3);}
      else{statusContext=WHY;statsLine='';}
    }
    // For follow-up stalls, replace with time-aware phrasing
    const inbound=s.inboundLogged||item.lastInbound;
    if(item.band==='follow_ups_due'&&inbound&&item.overdueDays>0){
      const d=item.overdueDays;
      statusContext=`replied ${d} day${d!==1?'s':''} ago — no follow-up sent`;
    }
    const hasStatusContext=!!statusContext;
    const hasStatsLine=!!statsLine;

    const r={
      id:item.id, num:String(this.numMap[item.id]).padStart(2,'0'), handle:'@'+item.handle, reason:WHY,
      open:this.state.openR===item.id, toggle:()=>this.toggleR(item.id),
      isStall:!!item.isStall, draft:item.draft,
      lastInbound:inbound, hasInbound:!!inbound,
      copied:!!s.copied, notCopied:!s.copied,
      copy:()=>this.copyR(item.id,item.draft), markSent:()=>this.markSentR(item.id,item.account), skip:()=>this.skipR(item.id),
      replyOpen:!!s.replyOpen, replyClosed:!s.replyOpen, replyText:s.replyText||'',
      replyToggle:()=>this.replyToggleR(item.id), replyInput:e=>this.replyInputR(item.id,e.target.value), replySave:()=>this.replySaveR(item.id),
      secondaryLine:item.secondaryLine||'', hasSecondary:!!(item.secondaryLine),
      dmTag:dmTag||'DM', emailTag:emailTag||'EMAIL',
      hasDmTag, hasEmailTag, dmTagClass, emailTagClass,
      statusContext, statsLine, hasStatusContext, hasStatsLine,
      markWon:()=>this.chipR(item.id,'Won'),
      markLost:()=>this.chipR(item.id,'Lost'),
    };
    if(item.band==='reply_needed') r.chips=['Call booked','Not now','Wrong person','Lost'].map(l=>({label:l,onClick:()=>this.chipR(item.id,l)}));
    else r.chips=[];
    r.hasChips=r.chips.length>0;
    r.isReply=item.band==='reply_needed';
    return r;
  }

  // Groups leads by their effective account (JS override → Python assignment)
  groupByAccount(items){
    const st=this.state;
    const active=items.filter(it=>!this.S(it.id).terminal);
    const filt=st.filter
      ? active.filter(it=>(st.leadAssignments[it.id]||it.account)===st.filter)
      : active;
    const groups=[];
    st.igAccounts.forEach(a=>{
      const rows=filt.filter(it=>(st.leadAssignments[it.id]||it.account)===a.id).map(it=>this.mkRow(it));
      if(rows.length) groups.push({key:a.id,label:'Sending from @'+a.handle,rows});
    });
    return {groups,count:filt.length};
  }

  mkShop(item){
    const s=this.S(item.id);
    const hasPhone=!!(item.phone&&item.phone!=='—');
    const emailPrimary=item.track!=='call';
    const emailTag=`EMAIL ${Math.min((item.emailStep||0)+1,4)}/4`;
    const callTag=`CALL ${Math.min((item.callStep||0)+1,2)}/2`;
    const emailTagClass=`ch-tag ${item.emailDueToday?'ch-tag-primary':'ch-tag-secondary'}`;
    const callTagClass=`ch-tag ${item.callDueToday?'ch-tag-primary':'ch-tag-secondary'}`;
    const o={
      id:item.id, store:item.store, city:item.city, phone:item.phone||'—', draft:item.draft,
      open:this.state.openS===item.id, toggle:()=>this.toggleS(item.id),
      isEmail:!!item.emailDueToday, isCall:!!(item.callDueToday&&hasPhone),
      copied:!!s.copied, notCopied:!s.copied,
      copy:()=>this.copyS(item.id,item.draft), markSent:()=>this.markSentS(item.id), skip:()=>this.skipS(item.id),
      answered:!!s.answered, notAnswered:!s.answered, answeredAct:()=>this.answeredS(item.id), noAnswer:()=>this.noAnswerS(item.id),
      replyOpen:!!s.replyOpen, replyClosed:!s.replyOpen,
      replyToggle:()=>this.replyToggleS(item.id),
      callChips:['Visit booked','Interested — follow up','Not now','Wrong number','Lost'].map(l=>({label:l,onClick:()=>this.chipS(item.id,l,'call')})),
      emailChips:['Keep talking','Visit booked','Call booked','Not now','Lost'].map(l=>({label:l,onClick:()=>this.chipS(item.id,l,'email')})),
      shopHasCall:hasPhone, noPhone:!hasPhone,
      emailTag, callTag, emailTagClass, callTagClass,
      emailDueLabel:item.emailDueLabel||'',
      callDueLabel:item.callDueLabel||'',
      emailSectionHdr:item.emailSectionHdr||'',
      callSectionHdr:item.callSectionHdr||'',
      callNotAttempted:!!item.callNotAttempted,
      hasCallSection:!!(hasPhone&&item.callSectionHdr),
      contextLine:item.why||'', hasContextLine:!!(item.why),
      markWon:()=>this.chipS(item.id,'Won','email'),
      markLost:()=>this.chipS(item.id,'Lost','email'),
    };
    return o;
  }

  acctView(a){
    const isLimit=a.sentToday>=a.cap, isPaused=a.status==='paused';
    const status=isPaused?'Paused':isLimit?'At limit':'Active';
    return {
      id:a.id, handle:'@'+a.handle, used:a.sentToday, cap:a.cap, usedStr:a.sentToday+'/'+a.cap,
      pct:Math.round(a.sentToday/a.cap*100)+'%',
      status, pillColor:isPaused?'#9A9A95':isLimit?'#E8563A':'#6B7280',
      pillBg:isLimit?'#FBF1EE':'#F4F4F3',
      ring:this.state.filter===a.id?'#E8563A':'#EDEDEB',
      onClick:()=>this.setState(s=>({filter:s.filter===a.id?null:a.id})),
      inProgress:a.inProgress||0, queued:a.queued||0,
    };
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

    // accounts bar — same shared igAccounts as Admin
    v.accounts=st.igAccounts.map(a=>this.acctView(a));
    v.hasFilter=!!st.filter;
    const filterAcc=st.igAccounts.find(a=>a.id===st.filter);
    v.filterLabel=filterAcc?('@'+filterAcc.handle):'';
    v.clearFilter=()=>this.setState({filter:null});

    // all three bands grouped by assigned account
    const reply=this.groupByAccount(D.resellers.replies);
    const fu=this.groupByAccount(D.resellers.followups);
    const newByAcct=this.groupByAccount(D.resellers.newout);

    v.replyGroups=reply.groups; v.replyCount=reply.count; v.replyHas=reply.count>0;
    v.fuGroups=fu.groups; v.fuCount=fu.count; v.fuHas=fu.count>0;
    v.newGroups=newByAcct.groups; v.newCount=newByAcct.count; v.newHas=newByAcct.count>0;
    v.allCaughtR=(reply.count+fu.count+newByAcct.count)===0;

    // progress + done
    const totalR=D.resellers.doneStart+(D.resellers.replies.length+D.resellers.followups.length+D.resellers.newout.length);
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
        const newAcc={id,handle:h,cap:40,sentToday:0,midConvoCount:0,status:'active',inProgress:0,queued:0};
        const updatedAccounts=[...st.igAccounts,newAcc];
        const activeAccounts=updatedAccounts.filter(a=>a.status==='active');

        // Redistribute all uncontacted (new_outreach) leads round-robin across every active account
        const allNewOut=this.D.resellers.newout.filter(it=>!this.S(it.id).terminal);
        const newAssignments={...st.leadAssignments};
        allNewOut.forEach((it,i)=>{
          newAssignments[it.id]=activeAccounts[i%activeAccounts.length].id;
        });

        // Recompute queued count per account from the new assignments
        const newOutCount={};
        allNewOut.forEach(it=>{ const a=newAssignments[it.id]; if(a) newOutCount[a]=(newOutCount[a]||0)+1; });
        const updatedAccountsWithQueued=updatedAccounts.map(a=>({...a,queued:newOutCount[a.id]||0}));

        // How many queued leads moved to the new account
        const movedToNew=newOutCount[id]||0;
        const msg=movedToNew>0
          ? `@${h} added · ${movedToNew} queued leads automatically assigned to this account`
          : `@${h} added to the pipeline`;

        this.setState({
          igAccounts:updatedAccountsWithQueued, leadAssignments:newAssignments,
          showAddForm:false, newHandle:'', uploadMsg:msg,
        });
        // Auto-dismiss notification after 8 seconds
        setTimeout(()=>this.setState(s=>s.uploadMsg===msg?{uploadMsg:null}:{}),8000);
      },

      // Upload area click — simulates a CSV upload for the demo
      uploadClick:()=>{
        if(this.state.uploadLoading) return;
        this.setState({uploadLoading:true, uploadMsg:null});
        setTimeout(()=>{
          // Use this.state (live) so we always see current accounts, not stale closure
          const active=this.state.igAccounts.filter(a=>a.status==='active');
          const total=28;
          const n=Math.max(active.length,1);
          const base=Math.floor(total/n);
          const parts=active.map((a,i)=>{
            const count=i===active.length-1?(total-base*(active.length-1)):base;
            return `@${a.handle} (${count} leads)`;
          });
          const msg=`✓ ${total} new leads added — assigned to ${parts.join(' · ')}`;
          this.setState({uploadLoading:false, uploadMsg:msg});
          setTimeout(()=>this.setState(s=>s.uploadMsg===msg?{uploadMsg:null}:{}),10000);
        },1400);
      },
      uploadLoading:st.uploadLoading,
      notUploading:!st.uploadLoading,
      uploadMsg:st.uploadMsg,
      hasUploadMsg:!!st.uploadMsg,
      dismissUploadMsg:()=>this.setState({uploadMsg:null}),

      resetDemo:()=>{
        const igAccounts=this.D.accounts.map(a=>({
          id:a.id, handle:a.handle, cap:a.cap, sentToday:a.used,
          midConvoCount:a.midConvoCount||0, status:(a.status||'active').toLowerCase(),
        }));
        // Full reset: accounts, all actions, all lead reassignments, all state
        this.setState({
          igAccounts, items:{}, actedR:0, actedS:0, filter:null,
          openR:this.qOrder[0]||null, openS:this.sOrder[0]||null,
          showDoneR:false, showDoneS:false,
          leadAssignments:{}, uploadMsg:null, uploadLoading:false,
          showAddForm:false, newHandle:'', removeWarn:null,
        });
      },
    };
    v.adminAccounts=this.buildAdminAccounts();
    return v;
  }
}
