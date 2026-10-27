/* In game (Ctrl+X): the build planned before the game, with what you already own.
   Read-only and click-through: only your own items and gold are used, never the enemies'. */
(function(L){
  var $=L.$,el=L.el,lane=L.lane,icon=L.icon,fmt=L.fmt,cls=L.cls,clear=L.clear;
  var SIG=0.3;

  function k(n){n=Number(n)||0;return n>=1000?(n/1000).toFixed(1)+"k":String(n)}
  function clock(s){s=Number(s)||0;var m=Math.floor(s/60),r=Math.floor(s%60);return m+":"+(r<10?"0":"")+r}
  function itemIcon(p){
    var ic=icon(p.name,p.icon);ic.title=p.name+(p.gold?" · "+p.gold+"g":"");
    if(p.owned)ic.classList.add("own");
    return ic;
  }

  L.register("ingame",{render:function(s){
    var box=$("igBody");clear(box);
    var h=el("div","ig-h");
    if(s.champ)h.appendChild(icon(s.champ.name,s.champ.icon,"igc"));
    var t=el("div","ig-t");
    t.appendChild(el("div","ig-n",(s.champ?s.champ.name:"")+" · lvl "+(s.level||"?")));
    t.appendChild(el("div","ig-s",clock(s.time)+" · "+k(s.gold)+" gold"));
    h.appendChild(t);
    if(s.keystone){var ks=icon(s.keystone.name,s.keystone.icon,"igk");ks.title=s.keystone.name;h.appendChild(ks)}
    box.appendChild(h);
    if(s.empty){box.appendChild(el("div","bnote",s.empty));return}

    if(s.core&&s.core.length){
      var c=el("div","ig-sec");c.appendChild(el("div","ig-l","Core"));
      var row=el("div","ig-core");
      s.core.forEach(function(p,i){if(i)row.appendChild(el("span","arrow","›"));row.appendChild(itemIcon(p))});
      c.appendChild(row);
      if(s.next){
        var nx=el("div","ig-next"),need=Math.max(0,(s.next.gold||0)-(s.gold||0));
        nx.appendChild(el("span",null,"Next: "+s.next.name));
        nx.appendChild(el("b",need?"":"pos",need?k(need)+" more":"affordable"));
        c.appendChild(nx);
      }
      box.appendChild(c);
    }
    (s.sections||[]).forEach(function(sec){
      var b=el("div","ig-sec");b.appendChild(el("div","ig-l",sec.title));
      sec.options.forEach(function(o){
        var r=el("div","ig-opt");
        (o.parts||[]).forEach(function(p){r.appendChild(itemIcon(p))});
        r.appendChild(el("span","ig-wr",o.wr+"% · "+o.pr+"%"));
        var m=Number(o.matchup)||0;
        if((o.vs||[]).length>1||(o.vs||[]).length===1&&Math.abs(m)>=SIG){
          var parts=el("span","ig-vs");
          o.vs.forEach(function(v){parts.appendChild(icon(v.name,v.icon));parts.appendChild(el("b",cls(v.weighted),fmt(v.weighted)))});
          r.appendChild(parts);
        }
        r.appendChild(el("span","ig-mu "+(o.has_vs&&Math.abs(m)>=SIG?cls(m):""),o.has_vs?(Math.abs(m)>=SIG?fmt(m):"±0"):""));
        b.appendChild(r);
      });
      box.appendChild(b);
    });
    if(s.skill_order){
      var sk=el("div","ig-sec");sk.appendChild(el("div","ig-l","Skills"));
      var ord=el("div","skillord");
      s.skill_order.split("").forEach(function(ch,i){
        ord.appendChild(el("span",(ch==="R"?"R":"")+(i+1===s.level?" now":"")+(i+1<s.level?" done":""),ch));
      });
      sk.appendChild(ord);box.appendChild(sk);
    }
    if(s.opponents&&s.opponents.length){
      var v=el("div","vsrow");v.appendChild(el("span",null,"Δ vs"));
      s.opponents.forEach(function(o){var i=icon(o.name,o.icon);i.title=o.name;v.appendChild(i)});
      v.appendChild(el("span",null,"· from before the game"));
      box.appendChild(v);
    }
  }});
})(window.LPH);
