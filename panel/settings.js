/* Settings form: Python (settings_ui.SettingsApi) owns the values and validation. */
(function(){
  var $=function(id){return document.getElementById(id)};
  var NUM=["min_pick_rate"];
  var BOOL=["comfort_only"];
  var weight=1;  /* record-and-mastery weight to save when the switch is on (config.toml can tune it) */

  function call(name){
    var a=window.pywebview&&window.pywebview.api, args=[].slice.call(arguments,1);
    return a&&a[name]?a[name].apply(a,args):Promise.reject(new Error("not running in the app"));
  }
  function status(msg,bad){var s=$("status");s.textContent=msg||"";s.className=bad?"bad":""}
  function fill(v){
    NUM.forEach(function(k){$(k).value=v[k]});
    BOOL.forEach(function(k){$(k).checked=!!v[k]});
    $("lockfile").value=v.lockfile;
    var w=Number(v.comfort_weight);
    $("use_comfort").checked=w>0;
    if(w>0)weight=w;
    clearErrors();
  }
  function clearErrors(){
    document.querySelectorAll(".f").forEach(function(f){f.classList.remove("bad");f.querySelector(".err").textContent=""});
  }
  function collect(){
    var v={};
    NUM.forEach(function(k){var t=$(k).value.trim();v[k]=t===""?null:Number(t)});
    BOOL.forEach(function(k){v[k]=$(k).checked});
    v.lockfile=$("lockfile").value;
    v.comfort_weight=$("use_comfort").checked?weight:0;
    return v;
  }
  function showErrors(errs){
    clearErrors();
    var open=false;
    Object.keys(errs).forEach(function(k){
      var f=document.querySelector('.f[data-f="'+k+'"]');
      if(!f)return;
      f.classList.add("bad");f.querySelector(".err").textContent=errs[k];
      if(f.closest("details"))open=true;
    });
    if(open)$("adv").open=true;
  }

  $("save").addEventListener("click",function(){
    status("");
    call("save",collect()).then(function(r){
      if(r.ok){clearErrors();status("Saved. Applied now.")}
      else{showErrors(r.errors);status("Fix the highlighted fields.",true)}
    }).catch(function(e){status("Could not save: "+e.message,true)});
  });
  $("reset").addEventListener("click",function(){
    call("defaults").then(function(d){fill(d);status("Defaults loaded. Save to keep them.")});
  });
  $("cancel").addEventListener("click",function(){call("close")});
  $("openFile").addEventListener("click",function(e){e.preventDefault();call("open_file")});

  function init(){
    call("get").then(function(r){fill(r.values);$("version").textContent=r.version||""}).catch(function(e){status(e.message,true)});
  }
  if(window.pywebview&&window.pywebview.api)init();
  else window.addEventListener("pywebviewready",init);
  window.fillSettings=fill; /* screenshot harness */
})();
