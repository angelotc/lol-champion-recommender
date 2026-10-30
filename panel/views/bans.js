/* Ban phase: enemy champions worth banning against your likely picks and your team's hovers. */
(function(L){
  var $=L.$,el=L.el,lane=L.lane,icon=L.icon,fmt=L.fmt,cls=L.cls,clear=L.clear,short=L.short;
  var openSet={};

  function chips(box,list,kind){
    clear(box);
    (list||[]).forEach(function(c){
      var ch=el("div","chip "+kind+(c.tentative?" tent":"")+(c.is_me?" me":""));
      ch.title=c.name;ch.appendChild(icon(c.name,c.icon));box.appendChild(ch);
    });
  }
  function line(c){
    var d=el("div","c");
    d.appendChild(el("span","d "+cls(c.delta),fmt(c.delta)));
    d.appendChild(el("span",null,"vs"));
    d.appendChild(icon(c.name,c.icon));
    d.appendChild(el("span","n",short(c.name)));
    return d;
  }
  function detail(t){
    var box=el("div","detail"),tb=document.createElement("table");
    var h=document.createElement("tr");
    ["Your pick","Δ","Games","Wtd"].forEach(function(x){h.appendChild(el("th",null,x))});
    tb.appendChild(h);
    (t.against||[]).forEach(function(c){
      var tr=document.createElement("tr");
      var td=el("td","ch");td.appendChild(icon(c.name,c.icon));td.appendChild(el("span","n",c.name));tr.appendChild(td);
      tr.appendChild(el("td",cls(c.delta),fmt(c.delta)));
      tr.appendChild(el("td",null,String(c.games)));
      tr.appendChild(el("td",null,fmt(c.weighted)));
      tb.appendChild(tr);
    });
    box.appendChild(tb);
    var b=el("div","base");b.appendChild(el("span",null,"Own strength ("+lane(t.lane)+")"));b.appendChild(el("span",null,fmt(t.strength,2)));
    box.appendChild(b);
    box.appendChild(el("div","base","Δ is your pick's win rate change when facing "+t.name+" (negative = it beats you)."));
    return box;
  }

  L.register("bans",{render:function(s){
    chips($("banAllies"),s.allies,"ally");
    chips($("banBans"),s.bans,"bans");
    $("banBansBox").classList.toggle("hidden",!(s.bans&&s.bans.length));
    var vs=$("banPicks");clear(vs);
    if(s.picks&&s.picks.length){
      vs.appendChild(el("span",null,"Measured against your likely picks:"));
      s.picks.forEach(function(p){var i=icon(p.name,p.icon);i.title=p.name;vs.appendChild(i)});
    }
    var team=(s.allies||[]).filter(function(a){return !a.is_me});
    if(team.length){
      vs.appendChild(el("span",null,(s.picks&&s.picks.length?" and":"Measured against")+" your team's hovers:"));
      team.forEach(function(p){var i=icon(p.name,p.icon);i.title=p.name+(p.lane?" ("+p.lane+")":"");vs.appendChild(i)});
    }
    var box=$("threats"),keep=box.scrollTop;clear(box);
    (s.threats||[]).forEach(function(t,i){
      var d=el("div","rec threat"+(i===0?" top":"")+(openSet[t.name]?" open":""));
      var m=el("div","main");
      m.appendChild(el("div","rank",String(i+1)));
      m.appendChild(icon(t.name,t.icon,"big"));
      var mid=el("div","mid");
      var nm=el("div","name",t.name);nm.appendChild(el("span","lanechip",lane(t.lane)));mid.appendChild(nm);
      mid.appendChild(el("div","meta","picked "+t.pick_rate+"% · strength "+fmt(t.strength,1)));
      var sub=el("div","sub");
      (t.against||[]).filter(function(c){return c.delta<0}).slice(0,2).forEach(function(c){sub.appendChild(line(c))});
      mid.appendChild(sub);m.appendChild(mid);
      m.appendChild(el("div","score",(t.score*10).toFixed(1)));
      d.title="Ban value: how likely you face it x how much it hurts your picks";
      d.appendChild(m);d.appendChild(detail(t));
      d.addEventListener("click",function(){
        if(openSet[t.name]){delete openSet[t.name];d.classList.remove("open")}
        else{openSet[t.name]=1;d.classList.add("open")}
      });
      box.appendChild(d);
    });
    box.scrollTop=keep;
  }});
})(window.LPH);
