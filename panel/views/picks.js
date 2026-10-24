/* Champ select: teams, enemy lane picker, and ranked recommendations. */
(function(L){
  var $=L.$,el=L.el,lane=L.lane,icon=L.icon,fmt=L.fmt,cls=L.cls,clear=L.clear,short=L.short;
  var openSet={},pickId=null,lastEnemies=[];

  function chipRow(box,list,kind){
    clear(box);
    (list||[]).forEach(function(c){
      var ch=el("div","chip "+kind+(c.tentative?" tent":"")+(c.is_me?" me":""));
      ch.title=c.name+(c.lane?" ("+c.lane+")":"");
      ch.appendChild(icon(c.name,c.icon));
      if(kind==="enemy"){
        ch.className+=(c.flex?" flex":"")+(pickId===c.id?" sel":"");
        ch.title=c.name+" ("+(c.manual?"set to ":"guessed ")+(c.lane||"?")+")"+(c.flex?" - flex pick":"")+": click to set lane";
        ch.appendChild(el("div","lane"+(c.manual?" manual":""),lane(c.lane)));
        ch.addEventListener("click",function(){pickId=pickId===c.id?null:c.id;renderEnemies(lastEnemies)});
      }
      box.appendChild(ch);
    });
  }
  function renderEnemies(list){
    lastEnemies=list||[];
    if(pickId!=null&&!lastEnemies.some(function(c){return c.id===pickId}))pickId=null;
    chipRow($("enemies"),lastEnemies,"enemy");
    $("enemyHint").textContent=lastEnemies.length?"· click to set lane":"";
    var box=$("lanePick");clear(box);
    var c=lastEnemies.filter(function(e){return e.id===pickId})[0];
    box.classList.toggle("hidden",!c);
    if(!c)return;
    var t=el("div","lp-t");t.appendChild(el("b",null,c.name));
    t.appendChild(document.createTextNode(c.manual?" · lane set by you":" · lane guessed from play rate"));
    box.appendChild(t);
    var row=el("div","lp-b");
    function choose(l){
      pickId=null;renderEnemies(lastEnemies);
      L.api("set_enemy_lane",c.id,l);
    }
    L.LANE_ORDER.forEach(function(l){
      var b=el("button",c.lane===l?"on":null,lane(l));
      var r=c.rates&&c.rates[l];b.appendChild(el("span",null,r!=null?r+"%":""));
      b.title=c.name+" plays "+l+" in "+(r!=null?r+"%":"?")+" of games";
      b.addEventListener("click",function(){choose(l)});
      row.appendChild(b);
    });
    var a=el("button",!c.manual?"on":null,"auto");a.appendChild(el("span",null,"guess"));
    a.title="Go back to the guessed lane";
    a.addEventListener("click",function(){if(c.manual)choose("");else{pickId=null;renderEnemies(lastEnemies)}});
    row.appendChild(a);
    box.appendChild(row);
  }
  function contrib(c){
    var d=el("div","c");
    d.appendChild(el("span","d "+cls(c.weighted),fmt(c.weighted)));
    d.appendChild(el("span",null,c.kind==="vs"?"vs":"w/"));
    d.appendChild(icon(c.name,c.icon));
    d.appendChild(el("span","n",short(c.name)));
    return d;
  }
  function detail(r){
    var box=el("div","detail"),t=document.createElement("table");
    var h=document.createElement("tr");
    ["Kind","Champ","Lane","Δ","Games","Wtd"].forEach(function(x){h.appendChild(el("th",null,x))});
    t.appendChild(h);
    (r.contributions||[]).forEach(function(c){
      var tr=document.createElement("tr");if(c.tentative)tr.className="tent";
      tr.appendChild(el("td",null,c.kind==="vs"?"vs":"w/"));
      var td=el("td","ch");td.appendChild(icon(c.name,c.icon));td.appendChild(el("span","n",c.name));tr.appendChild(td);
      tr.appendChild(el("td",null,lane(c.lane)));
      tr.appendChild(el("td",cls(c.delta),fmt(c.delta)));
      tr.appendChild(el("td",null,c.games!=null?String(c.games):"-"));
      tr.appendChild(el("td",null,fmt(c.weighted)));
      t.appendChild(tr);
    });
    box.appendChild(t);
    var b=el("div","base");b.appendChild(el("span",null,"Base"));b.appendChild(el("span",null,fmt(r.base,2)));
    box.appendChild(b);
    return box;
  }
  function renderLocked(l){
    var b=$("locked");clear(b);b.classList.toggle("hidden",!l);
    if(!l)return;
    b.appendChild(icon(l.name,l.icon));
    b.appendChild(el("span",null,"Locked in: "+l.name));
    b.appendChild(el("span","sub2","picks frozen"));
  }
  function renderRecs(recs,locked){
    var box=$("recs");clear(box);
    (recs||[]).forEach(function(r,i){
      var d=el("div","rec"+(i===0?" top":"")+(openSet[r.name]?" open":"")+(locked&&locked.name===r.name?" mine":""));
      var m=el("div","main");
      m.appendChild(el("div","rank",String(i+1)));
      m.appendChild(icon(r.name,r.icon,"big"));
      var mid=el("div","mid");mid.appendChild(el("div","name",r.name));
      var sub=el("div","sub");
      (r.contributions||[]).slice(0,2).forEach(function(c){sub.appendChild(contrib(c))});
      mid.appendChild(sub);m.appendChild(mid);
      m.appendChild(el("div","score "+cls(r.score),fmt(r.score,2)));
      d.appendChild(m);d.appendChild(detail(r));
      d.addEventListener("click",function(){
        if(openSet[r.name]){delete openSet[r.name];d.classList.remove("open")}
        else{openSet[r.name]=1;d.classList.add("open")}
      });
      box.appendChild(d);
    });
  }

  L.register("picks",{render:function(s){
    chipRow($("allies"),s.allies,"ally");
    renderEnemies(s.enemies);
    clear($("bans"));$("bansBox").classList.toggle("hidden",!(s.bans&&s.bans.length));
    (s.bans||[]).forEach(function(b){var i=icon(b.name,b.icon);i.title=b.name;$("bans").appendChild(i)});
    renderLocked(s.locked);
    var keep=$("recs").scrollTop;renderRecs(s.recs,s.locked);$("recs").scrollTop=keep;
  }});
})(window.LPH);
