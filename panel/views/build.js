/* After lock-in: runes, spells, skills and items, with deltas vs your lane opponents. */
(function(L){
  var $=L.$,el=L.el,lane=L.lane,icon=L.icon,fmt=L.fmt,cls=L.cls,clear=L.clear;
  var SIG=0.3;  /* matchup deltas smaller than this are shown as neutral */

  function k(n){n=Number(n)||0;return n>=1000?(n/1000).toFixed(n>=10000?0:1)+"k":String(n)}
  function vsTip(o){
    var t=el("div","vsd");
    if(!(o.vs&&o.vs.length)){t.appendChild(el("div",null,"No matchup data (too few games)"));return t}
    o.vs.forEach(function(v){
      var r=el("div");r.appendChild(el("span",null,"vs"));r.appendChild(icon(v.name,v.icon));
      r.appendChild(el("span",null,v.name+" ("+lane(v.lane)+")"));
      r.appendChild(el("b",cls(v.delta),fmt(v.delta)));r.appendChild(el("span",null,"· "+k(v.games)+" games"));
      t.appendChild(r);
    });
    return t;
  }
  function mu(o){
    var m=Number(o.matchup)||0;
    if(!(o.vs&&o.vs.length))return el("div","mu","");
    return el("div","mu "+(Math.abs(m)<SIG?"":cls(m)),Math.abs(m)<SIG?"±0":fmt(m));
  }
  function optRow(o,sep){
    var d=el("div","opt"+(o.best?" best":""));
    var its=el("div","its");
    (o.parts||[]).forEach(function(p,i){
      if(i&&sep)its.appendChild(el("span","arrow","›"));
      var ic=icon(p.name,p.icon);ic.title=p.name;its.appendChild(ic);
    });
    d.appendChild(its);
    var st=el("div","st");
    var w=el("span");w.appendChild(el("b",null,o.wr+"%"));w.appendChild(document.createTextNode(" win"));
    st.appendChild(w);  // whole pieces wrap to a second line on wide rows instead of being cut off
    [o.pr+"% picked",k(o.games)].forEach(function(t){
      st.appendChild(document.createTextNode(" "));  // the break point, outside the nowrap pieces
      st.appendChild(el("span",null,"· "+t));
    });
    st.title=(o.parts||[]).map(function(p){return p.name}).join(" › ");
    d.appendChild(st);
    d.appendChild(mu(o));
    if(o.best)d.appendChild(el("span","tag","BEST VS LANE"));
    d.appendChild(vsTip(o));
    return d;
  }
  function runeEl(r,kind){
    var d=el("div","rn"+(kind?" "+kind:""));
    var ic=icon(r.name,r.icon);d.appendChild(ic);
    var m=Number(r.matchup)||0;
    d.title=r.name+(r.wr!=null?" · "+r.wr+"% win · "+r.pr+"% picked":"")+
      ((r.vs||[]).map(function(v){return "\nvs "+v.name+": "+fmt(v.delta)+" ("+v.games+" games)"}).join(""));
    d.appendChild(el("span","dl "+(Math.abs(m)<SIG?"":cls(m)),(r.vs&&r.vs.length&&Math.abs(m)>=SIG)?fmt(m):""));
    return d;
  }
  function page(p){
    var box=el("div","page");
    var l=el("div","pl");l.appendChild(el("b",null,p.label));
    l.appendChild(el("span",null,p.wr+"% · "+k(p.games)+" games"+(p.small?" · small sample":"")));
    box.appendChild(l);
    var r1=el("div","pr");
    (p.primary||[]).forEach(function(r,i){r1.appendChild(runeEl(r,i===0?"key":""))});
    box.appendChild(r1);
    var r2=el("div","pr");
    (p.secondary||[]).forEach(function(r){r2.appendChild(runeEl(r))});
    r2.appendChild(el("div","sep"));
    (p.shards||[]).forEach(function(r){r2.appendChild(runeEl(r,"shard"))});
    box.appendChild(r2);
    if(p.win_shards&&p.win_shards.length){
      var r3=el("div","pr");r3.appendChild(el("span","wsl","Highest win rate shards"));r3.appendChild(el("div","sep"));
      p.win_shards.forEach(function(r){r3.appendChild(runeEl(r,"shard"))});
      box.appendChild(r3);
    }
    return box;
  }
  function section(title,right){
    var s=el("div","bsec"),h=el("h3",null,title);
    if(right)h.appendChild(el("span",null,right));
    s.appendChild(h);return s;
  }

  L.register("build",{render:function(s){
    var head=$("bHead"),body=$("bBody"),keep=body.scrollTop;clear(head);clear(body);
    if(s.champ){head.appendChild(icon(s.champ.name,s.champ.icon,"big"))}
    var t=el("div","bt");
    t.appendChild(el("div","bn",(s.champ?s.champ.name:"")+" build"));
    if(s.games)t.appendChild(el("div","bs",lane(s.lane)+" · "+s.wr+"% win · "+k(s.games)+" games ("+(s.tier||"")+", "+s.days+"d)"));
    var vs=el("div","vsrow");
    if(s.opponents&&s.opponents.length){
      vs.appendChild(el("span",null,"Δ vs"));
      s.opponents.forEach(function(o){var i=icon(o.name,o.icon);i.title=o.name+": "+k(o.games)+" games";vs.appendChild(i);
        vs.appendChild(el("span",null,k(o.games)))});
    }else vs.appendChild(el("span",null,"Δ shows once your lane opponent locks in"));
    t.appendChild(vs);head.appendChild(t);
    if(s.empty){body.appendChild(el("div","bnote",s.empty));return}

    var rs=section("Runes","Δ = edge vs this lane");
    (s.pages||[]).forEach(function(p){rs.appendChild(page(p))});
    if(s.keystones&&s.keystones.length>1){
      var ks=el("div","pr");
      s.keystones.forEach(function(r){var e=runeEl(r,"key");e.title=r.name+" · "+r.pr+"% picked"+e.title.slice(r.name.length);ks.appendChild(e)});
      var kb=el("div","page");kb.appendChild(el("div","pl","Keystones by pick rate"));kb.appendChild(ks);rs.appendChild(kb);
    }
    body.appendChild(rs);

    if(s.spells&&s.spells.length){
      var sp=section("Summoner spells");s.spells.forEach(function(o){sp.appendChild(optRow(o))});body.appendChild(sp);
    }
    if(s.skill_order){
      var sk=section("Skills",(s.skills||[]).map(function(x){return x.key.split("").join(" > ")+" "+x.pr+"%"}).join(" · "));
      var ord=el("div","skillord"),max=s.skills&&s.skills[0]?s.skills[0].key.charAt(0):"";
      s.skill_order.split("").forEach(function(c){ord.appendChild(el("span",c==="R"?"R":c===max?"max":"",c))});
      sk.appendChild(ord);body.appendChild(sk);
    }
    (s.items||[]).forEach(function(sec){
      var b=section(sec.title);
      sec.options.forEach(function(o){b.appendChild(optRow(o,sec.key==="core"||sec.key==="start"))});
      body.appendChild(b);
    });
    body.appendChild(el("div","bnote","Δ compares an option's win rate against the enemies shown above (lane opponents full weight; junglers and roamers less) with its usual win rate, "+
      "shrunk by games (hover for each opponent). Later items show higher win rates because longer games are more often won: "+
      "compare options within a section."));
    body.scrollTop=keep;
  }});
})(window.LPH);
