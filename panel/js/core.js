/* Shared helpers and the view router. Python calls updateState(state); state.view picks the view. */
(function(){
  var $=function(id){return document.getElementById(id)};
  var LANES={top:"top",jungle:"jg",jg:"jg",middle:"mid",mid:"mid",bottom:"bot",bot:"bot",adc:"bot",support:"sup",utility:"sup",sup:"sup"};
  function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
  function lane(l){return l?(LANES[String(l).toLowerCase()]||String(l).slice(0,3)):""}
  function icon(name,url,cls){
    var w=el("span","ico"+(cls?" "+cls:""));
    var fb=el("span","fb",String(name||"?").replace(/[^A-Za-z0-9]/g,"").slice(0,2));
    w.appendChild(fb);
    if(url){var im=document.createElement("img");im.alt="";im.loading="lazy";
      im.onerror=function(){im.remove()};
      im.onload=function(){fb.remove()};
      im.src=url;w.appendChild(im);}
    return w;
  }
  function fmt(v,d){var n=Number(v);if(!isFinite(n))return"-";return(n>0?"+":"")+n.toFixed(d==null?1:d)}
  function cls(v){return Number(v)>=0?"pos":"neg"}
  function clear(n){while(n.firstChild)n.removeChild(n.firstChild)}
  function short(n){n=String(n||"");return n.length>8?n.slice(0,7)+"…":n}
  function cap(s){s=String(s||"");return s.charAt(0).toUpperCase()+s.slice(1)}
  function pad(n){return(n<10?"0":"")+n}
  /* call window.pywebview.api.<name>(...); false when not running inside the app */
  function api(name){
    var args=[].slice.call(arguments,1);
    try{var a=window.pywebview&&window.pywebview.api;if(a&&a[name]){a[name].apply(a,args);return true}}catch(e){}
    return false;
  }

  var views={};
  function register(name,view){views[name]=view}

  var pending=null;
  function renderTiers(s){
    var box=$("tiers");
    $("tiersBox").classList.toggle("hidden",!(s.tiers&&s.tiers.length));
    clear(box);
    (s.tiers||[]).forEach(function(t){
      var b=el("button",(pending||s.tier_id)===t.id?"on":null,t.label);
      b.disabled=!!pending;
      b.addEventListener("click",function(){
        if(pending||t.id===s.tier_id)return;
        pending=t.id;renderTiers(s);
        if(!api("set_tier",t.id)){pending=null;renderTiers(s)}
      });
      box.appendChild(b);
    });
    $("note").textContent=s.note||"";
    $("note").classList.toggle("hidden",!s.note);
  }

  function renderTabs(s){
    var box=$("tabs");clear(box);
    box.classList.toggle("hidden",!(s.tabs&&s.tabs.length));
    (s.tabs||[]).forEach(function(t){
      var b=el("button",t===s.tab?"on":null,t);
      b.addEventListener("click",function(){if(t!==s.tab)api("set_tab",t)});
      box.appendChild(b);
    });
  }

  window.updateState=function(s){
    s=s||{};
    var name=s.view||(s.status==="active"?"picks":"status");
    if(!views[name])name="status";
    var parts=[];
    if(s.role)parts.push(cap(s.role));if(s.days!=null&&s.days!=="")parts.push(s.days+"d");
    $("title").textContent=parts.length?parts.join(" · "):"LoL Companion";
    pending=null;  // any new state answers a tier click (accepted or not)
    renderTiers(s);
    document.querySelectorAll("section.view").forEach(function(v){v.classList.toggle("hidden",v.dataset.view!==name)});
    renderTabs(s);
    views[name].render(s);
    var n=new Date();
    $("foot").innerHTML="Data: lolalytics · last update "+pad(n.getHours())+":"+pad(n.getMinutes())+":"+pad(n.getSeconds())+" · <b>Ctrl+X</b> show/hide";
  };
  window.setScale=function(v){v=parseFloat(v);if(isFinite(v)&&v>0)document.documentElement.style.setProperty("--s",v)};
  window.setMode=function(m){
    document.body.classList.toggle("overlay",m==="overlay");
    document.body.dataset.mode=m||"";
  };
  $("gear").addEventListener("click",function(){api("open_settings")});
  $("hide").addEventListener("click",function(){api("hide")});

  window.LPH={$:$,el:el,lane:lane,icon:icon,fmt:fmt,cls:cls,clear:clear,short:short,cap:cap,api:api,register:register,
    LANE_ORDER:["top","jungle","middle","bottom","support"]};
})();
