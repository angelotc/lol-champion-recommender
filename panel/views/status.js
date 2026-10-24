/* Waiting / queue / error messages. */
(function(L){
  L.register("status",{render:function(s){
    var em=L.$("empty");
    em.classList.toggle("err",s.status==="error");
    L.$("msg").textContent=s.message||(s.status==="error"?"Error":"Waiting for champ select...");
  }});
})(window.LPH);
